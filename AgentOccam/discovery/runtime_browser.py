"""Browser state and capture helpers for the discovery runtime."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from AgentOccam.discovery.action_summary import (
    matches_path_step,
    snapshot_candidate_for_path,
    summarize_candidate_action,
    summarize_path_steps,
)
from AgentOccam.discovery.models import ActionCandidate, ScreenRecord, TransitionRecord
from AgentOccam.logger import logger


class DiscoveryRuntimeBrowserMixin:
    def _build_env(self):
        return self._script_browser_env_cls(
            headless=self.config.discovery.headless,
            slow_mo=self.config.discovery.slow_mo_ms,
            observation_type=self.config.discovery.observation_type,
            current_viewport_only=self.config.discovery.current_viewport_only,
            viewport_size={
                "width": self.config.discovery.viewport_width,
                "height": self.config.discovery.viewport_height,
            },
            sleep_after_execution=self.config.discovery.sleep_after_execution_sec,
            global_config=SimpleNamespace(logname=f"discovery-{self.pipeline.run_id}"),
        )

    def _write_browser_config(self, run_dir: Path) -> Path:
        browser_config = {
            "sites": [self.config.sut.name],
            "task_id": f"{self.config.sut.name}_discovery",
            "storage_state": self.config.sut.storage_state,
            "start_url": self.config.sut.start_url,
            "geolocation": None,
        }
        target = run_dir / "discovery_browser_config.json"
        target.write_text(
            json.dumps(browser_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("Wrote browser config: %s", target)
        return target

    def _reset_env(self):
        assert self.env is not None
        assert self.browser_config_path is not None
        return self.env.reset(options={"config_file": str(self.browser_config_path)})

    def _reset_and_capture(self) -> ScreenRecord:
        observation, info = self._reset_env()
        return self._capture_screen(observation=observation, info=info)

    def _restore_browser_to_path(self, path_steps: list[ActionCandidate]) -> bool:
        try:
            observation, info = self._reset_env()
        except Exception:
            logger.exception("Failed to reset browser while restoring discovery state.")
            return False

        current_screen = self._capture_screen(
            observation=observation,
            info=info,
            metadata={"replay_restore": True},
            capture_screenshot=False,
        )
        replayed_actions: list[str] = []
        for step in path_steps:
            candidate = self._resolve_replay_candidate(current_screen, step)
            if candidate is None:
                logger.warning(
                    "Failed to resolve replay step after blocked navigation: step=%s replayed=%s url=%s",
                    summarize_candidate_action(step),
                    replayed_actions,
                    current_screen.url,
                )
                return False
            success, observation, info, error_message = self._execute_action(candidate.action)
            if not success:
                logger.warning(
                    "Failed to restore browser state after blocked navigation at step=%s replayed=%s error=%s",
                    summarize_candidate_action(step),
                    replayed_actions,
                    error_message,
                )
                return False
            replayed_actions.append(summarize_candidate_action(step))
            if observation is None or info is None:
                logger.warning(
                    "Replay step did not yield an updated observation: step=%s",
                    summarize_candidate_action(step),
                )
                return False
            current_screen = self._capture_screen(
                observation=observation,
                info=info,
                metadata={
                    "replay_restore": True,
                    "via_action": summarize_candidate_action(step),
                },
                capture_screenshot=False,
            )
        return True

    def _execute_from_current_screen(
        self,
        source_screen_id: str,
        source_live_screen: ScreenRecord | None,
        candidate: ActionCandidate,
        path_steps: list[ActionCandidate],
    ) -> tuple[TransitionRecord, str | None, ScreenRecord | None]:
        action_summary = summarize_candidate_action(candidate)
        logger.info(
            "Executing action from screen=%s action=%s label=%s selected_by=%s",
            source_screen_id,
            candidate.action,
            candidate.label or "(no label)",
            candidate.selected_by,
        )
        success, observation, info, error_message = self._execute_action(candidate.action)
        if not success or observation is None or info is None:
            logger.warning(
                "Action failed on screen=%s action=%s error=%s",
                source_screen_id,
                candidate.action,
                error_message,
            )
            return (
                TransitionRecord(
                    from_screen_id=source_screen_id,
                    to_screen_id=source_screen_id,
                    action=action_summary,
                    success=False,
                    action_label=candidate.label,
                    selection_reason=candidate.reason,
                    error_message=error_message,
                    metadata={
                        "action_signature": candidate.signature,
                        "action_summary": action_summary,
                    },
                ),
                None,
                None,
            )

        destination_record = self._capture_screen(
            observation=observation,
            info=info,
            metadata={"via_action": action_summary},
        )
        if not self._is_allowed_url(destination_record.url):
            restored = self._restore_browser_to_path(path_steps)
            logger.warning(
                "Blocked navigation outside allowed domains from screen=%s action=%s url=%s restored=%s",
                source_screen_id,
                candidate.action,
                destination_record.url,
                restored,
            )
            return (
                TransitionRecord(
                    from_screen_id=source_screen_id,
                    to_screen_id=source_screen_id,
                    action=action_summary,
                    success=False,
                    action_label=candidate.label,
                    selection_reason=candidate.reason,
                    error_message=(
                        f"Navigated outside allowed domains: {destination_record.url}"
                    ),
                    metadata={
                        "action_signature": candidate.signature,
                        "action_summary": action_summary,
                        "blocked_url": destination_record.url,
                        "selected_by": candidate.selected_by,
                        "browser_state_restored": restored,
                    },
                ),
                None,
                None,
            )
        destination_screen_id, destination_is_new, _ = self._register_screen(
            destination_record,
            path_actions=summarize_path_steps(path_steps + [snapshot_candidate_for_path(candidate)]),
        )
        destination_record.metadata["canonical_screen_id"] = destination_screen_id or source_screen_id
        self._record_candidate_outcome(candidate, destination_screen_id)
        logger.info(
            "Action succeeded from=%s to=%s live=%s action=%s",
            source_screen_id,
            destination_screen_id,
            destination_record.screen_id,
            candidate.action,
        )
        return (
            TransitionRecord(
                from_screen_id=source_screen_id,
                to_screen_id=destination_screen_id,
                action=action_summary,
                success=True,
                action_label=candidate.label,
                selection_reason=candidate.reason,
                error_message="",
                metadata={
                    "action_signature": candidate.signature,
                    "action_summary": action_summary,
                    "selected_by": candidate.selected_by,
                    "novelty_score": candidate.novelty_score,
                    "destination_is_new": destination_is_new,
                    "destination_live_screen_id": destination_record.screen_id,
                    "destination_live_fingerprint": destination_record.fingerprint,
                    "semantic_revisit_of": destination_record.metadata.get("semantic_revisit_of", ""),
                    "semantic_revisit_mode": destination_record.metadata.get("semantic_revisit_mode", ""),
                    "source_live_screen_id": source_live_screen.screen_id if source_live_screen else "",
                    "live_fingerprint_changed": (
                        source_live_screen.fingerprint != destination_record.fingerprint
                        if source_live_screen is not None
                        else None
                    ),
                },
            ),
            destination_screen_id,
            destination_record,
        )

    def _resolve_replay_candidate(
        self,
        screen: ScreenRecord,
        path_step: ActionCandidate,
    ) -> ActionCandidate | None:
        if path_step.signature == "navigation:go_back":
            return ActionCandidate(
                action="go_back",
                role="navigation",
                label="Go Back",
                zone="navigation",
                signature="navigation:go_back",
                source="navigation",
                selected_by="restore",
            )

        for candidate in self.policy.enumerate_actions(screen):
            if matches_path_step(candidate, path_step):
                return candidate
        return None

    def _execute_action(self, action: str) -> tuple[bool, Any | None, dict[str, Any] | None, str]:
        assert self.env is not None
        try:
            action_object = self._create_id_based_action(action)
            if not action_object:
                return False, None, None, f"Action was not parsed: {action}"
            observation, _, _, _, info = self.env.step(action_object)
            return True, observation, info, ""
        except Exception as exc:
            return False, None, None, str(exc)

    def _capture_screen(
        self,
        observation: Any,
        info: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        capture_screenshot: bool = True,
    ) -> ScreenRecord:
        assert self.env is not None
        self.screen_counter += 1
        screen_id = f"screen_{self.screen_counter:04d}"
        title = ""
        can_go_back = False
        try:
            title = self.env.page.title()
        except Exception:
            title = ""
        try:
            can_go_back = bool(self.env.page.evaluate("window.history.length > 1"))
        except Exception:
            can_go_back = False

        screenshot_path = None
        if capture_screenshot and self.config.discovery.screenshot_on_each_step:
            screenshot_path = str(self._save_screenshot(screen_id))

        merged_metadata = {
            "title": title,
            "can_go_back": can_go_back,
            "observation_metadata": info.get("observation_metadata", {}),
        }
        if metadata:
            merged_metadata.update(metadata)

        return self.pipeline.recorder.build_screen_record(
            screen_id=screen_id,
            url=self.env.page.url,
            title=title,
            observation_text=self._extract_observation_text(observation),
            screenshot_path=screenshot_path,
            metadata=merged_metadata,
        )

    def _save_screenshot(self, screen_id: str) -> Path:
        assert self.env is not None
        screens_dir = self.pipeline.screens_dir
        if screens_dir is None:
            raise RuntimeError("DiscoveryPipeline.prepare_run() must be called before screenshots.")
        target = screens_dir / f"{screen_id}.png"
        self.env.page.screenshot(path=str(target), full_page=True)
        return target

    def _extract_observation_text(self, observation: Any) -> str:
        if isinstance(observation, dict):
            text_value = observation.get("text", "")
            if isinstance(text_value, (list, tuple)):
                return str(text_value[0]) if text_value else ""
            return str(text_value)
        return str(observation)

    def _is_allowed_url(self, url: str) -> bool:
        allowed_domains = [
            domain.strip().lower()
            for domain in self.config.sut.allowed_domains
            if domain.strip()
        ]
        if not allowed_domains:
            return True
        try:
            netloc = urlparse(url).netloc.lower()
        except Exception:
            return False
        if not netloc:
            return False
        return any(
            netloc == allowed_domain or netloc.endswith(f".{allowed_domain}")
            for allowed_domain in allowed_domains
        )
