"""Shared ADK session client for discovery-oriented LLM roles."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from AgentOccam.discovery.action_summary import (
    summarize_candidate_action,
    summarize_transition_action,
)
from AgentOccam.discovery.memory import ExplorationMemory
from AgentOccam.discovery.models import DiscoveryRunConfig, ScreenRecord, TransitionRecord
from AgentOccam.discovery.prompt_templates import get_discovery_role_instruction
from AgentOccam.llms.retry_utils import (
    compute_retry_delay_seconds,
    should_retry_error,
)
from AgentOccam.logger import logger


@dataclass
class ADKRoleInvocation:
    role: str
    model_id: str
    session_id: str
    success: bool
    prompt_excerpt: str
    response_excerpt: str
    timestamp_utc: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ADKDiscoveryClient:
    """Maintains shared ADK sessions for discovery LLM roles and state snapshots."""

    def __init__(self, config: DiscoveryRunConfig) -> None:
        self.config = config
        self.enabled = bool(config.adk.enabled)
        self.available = False
        self.availability_error = ""
        self.run_id = ""
        self.run_dir = ""
        self._session_service: Any | None = None
        self._llm_agent_cls: Any | None = None
        self._runner_cls: Any | None = None
        self._types: Any | None = None
        self._role_runners: dict[str, Any] = {}
        self._role_session_ids: dict[str, str] = {}
        self._role_metadata: dict[str, tuple[str, str]] = {}
        self._role_invocations: list[ADKRoleInvocation] = []
        self._prompt_log_counter = 0
        self._screen_index_by_id: dict[str, int] = {}
        self._state_snapshot: dict[str, Any] = {
            "run": {},
            "memory": {},
            "screens": [],
            "transitions": [],
            "task_plan": [],
            "generated_tasks": [],
            "summary": {},
        }
        if not self.enabled:
            return
        try:
            from google.adk.agents import LlmAgent
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
            from google.genai import types

            self._llm_agent_cls = LlmAgent
            self._runner_cls = Runner
            self._types = types
            self._session_service = InMemorySessionService()
            self.available = True
        except Exception as exc:
            self.availability_error = str(exc)
            logger.warning("ADK discovery client unavailable: %s", exc)

    @property
    def use_for_llm_calls(self) -> bool:
        return (
            self.enabled
            and self.available
            and self.config.adk.use_for_discovery_llm_calls
        )

    def start_run(self, run_id: str, run_dir: Path, mode: str) -> None:
        self.run_id = run_id
        self.run_dir = str(run_dir)
        self._prompt_log_counter = 0
        if not self._should_track_state():
            return

        self._screen_index_by_id.clear()
        self._state_snapshot = {
            "run": {},
            "memory": {},
            "screens": [],
            "transitions": [],
            "task_plan": [],
            "generated_tasks": [],
            "summary": {},
        }
        self._state_snapshot["run"] = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "sut": self.config.sut.name,
            "start_url": self.config.sut.start_url,
            "mode": mode,
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "adk_enabled": self.enabled,
            "adk_available": self.available,
            "adk_availability_error": self.availability_error,
            "role_session_mode": self.config.adk.role_session_mode,
            "sessioned_llm_roles": self.config.adk.sessioned_llm_roles[:],
        }

    def log_role_prompt(
        self,
        role: str,
        model_id: str,
        prompt: str,
        system_prompt: str = "",
        metadata: dict[str, Any] | None = None,
        provider: str | None = None,
    ) -> Path | None:
        if not self.run_dir:
            return None
        prompts_dir = Path(self.run_dir) / "prompt_logs"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        self._prompt_log_counter += 1
        target = prompts_dir / f"{self._prompt_log_counter:04d}_{role}.json"
        combined_prompt = prompt.strip()
        if system_prompt.strip():
            combined_prompt = (
                "SYSTEM PROMPT:\n"
                + system_prompt.strip()
                + "\n\nUSER PROMPT:\n"
                + prompt.strip()
            )
        payload = {
            "sequence": self._prompt_log_counter,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "model_id": model_id,
            "provider": provider or ("adk" if self.use_for_llm_calls else "direct"),
            "system_prompt": system_prompt,
            "user_prompt": prompt,
            "combined_prompt": combined_prompt,
            "metadata": metadata or {},
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    def record_memory(
        self,
        memory: ExplorationMemory,
        current_screen: ScreenRecord | None = None,
    ) -> None:
        if not self._should_track_state():
            return
        self._state_snapshot["memory"] = memory.build_compact_snapshot(current_screen=current_screen)

    def record_screen(
        self,
        screen: ScreenRecord,
        is_new: bool,
        path_actions: list[str],
        live_screen_id: str = "",
        semantic_revisit_of: str = "",
    ) -> None:
        if not self._should_track_state():
            return
        screen_entry = {
            "screen_id": screen.screen_id,
            "live_screen_id": live_screen_id or screen.screen_id,
            "title": screen.title,
            "url": screen.url,
            "page_type": screen.page_type,
            "summary": screen.summary,
            "domain_objects": screen.domain_objects[:],
            "forms_detected": screen.forms_detected[:],
            "form_fields": [
                {
                    "label": field.label,
                    "field_type": field.field_type,
                    "required": field.required,
                }
                for field in screen.form_fields[:12]
            ],
            "important_dom": screen.important_dom[:8],
            "secondary_dom": screen.secondary_dom[:8],
            "task_opportunities": screen.task_opportunities[:6],
            "recommended_actions": [
                {
                    "action_summary": summarize_candidate_action(candidate),
                    "action_signature": candidate.signature,
                    "label": candidate.label,
                    "reason": candidate.reason,
                    "zone": candidate.zone,
                    "selected_by": candidate.selected_by,
                }
                for candidate in screen.recommended_actions[:10]
            ],
            "is_new": is_new,
            "path_actions": path_actions[:],
            "semantic_revisit_of": semantic_revisit_of,
        }
        screens: list[dict[str, Any]] = self._state_snapshot["screens"]
        existing_index = self._screen_index_by_id.get(screen.screen_id)
        if existing_index is None:
            screens.append(screen_entry)
            self._screen_index_by_id[screen.screen_id] = len(screens) - 1
        else:
            screens[existing_index] = screen_entry

    def record_transition(
        self,
        transition: TransitionRecord,
        effect: str,
        source_screen: ScreenRecord,
        destination_screen: ScreenRecord,
    ) -> None:
        if not self._should_track_state():
            return
        transitions: list[dict[str, Any]] = self._state_snapshot["transitions"]
        transitions.append(
            {
                "from_screen_id": transition.from_screen_id,
                "to_screen_id": transition.to_screen_id,
                "action_summary": summarize_transition_action(transition),
                "action_signature": str(transition.metadata.get("action_signature", "")),
                "action_label": transition.action_label,
                "success": transition.success,
                "effect": effect,
                "source_title": source_screen.title,
                "source_url": source_screen.url,
                "destination_title": destination_screen.title,
                "destination_url": destination_screen.url,
                "selection_reason": transition.selection_reason,
                "metadata": dict(transition.metadata),
            }
        )
        if len(transitions) > 120:
            del transitions[:-120]

    def record_task_plan(self, task_plan: list[dict[str, Any]]) -> None:
        if not self._should_track_state():
            return
        self._state_snapshot["task_plan"] = task_plan[:]

    def record_generated_tasks(self, generated_tasks: list[dict[str, Any]]) -> None:
        if not self._should_track_state():
            return
        self._state_snapshot["generated_tasks"] = generated_tasks[:]

    def record_summary(self, summary: dict[str, Any]) -> None:
        if not self._should_track_state():
            return
        self._state_snapshot["summary"] = dict(summary)

    def build_prompt_state_block(self, current_screen: ScreenRecord | None = None) -> str:
        prompt_state = {
            "run": {
                "sut": self._state_snapshot["run"].get("sut", self.config.sut.name),
                "mode": self._state_snapshot["run"].get("mode", self.config.discovery.mode),
            },
            "memory": self._state_snapshot.get("memory", {}),
            "recent_screens": self._state_snapshot.get("screens", [])[-6:],
            "recent_transitions": self._state_snapshot.get("transitions", [])[-8:],
            "current_screen": {
                "screen_id": current_screen.screen_id,
                "title": current_screen.title,
                "url": current_screen.url,
                "page_type": current_screen.page_type,
                "summary": current_screen.summary,
            }
            if current_screen is not None
            else {},
            "recent_role_invocations": [
                {
                    "role": item.role,
                    "success": item.success,
                    "response_excerpt": item.response_excerpt,
                }
                for item in self._role_invocations[-6:]
            ],
        }
        text = json.dumps(prompt_state, ensure_ascii=False, indent=2)
        max_chars = max(0, int(self.config.adk.prompt_state_max_chars))
        if max_chars and len(text) > max_chars:
            return text[: max_chars - 3] + "..."
        return text

    def call_role(
        self,
        role: str,
        prompt: str,
        model_id: str,
        system_prompt: str = "",
    ) -> str:
        if not self.use_for_llm_calls:
            raise RuntimeError("ADK discovery client is not active for LLM calls.")

        runner, session_id = self._ensure_role_runner(
            role=role,
            model_id=model_id,
            system_prompt=system_prompt,
        )
        content = self._types.Content(
            role="user",
            parts=[self._types.Part(text=prompt)],
        )
        response_text = ""
        error_message = ""
        success = False
        try:
            max_attempts = 8
            for attempt_index in range(max_attempts):
                response_text = ""
                try:
                    events = runner.run(
                        user_id=self.config.adk.user_id,
                        session_id=session_id,
                        new_message=content,
                    )
                    for event in events:
                        if event.is_final_response() and event.content:
                            for part in event.content.parts or []:
                                if getattr(part, "text", ""):
                                    response_text = part.text.strip()
                                    break
                        if response_text:
                            break
                    if not response_text:
                        raise ValueError(f"No response generated from ADK role '{role}'")
                    success = True
                    return response_text
                except Exception as exc:
                    error_message = str(exc)
                    if attempt_index >= max_attempts - 1 or not should_retry_error(exc):
                        raise
                    wait_seconds = compute_retry_delay_seconds(
                        exc,
                        attempt_index,
                        default_seconds=10.0,
                        quota_seconds=30.0,
                    )
                    logger.warning(
                        "ADK role call retry scheduled: role=%s model=%s session_id=%s attempt=%s/%s wait=%.1fs error=%s",
                        role,
                        model_id,
                        session_id,
                        attempt_index + 1,
                        max_attempts,
                        wait_seconds,
                        exc,
                    )
                    time.sleep(wait_seconds)
        except Exception as exc:
            error_message = str(exc)
            raise
        finally:
            self._append_invocation(
                ADKRoleInvocation(
                    role=role,
                    model_id=model_id,
                    session_id=session_id,
                    success=success,
                    prompt_excerpt=self._truncate(prompt, 500),
                    response_excerpt=self._truncate(response_text or error_message, 500),
                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    error=error_message,
                )
            )

    def export_snapshot(self, run_dir: Path) -> Path | None:
        if not self.enabled or not self.config.adk.export_session_snapshot:
            return None
        target = run_dir / "adk_session_snapshot.json"
        payload = {
            "enabled": self.enabled,
            "available": self.available,
            "availability_error": self.availability_error,
            "role_sessions": dict(self._role_session_ids),
            "state": self._state_snapshot,
            "role_invocations": [item.to_dict() for item in self._role_invocations],
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Exported ADK discovery snapshot: %s", target)
        return target

    def _ensure_role_runner(
        self,
        role: str,
        model_id: str,
        system_prompt: str,
    ) -> tuple[Any, str]:
        role_key = role
        role_instruction = system_prompt.strip() or get_discovery_role_instruction(role)
        current_metadata = (model_id, role_instruction)
        if role_key in self._role_runners and self._role_metadata.get(role_key) == current_metadata:
            return self._role_runners[role_key], self._role_session_ids[role_key]

        actual_model_id = model_id.replace("adk-", "") if model_id.startswith("adk-") else model_id
        session_id = self._build_session_id(role_key)
        agent = self._llm_agent_cls(
            model=actual_model_id,
            name=f"agentoccam_{role_key}",
            instruction=role_instruction,
            description=f"AgentOccam discovery helper for role '{role_key}'",
        )
        try:
            self._run_async(
                self._session_service.create_session(
                    app_name=self.config.adk.app_name,
                    user_id=self.config.adk.user_id,
                    session_id=session_id,
                )
            )
        except Exception as exc:
            if "already" not in str(exc).lower():
                raise
        runner = self._runner_cls(
            agent=agent,
            app_name=self.config.adk.app_name,
            session_service=self._session_service,
        )
        self._role_runners[role_key] = runner
        self._role_session_ids[role_key] = session_id
        self._role_metadata[role_key] = current_metadata
        logger.info(
            "Initialized ADK role runner: role=%s model=%s session_id=%s",
            role_key,
            actual_model_id,
            session_id,
        )
        return runner, session_id

    def _append_invocation(self, invocation: ADKRoleInvocation) -> None:
        self._role_invocations.append(invocation)
        max_items = max(1, int(self.config.adk.max_recorded_role_invocations))
        if len(self._role_invocations) > max_items:
            del self._role_invocations[:-max_items]

    def _should_track_state(self) -> bool:
        return self.enabled and (
            self.use_for_llm_calls or self.config.adk.export_session_snapshot
        )

    def _build_session_id(self, role: str) -> str:
        prefix = self.config.adk.session_prefix.strip() or "discovery"
        run_token = self.run_id or "run"
        if self.config.adk.role_session_mode == "shared":
            return f"{prefix}_{run_token}"
        return f"{prefix}_{run_token}_{role}"

    def _run_async(self, coro: Any) -> Any:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)

    def _truncate(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."
