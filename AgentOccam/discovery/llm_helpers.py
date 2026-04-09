"""LLM helpers for hybrid discovery, annotation, and task generation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from AgentOccam.discovery.models import (
    ActionCandidate,
    DiscoveryRunConfig,
    ScreenRecord,
)
from AgentOccam.logger import logger

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
ACTION_VERB_RE = re.compile(r"\b(new|create|add|request|submit|edit|update|view|manage|open)\b", re.IGNORECASE)
FORM_HINT_RE = re.compile(r"\b(new|create|request|submit|form|edit|details|settings|profile|employee|calendar|team)\b", re.IGNORECASE)


def truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def extract_json_payload(text: str) -> Any:
    if not text:
        raise ValueError("Empty LLM response.")

    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fenced = JSON_BLOCK_RE.findall(text)
    for block in fenced:
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            continue

    first_object = stripped.find("{")
    last_object = stripped.rfind("}")
    if first_object != -1 and last_object != -1 and last_object > first_object:
        try:
            return json.loads(stripped[first_object : last_object + 1])
        except json.JSONDecodeError:
            pass

    first_array = stripped.find("[")
    last_array = stripped.rfind("]")
    if first_array != -1 and last_array != -1 and last_array > first_array:
        try:
            return json.loads(stripped[first_array : last_array + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError("Could not parse JSON payload from LLM response.")


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

    def __init__(self, config: DiscoveryRunConfig) -> None:
        self.config = config
        self._ranking_call = None
        self._annotation_call = None
        self._task_call = None
        self._revisit_call = None
        self._availability_error = ""

        if not config.llm.enabled:
            return

        try:
            from AgentOccam.model_registry import build_call_model
        except Exception as exc:
            self._availability_error = str(exc)
            return

        try:
            if config.llm.use_for_action_ranking:
                self._ranking_call = build_call_model(config.llm.exploration_model, system_prompt="")
            if config.llm.use_for_screen_annotation:
                self._annotation_call = build_call_model(config.llm.resolved_annotation_model(), system_prompt="")
            if config.llm.use_for_page_revisit_check:
                self._revisit_call = build_call_model(config.llm.resolved_annotation_model(), system_prompt="")
            if config.llm.use_for_task_generation:
                model_id = config.task_generation.llm_model or config.llm.resolved_task_generation_model()
                self._task_call = build_call_model(model_id, system_prompt="")
        except Exception as exc:
            self._availability_error = str(exc)
            self._ranking_call = None
            self._annotation_call = None
            self._task_call = None
            self._revisit_call = None

    @property
    def is_available(self) -> bool:
        return any(
            call is not None
            for call in (self._ranking_call, self._annotation_call, self._task_call, self._revisit_call)
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

        visited_page_descriptors = [
            f"{item.title or item.url} ({item.page_type or 'unknown'})"
            for item in discovered_screens[:12]
        ]
        prompt = f"""You are annotating one discovered page from a web application under test.

Your goal is to summarize the page and suggest realistic user tasks that this page can support.
Focus on concrete workflows, not abstract descriptions.

Return strict JSON with keys:
{{
  "page_type": "<short label>",
  "summary": "<2-3 sentence summary>",
  "key_entities": ["..."],
  "domain_objects": ["..."],
  "forms_detected": ["..."],
  "task_opportunities": ["..."],
  "important_dom": ["..."],
  "secondary_dom": ["..."],
  "write_risk": "low|medium|high"
}}

SUT: {self.config.sut.name}
Current URL: {screen.url}
Page title: {screen.title}
Path from start: {path_actions}
Previously discovered pages: {visited_page_descriptors}
Exploration memory:
{memory_summary or "- None"}

Candidate actions on this page:
{self._format_candidates(candidate_actions, max_items=18)}

Detected form fields:
{self._format_form_fields(screen)}

Accessibility tree excerpt:
{truncate_text(screen.observation_text, self.config.llm.annotation_observation_chars)}
"""
        try:
            response = self._annotation_call(prompt=prompt)
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

        discovered_descriptors = [
            f"{item.title or item.url} ({item.page_type or 'unknown'})"
            for item in discovered_screens[:18]
        ]
        prompt = f"""You are selecting the best actions to explore a web application under test.

Choose up to {limit} actions that are most likely to reveal new workflows, new screens, or task-worthy behavior.
Prefer navigation that broadens SUT coverage. Avoid repetitive clicks, legal/footer links, and low-value toggles.
Assume destructive actions were filtered already, but still prefer reversible exploration.
Candidates marked as shared chrome come from header, footer, or sidebar regions that may repeat across pages; only keep them if they are still likely to unlock a new module.
Strongly prefer actions that are likely to lead to an unseen page or a genuinely new workflow, not just a minor variant of an already visited page.

Return strict JSON:
{{
  "selected_actions": [
    {{
      "action": "click [123]",
      "reason": "<short reason>",
      "novelty_score": 0.0
    }}
  ]
}}

SUT: {self.config.sut.name}
Current URL: {screen.url}
Page title: {screen.title}
Page type: {screen.page_type or 'unknown'}
Current path: {path_actions}
Already discovered pages: {discovered_descriptors}
Exploration memory:
{memory_summary or "- None"}

Candidate actions:
{self._format_candidates(candidate_actions, max_items=32)}

Detected form fields:
{self._format_form_fields(screen)}

