"""LLM helpers for hybrid discovery, annotation, and task generation."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from AgentOccam.adk_discovery import ADKDiscoveryClient
from AgentOccam.discovery.action_summary import summarize_candidate_action
from AgentOccam.discovery.models import (
    ActionCandidate,
    DiscoveryRunConfig,
    ScreenRecord,
)
from AgentOccam.discovery.prompt_templates import (
    get_discovery_role_instruction,
    render_discovery_prompt,
)
from AgentOccam.logger import logger

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
ACTION_VERB_RE = re.compile(r"\b(new|create|add|request|submit|edit|update|view|manage|open)\b", re.IGNORECASE)
FORM_HINT_RE = re.compile(r"\b(new|create|request|submit|form|edit|details|settings|profile|employee|calendar|team)\b", re.IGNORECASE)
TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
RECOVERABLE_ACTION_RE = re.compile(r"(?P<action>click \[\d+\]|go_back)", re.IGNORECASE)
NOVELTY_SCORE_RE = re.compile(r"novelty(?:_score)?\s*[:=]\s*(?P<score>-?\d+(?:\.\d+)?)", re.IGNORECASE)
TEMPORAL_TERM_RE = re.compile(
    r"\b(calendar|month|week|day|date|today|tomorrow|yesterday|schedule)\b",
    re.IGNORECASE,
)
MONTH_NAME_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
WEEKDAY_RE = re.compile(
    r"\b(mon|monday|tue|tues|tuesday|wed|wednesday|thu|thur|thurs|thursday|fri|friday|sat|saturday|sun|sunday)\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"\b\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?\b|\b\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?\b",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b20\d{2}\b")
STANDALONE_NUMBER_RE = re.compile(r"\b\d{1,2}\b")
OBSERVATION_NODE_RE = re.compile(
    r"^\s*\[(?P<element_id>\d+)\]\s+(?P<role>[A-Za-z_]+)(?:\s+['\"](?P<label>[^'\"]*)['\"])?",
)
TEMPORAL_QUERY_KEYS = {
    "date",
    "month",
    "year",
    "week",
    "day",
    "start",
    "end",
    "from",
    "to",
}


def truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def extract_json_payload(text: str) -> Any:
    if not text:
        raise ValueError("Empty LLM response.")

    for snippet in _iter_json_snippets(text):
        parsed = _parse_json_like_snippet(snippet)
        if parsed is not None:
            return parsed

    raise ValueError("Could not parse JSON payload from LLM response.")


def _iter_json_snippets(text: str) -> list[str]:
    stripped = text.strip()
    snippets: list[str] = [stripped]

    fenced = JSON_BLOCK_RE.findall(text)
    snippets.extend(block.strip() for block in fenced if block.strip())

    first_object = stripped.find("{")
    last_object = stripped.rfind("}")
    if first_object != -1 and last_object != -1 and last_object > first_object:
        snippets.append(stripped[first_object : last_object + 1])

    first_array = stripped.find("[")
    last_array = stripped.rfind("]")
    if first_array != -1 and last_array != -1 and last_array > first_array:
        snippets.append(stripped[first_array : last_array + 1])

    unique: list[str] = []
    seen: set[str] = set()
    for snippet in snippets:
        candidate = snippet.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _parse_json_like_snippet(snippet: str) -> Any | None:
    normalized = (
        snippet.strip()
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    attempts = [normalized]
    trimmed_trailing_commas = TRAILING_COMMA_RE.sub(r"\1", normalized)
    if trimmed_trailing_commas != normalized:
        attempts.append(trimmed_trailing_commas)

    for attempt in attempts:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue

    for attempt in attempts:
        try:
            return ast.literal_eval(attempt)
        except Exception:
            continue

    return None


def load_json_if_exists(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    target = Path(path)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None


class DiscoveryLLMCoordinator:
    """Owns LLM-backed action ranking and screen annotation with safe fallbacks."""

    def __init__(
        self,
        config: DiscoveryRunConfig,
        adk_client: ADKDiscoveryClient | None = None,
    ) -> None:
        self.config = config
        self.adk_client = adk_client
        self._ranking_call = None
        self._annotation_call = None
        self._task_call = None
        self._revisit_call = None
        self._decision_call = None
        self._build_call_model = None
        self._availability_error = ""

        if not config.llm.enabled:
            return

        try:
            from AgentOccam.model_registry import build_call_model
        except Exception as exc:
            self._availability_error = str(exc)
            return

        self._build_call_model = build_call_model
        try:
            self._initialize_role_calls()
        except Exception as exc:
            self._availability_error = str(exc)
            self._ranking_call = None
            self._annotation_call = None
            self._task_call = None
            self._revisit_call = None
            self._decision_call = None

    def _initialize_role_calls(self) -> None:
        if self.config.llm.use_for_action_ranking:
            self._ranking_call = self._build_role_call(
                role="action_ranking",
                model_id=self.config.llm.exploration_model,
            )
        if self.config.llm.use_for_next_action_decision:
            self._decision_call = self._build_role_call(
                role="next_action",
                model_id=self.config.llm.exploration_model,
            )
        if self.config.llm.use_for_screen_annotation:
            self._annotation_call = self._build_role_call(
                role="screen_annotation",
                model_id=self.config.llm.resolved_annotation_model(),
            )
        if self.config.llm.use_for_page_revisit_check:
            self._revisit_call = self._build_role_call(
                role="page_revisit",
                model_id=self.config.llm.resolved_annotation_model(),
            )
        if self.config.llm.use_for_task_generation:
            self._task_call = self._build_role_call(
                role="task_generation",
                model_id=(
                    self.config.task_generation.llm_model
                    or self.config.llm.resolved_task_generation_model()
                ),
            )

    def _build_role_call(self, role: str, model_id: str):
        system_prompt = get_discovery_role_instruction(role)
        if self._should_use_sessioned_adk(role):
            return self._build_adk_role_call(
                role=role,
                model_id=model_id,
                system_prompt=system_prompt,
            )
        return self._build_direct_role_call(
            model_id=model_id,
            system_prompt=system_prompt,
        )

    def _should_use_sessioned_adk(self, role: str) -> bool:
        sessioned_roles = {
            str(configured_role).strip()
            for configured_role in (self.config.adk.sessioned_llm_roles or [])
            if str(configured_role).strip()
        }
        return bool(
            self.adk_client is not None
            and self.adk_client.use_for_llm_calls
            and role in sessioned_roles
        )

    def _build_direct_role_call(self, model_id: str, system_prompt: str):
        if self._build_call_model is None:
            raise RuntimeError("Direct model registry is not configured.")
        call_model = self._build_call_model(model_id, system_prompt=system_prompt)

        def _call(*, prompt: str) -> str:
            return call_model(prompt=prompt)

        _call._agentoccam_provider = "direct"  # type: ignore[attr-defined]
        _call._agentoccam_system_prompt = system_prompt  # type: ignore[attr-defined]
        return _call

    def _build_adk_role_call(self, role: str, model_id: str, system_prompt: str):
        def _call(*, prompt: str) -> str:
            assert self.adk_client is not None
            return self.adk_client.call_role(
                role=role,
                prompt=prompt,
                model_id=model_id,
                system_prompt=system_prompt,
            )

        _call._agentoccam_provider = "adk"  # type: ignore[attr-defined]
        _call._agentoccam_system_prompt = system_prompt  # type: ignore[attr-defined]
        return _call

    def _invoke_role_call(
        self,
        *,
        role: str,
        model_id: str,
        prompt: str,
        call_model,
        system_prompt: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        provider = getattr(call_model, "_agentoccam_provider", "direct")
        logged_system_prompt = getattr(
            call_model,
            "_agentoccam_system_prompt",
            system_prompt,
        )
        if self.adk_client is not None:
            prompt_path = self.adk_client.log_role_prompt(
                role=role,
                model_id=model_id,
                prompt=prompt,
                system_prompt=logged_system_prompt,
                metadata=metadata,
                provider=provider,
            )
            if prompt_path is not None:
                logger.debug(
                    "Logged discovery prompt: role=%s model=%s provider=%s path=%s",
                    role,
                    model_id,
                    provider,
                    prompt_path,
                )
        return call_model(prompt=prompt)

    @property
    def is_available(self) -> bool:
        return any(
            call is not None
            for call in (
                self._ranking_call,
                self._annotation_call,
                self._task_call,
                self._revisit_call,
                self._decision_call,
            )
        )

    @property
    def availability_error(self) -> str:
        return self._availability_error

    def annotate_screen(
        self,
        screen: ScreenRecord,
        candidate_actions: list[ActionCandidate],
        path_actions: list[str],
        discovered_screens: list[ScreenRecord],
        memory_summary: str = "",
    ) -> ScreenRecord:
        if self._annotation_call is None:
            return self._heuristic_annotate_screen(screen, candidate_actions)

        augmented_memory = self._merge_memory_context(memory_summary, screen)
        visited_page_descriptors = [
            f"{item.title or item.url} ({item.page_type or 'unknown'})"
            for item in discovered_screens[:12]
        ]
        prompt = render_discovery_prompt(
            "screen_annotation",
            sut_name=self.config.sut.name,
            current_url=screen.url,
            page_title=screen.title,
            path_actions=path_actions,
            visited_page_descriptors=visited_page_descriptors,
            exploration_memory=augmented_memory or "- None",
            candidate_actions=self._format_candidates(candidate_actions, max_items=18),
            detected_form_fields=self._format_form_fields(screen),
            accessibility_tree_excerpt=truncate_text(
                screen.observation_text,
                self.config.llm.annotation_observation_chars,
            ),
        )
        try:
            response = self._invoke_role_call(
                role="screen_annotation",
                model_id=self.config.llm.resolved_annotation_model(),
                prompt=prompt,
                call_model=self._annotation_call,
                system_prompt=(
                    get_discovery_role_instruction("screen_annotation")
                    if self.adk_client is not None and self.adk_client.use_for_llm_calls
                    else ""
                ),
                metadata={
                    "screen_id": screen.screen_id,
                    "url": screen.url,
                    "path_actions": path_actions,
                },
            )
            payload = extract_json_payload(response)
            screen.page_type = str(payload.get("page_type", "")).strip()
            screen.summary = str(payload.get("summary", "")).strip()
            screen.key_entities = self._normalize_str_list(payload.get("key_entities"))
            screen.domain_objects = self._normalize_str_list(payload.get("domain_objects"))
            screen.forms_detected = self._normalize_str_list(payload.get("forms_detected"))
            screen.task_opportunities = self._normalize_str_list(payload.get("task_opportunities"))[
                : self.config.llm.max_task_ideas_per_screen
            ]
            screen.important_dom = self._normalize_str_list(payload.get("important_dom"))[:8]
            screen.secondary_dom = self._normalize_str_list(payload.get("secondary_dom"))[:8]
            screen.write_risk = str(payload.get("write_risk", "unknown")).strip().lower() or "unknown"
        except Exception as exc:
            screen.metadata["annotation_error"] = str(exc)
            logger.warning(
                "Screen annotation fallback to heuristic for url=%s error=%s",
                screen.url,
                exc,
            )
            return self._heuristic_annotate_screen(screen, candidate_actions)

        if not screen.summary:
            screen = self._heuristic_annotate_screen(screen, candidate_actions)
        return screen

    def rank_action_candidates(
        self,
        screen: ScreenRecord,
        candidate_actions: list[ActionCandidate],
        path_actions: list[str],
        discovered_screens: list[ScreenRecord],
        memory_summary: str = "",
    ) -> list[ActionCandidate]:
        limit = max(1, min(self.config.llm.selected_actions_per_screen, len(candidate_actions)))
        if not candidate_actions:
            return []
        if self._ranking_call is None:
            return self._heuristic_rank_actions(candidate_actions, limit=limit)

        augmented_memory = self._merge_memory_context(memory_summary, screen)
        discovered_descriptors = [
            f"{item.title or item.url} ({item.page_type or 'unknown'})"
            for item in discovered_screens[:18]
        ]
        prompt = render_discovery_prompt(
            "action_ranking",
            limit=limit,
            sut_name=self.config.sut.name,
            current_url=screen.url,
            page_title=screen.title,
            page_type=screen.page_type or "unknown",
            path_actions=path_actions,
            discovered_descriptors=discovered_descriptors,
            exploration_memory=augmented_memory or "- None",
            candidate_actions=self._format_candidates(candidate_actions, max_items=32),
            detected_form_fields=self._format_form_fields(screen),
            accessibility_tree_excerpt=truncate_text(
                screen.observation_text,
                self.config.llm.ranking_observation_chars,
            ),
        )
        try:
            response = self._invoke_role_call(
                role="action_ranking",
                model_id=self.config.llm.exploration_model,
                prompt=prompt,
                call_model=self._ranking_call,
                system_prompt=(
                    get_discovery_role_instruction("action_ranking")
                    if self.adk_client is not None and self.adk_client.use_for_llm_calls
                    else ""
                ),
                metadata={
                    "screen_id": screen.screen_id,
                    "url": screen.url,
                    "path_actions": path_actions,
                },
            )
            payload = extract_json_payload(response)
            ranked = self._materialize_ranked_actions(
                payload=payload,
                candidate_actions=candidate_actions,
                limit=limit,
                selected_by="llm",
            )
            if ranked:
                return ranked
        except Exception as exc:
            screen.metadata["ranking_error"] = str(exc)
            screen.metadata["ranking_response_excerpt"] = truncate_text(response, 2000) if "response" in locals() else ""
            recovered = self._recover_ranked_actions_from_text(
                response if "response" in locals() else "",
                candidate_actions=candidate_actions,
                limit=limit,
            )
            if recovered:
                screen.metadata["ranking_error"] = (
                    f"{exc} (recovered {len(recovered)} action(s) from non-JSON response)"
                )
                logger.warning(
                    "Action ranking recovered from non-JSON response for url=%s recovered=%s error=%s",
                    screen.url,
                    len(recovered),
                    exc,
                )
                return recovered
            logger.warning(
                "Action ranking fallback to heuristic for url=%s error=%s",
                screen.url,
                exc,
            )

        return self._heuristic_rank_actions(candidate_actions, limit=limit)

    def can_generate_tasks(self) -> bool:
        return self._task_call is not None

    def task_call(self, prompt: str) -> str:
        if self._task_call is None:
            raise RuntimeError("Task-generation LLM is not configured.")
        model_id = self.config.task_generation.llm_model or self.config.llm.resolved_task_generation_model()
        return self._invoke_role_call(
            role="task_generation",
            model_id=model_id,
            prompt=prompt,
            call_model=self._task_call,
            system_prompt=(
                get_discovery_role_instruction("task_generation")
                if self.adk_client is not None and self.adk_client.use_for_llm_calls
                else ""
            ),
        )

    def judge_screen_revisit(
        self,
        current_screen: ScreenRecord,
        candidate_screens: list[ScreenRecord],
        memory_summary: str = "",
    ) -> dict[str, Any]:
        if not candidate_screens:
            return {
                "is_revisit": False,
                "matched_screen_id": "",
                "reason": "No prior candidates to compare against.",
                "confidence": 0.0,
                "difference_type": "distinct_page",
            }

        heuristic_result = self._heuristic_screen_revisit(current_screen, candidate_screens)
        if heuristic_result.get("is_revisit") and float(heuristic_result.get("confidence", 0.0)) >= 0.85:
            return heuristic_result

        if self._revisit_call is None:
            return heuristic_result

        augmented_memory = self._merge_memory_context(memory_summary, current_screen)
        prompt = render_discovery_prompt(
            "page_revisit",
            current_url=current_screen.url,
            current_title=current_screen.title,
            current_form_fields=self._format_form_fields(current_screen),
            accessibility_tree_excerpt=truncate_text(
                current_screen.observation_text,
                self.config.llm.revisit_observation_chars,
            ),
            exploration_memory=augmented_memory or "- None",
            candidate_pages=self._format_screen_candidates(candidate_screens),
        )
        try:
            response = self._invoke_role_call(
                role="page_revisit",
                model_id=self.config.llm.resolved_annotation_model(),
                prompt=prompt,
                call_model=self._revisit_call,
                system_prompt=(
                    get_discovery_role_instruction("page_revisit")
                    if self.adk_client is not None and self.adk_client.use_for_llm_calls
                    else ""
                ),
                metadata={
                    "screen_id": current_screen.screen_id,
                    "url": current_screen.url,
                    "candidate_screen_ids": [item.screen_id for item in candidate_screens],
                },
            )
            payload = extract_json_payload(response)
            matched_screen_id = str(payload.get("matched_screen_id", "")).strip()
            is_revisit = bool(payload.get("is_revisit", False))
            if is_revisit and any(item.screen_id == matched_screen_id for item in candidate_screens):
                return {
                    "is_revisit": True,
                    "matched_screen_id": matched_screen_id,
                    "reason": str(payload.get("reason", "")).strip(),
                    "confidence": self._coerce_float(payload.get("confidence", 0.0)),
                    "difference_type": str(payload.get("difference_type", "same_page_variant")).strip() or "same_page_variant",
                }
        except Exception as exc:
            logger.warning(
                "Screen revisit check fallback to heuristic for url=%s error=%s",
                current_screen.url,
                exc,
            )

        return heuristic_result

    def decide_next_action(
        self,
        screen: ScreenRecord,
        candidate_actions: list[ActionCandidate],
        memory_summary: str,
        elapsed_minutes: float,
        remaining_minutes: float,
    ) -> dict[str, str]:
        if self._decision_call is None or not self.config.llm.use_for_next_action_decision:
            return self._heuristic_next_action(candidate_actions)

        augmented_memory = self._merge_memory_context(memory_summary, screen)
        prompt = render_discovery_prompt(
            "next_action",
            elapsed_minutes=f"{elapsed_minutes:.2f}",
            remaining_minutes=f"{remaining_minutes:.2f}",
            current_url=screen.url,
            current_title=screen.title,
            current_page_type=screen.page_type or "unknown",
            current_page_summary=screen.summary or "None",
            current_task_opportunities=screen.task_opportunities,
            exploration_memory=augmented_memory or "- None",
            candidate_actions=self._format_candidates(candidate_actions, max_items=40),
            detected_form_fields=self._format_form_fields(screen),
            accessibility_tree_excerpt=truncate_text(
                screen.observation_text,
                self.config.llm.decision_observation_chars,
            ),
        )
        try:
            response = self._invoke_role_call(
                role="next_action",
                model_id=self.config.llm.exploration_model,
                prompt=prompt,
                call_model=self._decision_call,
                system_prompt=(
                    get_discovery_role_instruction("next_action")
                    if self.adk_client is not None and self.adk_client.use_for_llm_calls
                    else ""
                ),
                metadata={
                    "screen_id": screen.screen_id,
                    "url": screen.url,
                    "elapsed_minutes": round(elapsed_minutes, 3),
                    "remaining_minutes": round(remaining_minutes, 3),
                },
            )
            payload = extract_json_payload(response)
            action = str(payload.get("action", "")).strip()
            reason = str(payload.get("reason", "")).strip()
            mark_screen_done = bool(payload.get("mark_screen_done", False))
            return {
                "action": action,
                "reason": reason,
                "mark_screen_done": "true" if mark_screen_done else "false",
            }
        except Exception:
            logger.warning(
                "Next-action decision fallback to heuristic for screen=%s url=%s",
                screen.screen_id,
                screen.url,
            )
            return self._heuristic_next_action(candidate_actions)

    def _merge_memory_context(
        self,
        memory_summary: str,
        current_screen: ScreenRecord,
    ) -> str:
        if self.adk_client is not None and self.adk_client.use_for_llm_calls:
            return (
                "ADK session snapshot:\n"
                + self.adk_client.build_prompt_state_block(current_screen=current_screen)
            ).strip()

        summary = memory_summary.strip()
        if not summary:
            return ""

        max_chars = 4000
        if self.adk_client is not None:
            configured = int(self.adk_client.config.adk.prompt_state_max_chars)
            if configured > 0:
                max_chars = configured
        return truncate_text(summary, max_chars)

    def _materialize_ranked_actions(
        self,
        payload: Any,
        candidate_actions: list[ActionCandidate],
        limit: int,
        selected_by: str,
    ) -> list[ActionCandidate]:
        if not isinstance(payload, dict):
            return []
        selected_payload = payload.get("selected_actions", [])
        if not isinstance(selected_payload, list):
            return []
        selected_by_action = {
            item.action: item for item in candidate_actions
        }
        ranked: list[ActionCandidate] = []
        for raw in selected_payload:
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action", "")).strip()
            if action not in selected_by_action:
                continue
            base = selected_by_action[action]
            ranked.append(
                ActionCandidate(
                    action=base.action,
                    element_id=base.element_id,
                    role=base.role,
                    label=base.label,
                    raw_text=base.raw_text,
                    source=base.source,
                    zone=base.zone,
                    signature=base.signature,
                    is_shared_chrome=base.is_shared_chrome,
                    seen_count=base.seen_count,
                    reason=str(raw.get("reason", "")).strip(),
                    novelty_score=self._coerce_float(raw.get("novelty_score", 0.0)),
                    selected_by=selected_by,
                )
            )
            if len(ranked) >= limit:
                break
        return ranked

    def _recover_ranked_actions_from_text(
        self,
        response_text: str,
        candidate_actions: list[ActionCandidate],
        limit: int,
    ) -> list[ActionCandidate]:
        if not response_text.strip():
            return []
        selected_by_action = {
            item.action: item for item in candidate_actions
        }
        recovered: list[ActionCandidate] = []
        seen_actions: set[str] = set()
        for raw_line in response_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = RECOVERABLE_ACTION_RE.search(line)
            if match is None:
                continue
            action = match.group("action").strip()
            if action not in selected_by_action or action in seen_actions:
                continue
            base = selected_by_action[action]
            novelty_match = NOVELTY_SCORE_RE.search(line)
            novelty = (
                self._coerce_float(novelty_match.group("score"))
                if novelty_match is not None
                else (base.novelty_score or 0.5)
            )
            reason = line.replace(action, "", 1).strip(" -:\t")
            reason = NOVELTY_SCORE_RE.sub("", reason).strip(" -:\t,")
            reason = re.sub(r"^\d+[.)]?\s*", "", reason).strip(" -:\t,")
            recovered.append(
                ActionCandidate(
                    action=base.action,
                    element_id=base.element_id,
                    role=base.role,
                    label=base.label,
                    raw_text=base.raw_text,
                    source=base.source,
                    zone=base.zone,
                    signature=base.signature,
                    is_shared_chrome=base.is_shared_chrome,
                    seen_count=base.seen_count,
                    reason=reason or "Recovered from non-JSON ranking response.",
                    novelty_score=novelty,
                    selected_by="llm_recovered",
                )
            )
            seen_actions.add(action)
            if len(recovered) >= limit:
                break
        return recovered

    def _heuristic_annotate_screen(
        self,
        screen: ScreenRecord,
        candidate_actions: list[ActionCandidate],
    ) -> ScreenRecord:
        labels = [item.label.strip() for item in candidate_actions if item.label.strip()]
        form_labels = [field.label.strip() for field in screen.form_fields if field.label.strip()]
        parsed = urlparse(screen.url)
        inferred_type = self._infer_page_type(screen, labels)
        summary_parts = []
        if screen.title:
            summary_parts.append(f'The page title is "{screen.title}".')
        if parsed.path and parsed.path != "/":
            summary_parts.append(f'It is located at the path "{parsed.path}".')
        if labels:
            summary_parts.append(
                "Visible high-value actions include "
                + ", ".join(f'"{label}"' for label in labels[:4])
                + "."
            )
        if screen.form_fields:
            summary_parts.append(
                "Form fields include "
                + ", ".join(
                    f'"{field.label or field.field_type}"'
                    for field in screen.form_fields[:6]
                )
                + "."
            )
        screen.page_type = inferred_type
        screen.summary = " ".join(summary_parts) or f"Discovered screen at {screen.url}."
        screen.key_entities = self._unique_non_empty([screen.title, parsed.path.rsplit("/", 1)[-1], *form_labels[:4]])
        screen.domain_objects = self._infer_domain_objects(screen, labels + form_labels)
        screen.forms_detected = self._infer_forms_from_labels(labels + form_labels)
        screen.task_opportunities = self._heuristic_task_opportunities(screen, labels + form_labels)
        screen.important_dom = self._summarize_important_dom(screen, candidate_actions)
        screen.secondary_dom = self._summarize_secondary_dom(screen, candidate_actions)
        screen.write_risk = self._infer_write_risk(labels, screen)
        return screen

    def _heuristic_rank_actions(
        self,
        candidate_actions: list[ActionCandidate],
        limit: int,
    ) -> list[ActionCandidate]:
        def score(item: ActionCandidate) -> tuple[int, int, int, int, str]:
            label = item.label.lower()
            boost = 0
            if ACTION_VERB_RE.search(label):
                boost += 3
            if FORM_HINT_RE.search(label):
                boost += 2
            if any(token in label for token in ("more", "details", "view", "employee", "team", "calendar", "new")):
                boost += 1
            novelty = int((item.novelty_score or 0.5) * 10)
            shared_penalty = 1 if item.is_shared_chrome else 0
            seen_penalty = item.seen_count
            return (novelty + boost, -shared_penalty, -seen_penalty, -len(label), item.action)

        ranked = sorted(candidate_actions, key=score, reverse=True)
        return [
            ActionCandidate(
                action=item.action,
                element_id=item.element_id,
                role=item.role,
                label=item.label,
                raw_text=item.raw_text,
                source=item.source,
                zone=item.zone,
                signature=item.signature,
                is_shared_chrome=item.is_shared_chrome,
                seen_count=item.seen_count,
                reason=item.reason or "Heuristic ranking prioritized broader workflow coverage.",
                novelty_score=item.novelty_score or 0.5,
                selected_by=item.selected_by or "policy",
            )
            for item in ranked[:limit]
        ]

    def _heuristic_next_action(self, candidate_actions: list[ActionCandidate]) -> dict[str, str]:
        if not candidate_actions:
            return {"action": "stop", "reason": "No candidate actions remain.", "mark_screen_done": "true"}
        best = self._heuristic_rank_actions(candidate_actions, limit=1)[0]
        return {
            "action": best.action,
            "reason": best.reason or "Heuristic chose the highest-value remaining action.",
            "mark_screen_done": "false",
        }

    def _infer_page_type(self, screen: ScreenRecord, labels: list[str]) -> str:
        lower_title = screen.title.lower()
        lower_url = screen.url.lower()
        joined = " ".join(labels).lower()
        if any(token in lower_title or token in lower_url or token in joined for token in ("new", "create", "request", "form")):
            return "form"
        if any(token in lower_title or token in lower_url or token in joined for token in ("employee", "team", "list", "directory")):
            return "list"
        if any(token in lower_title or token in lower_url or token in joined for token in ("detail", "profile", "settings")):
            return "detail"
        if any(token in lower_title or token in lower_url or token in joined for token in ("calendar", "dashboard", "overview")):
            return "overview"
        return "page"

    def _infer_forms_from_labels(self, labels: list[str]) -> list[str]:
        forms = []
        for label in labels:
            if FORM_HINT_RE.search(label):
                forms.append(label)
        return self._unique_non_empty(forms[:4])

    def _infer_domain_objects(self, screen: ScreenRecord, labels: list[str]) -> list[str]:
        text = " ".join(
            [
                screen.title,
                screen.page_type,
                screen.summary,
                screen.url,
                *labels,
            ]
        ).lower()
        known_objects = [
            "absence",
            "leave request",
            "employee",
            "staff",
            "department",
            "calendar",
            "team",
            "company settings",
            "report",
            "feed",
            "api",
            "profile",
            "integration",
            "holiday",
        ]
        matches = [item for item in known_objects if item in text]
        if not matches and screen.page_type:
            matches = [screen.page_type]
        return self._unique_non_empty(matches[:5])

    def _heuristic_task_opportunities(self, screen: ScreenRecord, labels: list[str]) -> list[str]:
        opportunities: list[str] = []
        lower_labels = [label.lower() for label in labels]
        if any("new absence" in label for label in lower_labels):
            opportunities.append("Submit a new absence request")
        if any("add new employee" in label or "add single employee" in label for label in lower_labels):
            opportunities.append("Create a new employee account")
        if any("import employees" in label for label in lower_labels):
            opportunities.append("Import employees from a file")
        if any("employee" in label for label in lower_labels):
            opportunities.append("Navigate to the employee management area")
            opportunities.append("View details of existing employees in the directory")
        if any("team" in label for label in lower_labels):
            opportunities.append("Review the team view calendar")
        if any("department" in label for label in lower_labels):
            opportunities.append("Review or update department settings")
        if any("report" in label for label in lower_labels):
            opportunities.append("Open the reports area")
        if any("settings" in label or "profile" in label for label in lower_labels):
            opportunities.append("Review or update account settings")
        if not opportunities:
            target = screen.title or screen.url
            opportunities.append(f"Navigate to and review the {target} page")
        return self._unique_non_empty(opportunities[: self.config.llm.max_task_ideas_per_screen])

    def _infer_write_risk(self, labels: list[str], screen: ScreenRecord) -> str:
        text = (
            " ".join(labels).lower()
            + " "
            + " ".join(field.label for field in screen.form_fields).lower()
            + " "
            + screen.url.lower()
            + " "
            + screen.title.lower()
        )
        if any(token in text for token in ("new", "create", "submit", "request", "edit")):
            return "medium"
        if any(token in text for token in ("delete", "cancel", "revoke", "approve", "reject")):
            return "high"
        return "low"

    def _summarize_important_dom(
        self,
        screen: ScreenRecord,
        candidate_actions: list[ActionCandidate],
    ) -> list[str]:
        important: list[str] = []
        for field in screen.form_fields[:8]:
            label = field.label or field.field_type
            required = "required" if field.required else "optional"
            important.append(f'{field.field_type} "{label}" ({required})')
        for candidate in candidate_actions[:8]:
            label = self._candidate_label(candidate)
            if not label:
                continue
            important.append(f'{candidate.role or "action"} "{label}"')
        return self._unique_non_empty(important[:10])

    def _summarize_secondary_dom(
        self,
        screen: ScreenRecord,
        candidate_actions: list[ActionCandidate],
    ) -> list[str]:
        important = set(self._summarize_important_dom(screen, candidate_actions))
        secondary: list[str] = []
        for candidate in candidate_actions[4:16]:
            label = self._candidate_label(candidate)
            if not label:
                continue
            descriptor = f'{candidate.role or "action"} "{label}"'
            if descriptor in important:
                continue
            secondary.append(descriptor)
        return self._unique_non_empty(secondary[:8])

    def _candidate_label(self, candidate: ActionCandidate) -> str:
        label = (candidate.label or candidate.raw_text).strip()
        if label:
            return label
        return summarize_candidate_action(candidate).strip()

    def _format_candidates(
        self,
        candidates: list[ActionCandidate],
        max_items: int,
    ) -> str:
        lines = []
        for item in candidates[:max_items]:
            label = item.label or item.raw_text or item.action
            shared = "shared" if item.is_shared_chrome else "page"
            seen = f"seen={item.seen_count}" if item.seen_count else "seen=0"
            extra_reason = f' | note="{item.reason}"' if item.reason else ""
            lines.append(
                f'- {item.action} | role={item.role or "unknown"} | zone={item.zone or "main"} | {shared} | {seen} | novelty={item.novelty_score:.2f} | label="{label}"{extra_reason}'
            )
        return "\n".join(lines) if lines else "- No candidates"

    def _format_form_fields(self, screen: ScreenRecord) -> str:
        if not screen.form_fields:
            return "- None"
        lines = []
        for field in screen.form_fields[:20]:
            required = "required" if field.required else "optional"
            label = field.label or "(unlabeled)"
            lines.append(f'- {field.field_type} "{label}" ({required})')
        return "\n".join(lines)

    def _format_screen_candidates(self, screens: list[ScreenRecord]) -> str:
        lines: list[str] = []
        for screen in screens[: self.config.llm.revisit_candidate_limit]:
            important_dom = screen.important_dom[:4]
            lines.append(
                "\n".join(
                    [
                        f'- screen_id: {screen.screen_id}',
                        f'  url: {screen.url}',
                        f'  title: {screen.title}',
                        f'  page_type: {screen.page_type or "unknown"}',
                        f'  summary: {screen.summary or "None"}',
                        f'  form_fields: {[{"label": field.label, "type": field.field_type} for field in screen.form_fields[:8]]}',
                        f'  important_dom: {important_dom}',
                        "  accessibility_tree_excerpt:",
                        "  " + truncate_text(screen.observation_text, max(1200, self.config.llm.revisit_observation_chars // 3 or 1200)).replace("\n", "\n  "),
                    ]
                )
            )
        return "\n".join(lines) if lines else "- None"

    def _heuristic_screen_revisit(
        self,
        current_screen: ScreenRecord,
        candidate_screens: list[ScreenRecord],
    ) -> dict[str, Any]:
        current_profile = self._screen_revisit_profile(current_screen)
        best_screen = None
        best_score = -1
        best_reason = "Heuristic did not find a strong enough existing page match."
        best_confidence = 0.0
        for candidate in candidate_screens:
            candidate_profile = self._screen_revisit_profile(candidate)
            score = 0
            reasons: list[str] = []
            same_route = current_profile["route"] == candidate_profile["route"]
            same_query_template = current_profile["query_template"] == candidate_profile["query_template"]
            if same_route:
                score += 5
                reasons.append("same route")
            if same_route and same_query_template:
                score += 2
                reasons.append("same non-temporal query state")
            if (
                current_profile["normalized_title"]
                and current_profile["normalized_title"] == candidate_profile["normalized_title"]
            ):
                score += 3
                reasons.append("normalized title match")
            if (
                current_profile["form_signature"]
                and current_profile["form_signature"] == candidate_profile["form_signature"]
            ):
                score += 4
                reasons.append("same form signature")

            action_overlap = self._jaccard_similarity(
                current_profile["action_templates"],
                candidate_profile["action_templates"],
            )
            if action_overlap >= 0.75:
                score += 5
                reasons.append("very high action-template overlap")
            elif action_overlap >= 0.55:
                score += 4
                reasons.append("high action-template overlap")
            elif action_overlap >= 0.35:
                score += 2
                reasons.append("moderate action-template overlap")

            structure_overlap = self._jaccard_similarity(
                current_profile["structure_tokens"],
                candidate_profile["structure_tokens"],
            )
            if structure_overlap >= 0.75:
                score += 4
                reasons.append("very high structural overlap")
            elif structure_overlap >= 0.55:
                score += 3
                reasons.append("high structural overlap")
            elif structure_overlap >= 0.35:
                score += 2
                reasons.append("moderate structural overlap")

            temporal_variant = (
                same_route
                and same_query_template
                and current_profile["temporal_like"]
                and candidate_profile["temporal_like"]
            )
            if temporal_variant:
                score += 2
                reasons.append("temporal page family")
                if action_overlap >= 0.45:
                    score += 2
                    reasons.append("temporal navigation pattern overlap")

            confidence = min(1.0, score / 18.0)
            if score > best_score:
                best_score = score
                best_screen = candidate
                best_confidence = confidence
                best_reason = "; ".join(reasons) or best_reason

        if best_screen is not None and self._is_revisit_score_sufficient(
            score=best_score,
            current_profile=current_profile,
            candidate_profile=self._screen_revisit_profile(best_screen),
        ):
            return {
                "is_revisit": True,
                "matched_screen_id": best_screen.screen_id,
                "reason": best_reason,
                "confidence": best_confidence,
                "difference_type": "same_page_variant",
            }
        return {
            "is_revisit": False,
            "matched_screen_id": "",
            "reason": best_reason,
            "confidence": 0.0,
            "difference_type": "distinct_page",
        }

    def _screen_tokens(self, screen: ScreenRecord) -> set[str]:
        values = [
            screen.title,
            screen.page_type,
            screen.summary,
            self._normalized_route(screen.url),
            *(field.label or field.field_type for field in screen.form_fields[:6]),
            *screen.important_dom[:6],
        ]
        tokens: set[str] = set()
        for value in values:
            for token in re.split(r"[^a-z0-9]+", str(value or "").lower()):
                if len(token) >= 3:
                    tokens.add(token)
        return tokens

    def _screen_revisit_profile(self, screen: ScreenRecord) -> dict[str, Any]:
        candidates = screen.action_candidates or screen.recommended_actions
        return {
            "route": self._normalized_route(screen.url),
            "query_template": self._normalized_query_template(screen.url),
            "normalized_title": self._normalize_temporal_text(screen.title),
            "form_signature": self._form_signature(screen),
            "action_templates": self._action_template_set(candidates),
            "structure_tokens": self._screen_structure_tokens(screen),
            "temporal_like": self._screen_looks_temporal(screen, candidates),
        }

    def _is_revisit_score_sufficient(
        self,
        *,
        score: int,
        current_profile: dict[str, Any],
        candidate_profile: dict[str, Any],
    ) -> bool:
        action_overlap = self._jaccard_similarity(
            current_profile["action_templates"],
            candidate_profile["action_templates"],
        )
        structure_overlap = self._jaccard_similarity(
            current_profile["structure_tokens"],
            candidate_profile["structure_tokens"],
        )
        same_route = current_profile["route"] == candidate_profile["route"]
        same_query_template = current_profile["query_template"] == candidate_profile["query_template"]
        temporal_variant = (
            same_route
            and same_query_template
            and current_profile["temporal_like"]
            and candidate_profile["temporal_like"]
        )
        if same_query_template and score >= 10:
            return True
        if same_route and same_query_template and action_overlap >= 0.6 and structure_overlap >= 0.35:
            return True
        if temporal_variant and action_overlap >= 0.45 and structure_overlap >= 0.2:
            return True
        if (
            same_route
            and not same_query_template
            and current_profile["normalized_title"]
            and current_profile["normalized_title"] == candidate_profile["normalized_title"]
            and score >= 14
            and action_overlap >= 0.75
            and structure_overlap >= 0.75
        ):
            return True
        return False

    def _action_template_set(self, candidates: list[ActionCandidate]) -> set[str]:
        templates: set[str] = set()
        for candidate in candidates[:24]:
            if candidate.signature and candidate.signature.startswith("temporal:"):
                templates.add(candidate.signature)
                continue
            label = self._normalize_temporal_text(candidate.label or candidate.raw_text)
            if not label or label in {"<day>", "<month>", "<year>", "<date>"}:
                continue
            templates.add(f'{candidate.role or "action"}:{label}')
        return templates

    def _screen_structure_tokens(self, screen: ScreenRecord) -> set[str]:
        tokens: set[str] = set()
        for raw_line in screen.observation_text.splitlines()[:180]:
            match = OBSERVATION_NODE_RE.match(raw_line)
            if match is None:
                continue
            role = str(match.group("role") or "").strip().lower()
            if role in {"statictext", "text", "row", "gridcell"}:
                continue
            label = self._normalize_temporal_text(match.group("label") or "")
            if not label:
                tokens.add(role)
                continue
            tokens.add(f"{role}:{label}")
        if not tokens:
            return self._screen_tokens(screen)
        return tokens

    def _screen_looks_temporal(
        self,
        screen: ScreenRecord,
        candidates: list[ActionCandidate],
    ) -> bool:
        parsed = urlparse(screen.url)
        path = (parsed.path or "").lower()
        if "calendar" in path or "schedule" in path:
            return True
        query_keys = {
            key.lower()
            for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
        }
        if query_keys & TEMPORAL_QUERY_KEYS:
            return True
        if TEMPORAL_TERM_RE.search(screen.title or ""):
            return True
        if any(candidate.signature.startswith("temporal:") for candidate in candidates[:24] if candidate.signature):
            return True
        important_text = " ".join(screen.important_dom[:8])
        return bool(
            (MONTH_NAME_RE.search(important_text) or DATE_RE.search(important_text))
            and TEMPORAL_TERM_RE.search(important_text)
        )

    def _normalized_query_template(self, url: str) -> str:
        parsed = urlparse(url)
        retained = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            normalized_key = (key or "").strip().lower()
            if normalized_key in TEMPORAL_QUERY_KEYS:
                continue
            normalized_value = re.sub(r"\s+", " ", str(value or "").strip().lower())
            retained.append((normalized_key, normalized_value))
        retained.sort()
        return "&".join(
            f"{key}={value}"
            for key, value in retained
        )

    def _normalize_temporal_text(self, text: str) -> str:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return ""
        normalized = DATE_RE.sub(" <date> ", normalized)
        normalized = MONTH_NAME_RE.sub(" <month> ", normalized)
        normalized = WEEKDAY_RE.sub(" <weekday> ", normalized)
        normalized = YEAR_RE.sub(" <year> ", normalized)
        normalized = STANDALONE_NUMBER_RE.sub(" <day> ", normalized)
        normalized = re.sub(r"[^\w\s<>/-]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = normalized.replace("<day> <day>", "<day>")
        return normalized

    def _jaccard_similarity(self, left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        intersection = len(left & right)
        union = len(left | right)
        if union <= 0:
            return 0.0
        return intersection / union

    def _form_signature(self, screen: ScreenRecord) -> str:
        labels = []
        for field in screen.form_fields[:10]:
            label = (field.label or field.field_type).strip().lower()
            if label:
                labels.append(label)
        return "|".join(sorted(dict.fromkeys(labels)))

    def _normalized_route(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.path or "/"

    def _normalize_str_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return self._unique_non_empty([str(item).strip() for item in value])
        if value is None:
            return []
        return self._unique_non_empty([str(value).strip()])

    def _unique_non_empty(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def _coerce_float(self, value: Any) -> float:
        try:
            number = float(value)
        except Exception:
            return 0.0
        return max(0.0, min(1.0, number))