Accessibility tree excerpt:
{truncate_text(screen.observation_text, self.config.llm.ranking_observation_chars)}
"""
        try:
            response = self._ranking_call(prompt=prompt)
            payload = extract_json_payload(response)
            selected_payload = payload.get("selected_actions", [])
            selected_by_action = {
                item.action: item for item in candidate_actions
            }
            ranked: list[ActionCandidate] = []
            for raw in selected_payload:
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
                        selected_by="llm",
                    )
                )
                if len(ranked) >= limit:
                    break
            if ranked:
                return ranked
        except Exception as exc:
            screen.metadata["ranking_error"] = str(exc)
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
        return self._task_call(prompt=prompt)

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

        if self._revisit_call is None:
            return self._heuristic_screen_revisit(current_screen, candidate_screens)

        prompt = f"""You are deciding whether a newly reached page in a web application exploration is truly new, or is just a revisited variant of a page that has already been recorded.

Treat the page as ALREADY VISITED when the differences are minor or transient, such as:
- success or warning alerts
- expanded rows, opened accordions, or highlighted selections
- small filter or sort changes that do not create a new workflow
- the same page with slightly different visible records

Treat the page as NEW when it introduces a distinct workflow, page type, dedicated form, settings area, object detail page, modal workflow, or module that should be separately recorded.

Return strict JSON:
{{
  "is_revisit": true,
  "matched_screen_id": "screen_0001",
  "reason": "<short explanation>",
  "confidence": 0.0,
  "difference_type": "same_page_variant|same_page_with_alert|same_page_filter|distinct_page|distinct_form|uncertain"
}}

Current page:
- URL: {current_screen.url}
- Title: {current_screen.title}
- Form fields:
{self._format_form_fields(current_screen)}
- Accessibility tree:
{truncate_text(current_screen.observation_text, self.config.llm.revisit_observation_chars)}

Exploration memory:
{memory_summary or "- None"}

Candidate previously visited pages:
{self._format_screen_candidates(candidate_screens)}
"""
        try:
            response = self._revisit_call(prompt=prompt)
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

        return self._heuristic_screen_revisit(current_screen, candidate_screens)

    def decide_next_action(
        self,
        screen: ScreenRecord,
        candidate_actions: list[ActionCandidate],
        memory_summary: str,
        elapsed_minutes: float,
        remaining_minutes: float,
    ) -> dict[str, str]:
        if self._ranking_call is None or not self.config.llm.use_for_next_action_decision:
            return self._heuristic_next_action(candidate_actions)

        prompt = f"""You are autonomously exploring a web application under test in a single live browser session.

Choose exactly one next action that best improves coverage and avoids pointless loops.
You may choose:
- one candidate action listed below
- "go_back" if the current branch looks exhausted
- "stop" if exploration is saturated or no meaningful action remains

Avoid:
- repeating no-change actions
- oscillations like go_back then reopening the same page
- footer/legal links
- actions whose outcome is already well known unless they unlock a clearly new branch

Return strict JSON:
{{
  "action": "<candidate action | go_back | stop>",
  "reason": "<short explanation>",
  "mark_screen_done": true
}}

Elapsed minutes: {elapsed_minutes:.2f}
Remaining minutes: {remaining_minutes:.2f}
Current URL: {screen.url}
Current title: {screen.title}
Current page type: {screen.page_type or 'unknown'}
Current page summary: {screen.summary or 'None'}
Current task opportunities: {screen.task_opportunities}

Primary goal:
- Find unseen pages or distinct workflows that have not been recorded yet.
- If an action is likely to land on an already visited page or only change minor UI state, avoid it.

Exploration memory:
{memory_summary}

Available next actions:
{self._format_candidates(candidate_actions, max_items=40)}

Detected form fields:
{self._format_form_fields(screen)}

Full accessibility tree of the current page:
{truncate_text(screen.observation_text, self.config.llm.decision_observation_chars)}
"""
        try:
            response = self._ranking_call(prompt=prompt)
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
        return (candidate.label or candidate.raw_text or candidate.action).strip()

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
            lines.append(
                f'- [{field.element_id}] {field.field_type} "{label}" ({required})'
            )
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
        current_route = self._normalized_route(current_screen.url)
        current_title = (current_screen.title or "").strip().lower()
        current_form_signature = self._form_signature(current_screen)
        current_tokens = self._screen_tokens(current_screen)
        best_screen = None
        best_score = -1
        for candidate in candidate_screens:
            score = 0
            if self._normalized_route(candidate.url) == current_route:
                score += 5
            if current_title and current_title == (candidate.title or "").strip().lower():
                score += 3
            if current_form_signature and current_form_signature == self._form_signature(candidate):
                score += 4
            overlap = len(current_tokens & self._screen_tokens(candidate))
            score += min(overlap, 4)
            if score > best_score:
                best_score = score
                best_screen = candidate
        if best_screen is not None and best_score >= 8:
            return {
                "is_revisit": True,
                "matched_screen_id": best_screen.screen_id,
                "reason": "Heuristic matched route/title/form signature overlap with an existing screen.",
                "confidence": min(1.0, best_score / 12.0),
                "difference_type": "same_page_variant",
            }
        return {
            "is_revisit": False,
            "matched_screen_id": "",
            "reason": "Heuristic did not find a strong enough existing page match.",
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
