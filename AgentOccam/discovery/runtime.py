"""Runnable discovery executor for SUT exploration."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from AgentOccam.discovery.explorer import SafeBFSExplorerPolicy
from AgentOccam.discovery.llm_helpers import DiscoveryLLMCoordinator
from AgentOccam.discovery.memory import ExplorationMemory
from AgentOccam.discovery.models import (
    ActionCandidate,
    DiscoveryRunConfig,
    ScreenRecord,
    TransitionRecord,
)
from AgentOccam.discovery.pipeline import DiscoveryPipeline
from AgentOccam.logger import logger


class DiscoveryDependencyError(RuntimeError):
    """Raised when required runtime dependencies are unavailable."""


def _load_browser_env_runtime() -> tuple[type[Any], Callable[[str], Any]]:
    try:
        env_module = import_module("browser_env.envs")
        actions_module = import_module("browser_env.actions")
    except ModuleNotFoundError as exc:
        missing = exc.name or str(exc)
        raise DiscoveryDependencyError(
            "Discovery runtime dependencies are missing. "
            f"Install the required packages from requirements.txt first. Missing module: {missing}"
        ) from exc
    return env_module.ScriptBrowserEnv, actions_module.create_id_based_action


@dataclass
class FrontierNode:
    screen_id: str
    path_actions: list[str]
    depth: int


class DiscoveryExecutor:
    """Executes a conservative discovery run against an SUT."""

    def __init__(self, config: DiscoveryRunConfig) -> None:
        self.config = config
        self.pipeline = DiscoveryPipeline(config)
        self.policy = SafeBFSExplorerPolicy(
            max_candidate_actions_per_screen=config.discovery.max_candidate_actions_per_screen,
            include_go_back=False,
        )
        self.llm = DiscoveryLLMCoordinator(config)
        self.env: Any | None = None
        self.screen_counter = 0
        self.browser_config_path: Path | None = None
        self.start_time_monotonic = 0.0
        self.explored_actions = 0
        self.failed_actions = 0
        self.failed_replays = 0
        self.skipped_shared_chrome = 0
        self.semantic_revisits = 0
        self.llm_revisit_checks = 0
        self.tried_actions_by_screen: dict[str, set[str]] = defaultdict(set)
        self.best_depth_by_screen: dict[str, int] = {}
        self.signature_seen_on_screens: dict[str, set[str]] = defaultdict(set)
        self.signature_success_targets: dict[str, set[str]] = defaultdict(set)
        self.signature_attempt_count: dict[str, int] = defaultdict(int)
        self.memory = ExplorationMemory(config)
        self._script_browser_env_cls, self._create_id_based_action = _load_browser_env_runtime()

    def run(self) -> dict[str, Any]:
        run_dir = self.pipeline.prepare_run()
        self.browser_config_path = self._write_browser_config(run_dir)
        self.env = self._build_env()
        self.start_time_monotonic = time.monotonic()
        logger.info(
            "Discovery run started: sut=%s mode=%s time_budget=%sm max_steps=%s output=%s llm_available=%s",
            self.config.sut.name,
            self.config.discovery.mode,
            self.config.discovery.time_budget_minutes,
            self.config.discovery.max_steps,
            run_dir,
            self.llm.is_available,
        )

        try:
            if self.config.discovery.mode == "agentic_session":
                return self._run_agentic_session(run_dir)
            return self._run_guided_bfs(run_dir)
        finally:
            if self.env is not None:
                logger.info("Closing discovery browser environment.")
                self.env.close()

    def _run_guided_bfs(self, run_dir: Path) -> dict[str, Any]:
        root_record = self._reset_and_capture()
        root_screen_id, _, root_screen = self._register_screen(root_record, path_actions=[])
        self.best_depth_by_screen[root_screen_id] = 0
        logger.info(
            "Guided BFS initialized at root screen=%s title=%s url=%s",
            root_screen_id,
            root_screen.title or "(no title)",
            root_screen.url,
        )

        frontier: deque[FrontierNode] = deque(
            [FrontierNode(screen_id=root_screen_id, path_actions=[], depth=0)]
        )

        while frontier and self._within_limits():
            node = frontier.popleft()
            active_screen_id = node.screen_id
            active_screen = self.pipeline.graph.get_screen(active_screen_id)
            logger.debug(
                "Expanding BFS node screen=%s depth=%s frontier_remaining=%s",
                active_screen_id,
                node.depth,
                len(frontier),
            )

            if node.depth >= self.config.discovery.max_depth:
                continue

            for candidate in self._get_actions_for_screen(active_screen, node.depth):
                source_screen_id, source_screen = self._replay_to(node.path_actions)
                if source_screen_id is None or source_screen is None:
                    self.failed_replays += 1
                    break
                if not self._within_limits():
                    break
                if candidate.action in self.tried_actions_by_screen[source_screen_id]:
                    continue

                self.tried_actions_by_screen[source_screen_id].add(candidate.action)
                self.explored_actions += 1
                self._record_candidate_attempt(candidate)

                transition, destination_screen_id, destination_live_screen = self._execute_from_current_screen(
                    source_screen_id=source_screen_id,
                    source_live_screen=source_screen,
                    candidate=candidate,
                    path_actions=node.path_actions,
                )
                destination_screen = destination_live_screen or source_screen
                effect = self._classify_transition_effect(
                    source_screen=source_screen,
                    destination_screen=destination_screen,
                    success=transition.success,
                    destination_is_new=transition.metadata.get("destination_is_new"),
                    semantic_revisit_of=str(transition.metadata.get("semantic_revisit_of", "")),
                )
                transition.metadata["effect"] = effect
                self.pipeline.register_transition(transition)
                self.memory.register_attempt(candidate)
                self.memory.register_transition(
                    source_screen=source_screen,
                    destination_screen=destination_screen,
                    candidate=candidate,
                    effect=effect,
                    success=transition.success,
                )
                if not transition.success:
                    self.failed_actions += 1

                if (
                    transition.success
                    and destination_screen_id
                    and destination_screen_id != source_screen_id
                ):
                    next_depth = node.depth + 1
                    best_depth = self.best_depth_by_screen.get(destination_screen_id)
                    if best_depth is None or next_depth < best_depth:
                        self.best_depth_by_screen[destination_screen_id] = next_depth
                        logger.info(
                            "Queued newly discovered screen=%s from=%s depth=%s action=%s",
                            destination_screen_id,
                            source_screen_id,
                            next_depth,
                            candidate.label or candidate.action,
                        )
                        frontier.append(
                            FrontierNode(
                                screen_id=destination_screen_id,
                                path_actions=node.path_actions + [candidate.action],
                                depth=next_depth,
                            )
                        )

        return self._finalize_run(run_dir)

    def _run_agentic_session(self, run_dir: Path) -> dict[str, Any]:
        root_record = self._reset_and_capture()
        current_screen_id, _, current_screen = self._register_screen(root_record, path_actions=[])
        current_live_screen = root_record
        current_path_actions: list[str] = []
        logger.info(
            "Agentic session initialized at canonical=%s live=%s title=%s url=%s",
            current_screen_id,
            current_live_screen.screen_id,
            current_screen.title or "(no title)",
            current_screen.url,
        )

        while self._within_limits():
            canonical_screen = self.pipeline.graph.get_screen(current_screen_id)
            current_screen = current_live_screen
            depth = len(current_path_actions)
            logger.debug(
                "Agentic loop canonical=%s live=%s depth=%s tried_actions=%s elapsed=%.2fm remaining=%.2fm",
                current_screen_id,
                current_screen.screen_id,
                depth,
                len(self.tried_actions_by_screen[current_screen.screen_id]),
                self._elapsed_minutes(),
                self._remaining_minutes(),
            )

            candidates = self._get_actions_for_agentic_screen(current_screen, depth)
            untried_candidates = [
                candidate
                for candidate in candidates
                if candidate.action not in self.tried_actions_by_screen[current_screen.screen_id]
            ]

            if current_screen.metadata.get("can_go_back", False) and current_path_actions:
                untried_candidates.append(
                    ActionCandidate(
                        action="go_back",
                        role="navigation",
                        label="Go Back",
                        zone="navigation",
                        signature="navigation:go_back",
                        source="navigation",
                        selected_by="agentic",
                    )
                )

            if not untried_candidates:
                self.memory.mark_screen_done(canonical_screen, "No meaningful actions remain on this page.")
                logger.info(
                    "No remaining actions on live=%s canonical=%s title=%s; attempting go_back=%s",
                    current_screen.screen_id,
                    current_screen_id,
                    current_screen.title or "(no title)",
                    bool(current_path_actions and current_screen.metadata.get("can_go_back", False)),
                )
                if current_path_actions and current_screen.metadata.get("can_go_back", False):
                    back_candidate = ActionCandidate(
                        action="go_back",
                        role="navigation",
                        label="Go Back",
                        zone="navigation",
                        signature="navigation:go_back",
                        source="navigation",
                        selected_by="agentic",
                    )
                    self.explored_actions += 1
                    transition, destination_screen_id, destination_live_screen = self._execute_from_current_screen(
                        source_screen_id=current_screen_id,
                        source_live_screen=current_screen,
                        candidate=back_candidate,
                        path_actions=current_path_actions,
                    )
                    destination_screen = destination_live_screen or current_screen
                    effect = self._classify_transition_effect(
                        source_screen=current_screen,
                        destination_screen=destination_screen,
                        success=transition.success,
                        destination_is_new=transition.metadata.get("destination_is_new"),
                        semantic_revisit_of=str(transition.metadata.get("semantic_revisit_of", "")),
                    )
                    transition.metadata["effect"] = effect
                    self.pipeline.register_transition(transition)
                    self.memory.register_attempt(back_candidate)
                    self.memory.register_transition(
                        source_screen=current_screen,
                        destination_screen=destination_screen,
                        candidate=back_candidate,
                        effect=effect,
                        success=transition.success,
                    )
                    if transition.success and destination_live_screen is not None:
                        current_live_screen = destination_live_screen
                    current_screen_id, current_path_actions = self._apply_go_back_result(
                        destination_screen_id or current_screen_id,
                        current_path_actions,
                    )
                    continue
                break

            decision = self.llm.decide_next_action(
                screen=current_screen,
                candidate_actions=untried_candidates,
                memory_summary=self.memory.summarize_for_prompt(current_screen),
                elapsed_minutes=self._elapsed_minutes(),
                remaining_minutes=self._remaining_minutes(),
            )
            chosen_action = decision.get("action", "").strip()
            if decision.get("mark_screen_done") == "true":
                self.memory.mark_screen_done(current_screen, decision.get("reason", "Marked done by LLM"))
            if chosen_action == "stop":
                logger.info(
                    "LLM requested stop on canonical=%s live=%s title=%s reason=%s",
                    current_screen_id,
                    current_screen.screen_id,
                    current_screen.title or "(no title)",
                    decision.get("reason", ""),
                )
                break

            candidate = next((item for item in untried_candidates if item.action == chosen_action), None)
            if candidate is None:
                candidate = next((item for item in untried_candidates if item.action != "go_back"), untried_candidates[0])
                logger.debug(
                    "LLM action fallback applied on canonical=%s live=%s requested=%s actual=%s",
                    current_screen_id,
                    current_screen.screen_id,
                    chosen_action,
                    candidate.action,
                )

            self.tried_actions_by_screen[current_screen.screen_id].add(candidate.action)
            self.explored_actions += 1
            self._record_candidate_attempt(candidate)
            self.memory.register_attempt(candidate)

            transition, destination_screen_id, destination_live_screen = self._execute_from_current_screen(
                source_screen_id=current_screen_id,
                source_live_screen=current_screen,
                candidate=candidate,
                path_actions=current_path_actions,
            )
            destination_screen = destination_live_screen or current_screen
            effect = self._classify_transition_effect(
                source_screen=current_screen,
                destination_screen=destination_screen,
                success=transition.success,
                destination_is_new=transition.metadata.get("destination_is_new"),
                semantic_revisit_of=str(transition.metadata.get("semantic_revisit_of", "")),
            )
            transition.metadata["effect"] = effect
            transition.metadata["decision_reason"] = decision.get("reason", "")
            self.pipeline.register_transition(transition)
            self.memory.register_transition(
                source_screen=current_screen,
                destination_screen=destination_screen,
                candidate=candidate,
                effect=effect,
                success=transition.success,
            )

            if not transition.success:
                self.failed_actions += 1
                continue

            if destination_live_screen is not None:
                current_live_screen = destination_live_screen

            if candidate.action == "go_back":
                current_screen_id, current_path_actions = self._apply_go_back_result(
                    destination_screen_id or current_screen_id,
                    current_path_actions,
                )
                continue

            if effect != "no_change":
                current_path_actions.append(candidate.action)
            current_screen_id = destination_screen_id or current_screen_id

        return self._finalize_run(run_dir)

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

    def _replay_to(self, path_actions: list[str]) -> tuple[str | None, ScreenRecord | None]:
        observation, info = self._reset_env()
        current_record = self._capture_screen(
            observation=observation,
            info=info,
            capture_screenshot=False,
        )
        current_screen_id, _, current_screen = self._register_screen(
            current_record,
            path_actions=[],
            annotate_if_new=False,
        )
        if not path_actions:
            return current_screen_id, current_screen

        replayed_actions: list[str] = []
        for action in path_actions:
            success, observation, info, _error_message = self._execute_action(action)
            if not success or observation is None or info is None:
                return None, None
            replayed_actions.append(action)
            current_record = self._capture_screen(
                observation=observation,
                info=info,
                metadata={"replayed_via": replayed_actions[:]},
                capture_screenshot=False,
            )
            current_screen_id, _, current_screen = self._register_screen(
                current_record,
                path_actions=replayed_actions[:],
                annotate_if_new=False,
            )
        return current_screen_id, current_screen

    def _execute_from_current_screen(
        self,
        source_screen_id: str,
        source_live_screen: ScreenRecord | None,
        candidate: ActionCandidate,
        path_actions: list[str],
    ) -> tuple[TransitionRecord, str | None, ScreenRecord | None]:
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
                    action=candidate.action,
                    success=False,
                    action_label=candidate.label,
                    selection_reason=candidate.reason,
                    error_message=error_message,
                ),
                None,
                None,
            )

        destination_record = self._capture_screen(
            observation=observation,
            info=info,
            metadata={"via_action": candidate.action},
        )
        if not self._is_allowed_url(destination_record.url):
            logger.warning(
                "Blocked navigation outside allowed domains from screen=%s action=%s url=%s",
                source_screen_id,
                candidate.action,
                destination_record.url,
            )
            return (
                TransitionRecord(
                    from_screen_id=source_screen_id,
                    to_screen_id=source_screen_id,
                    action=candidate.action,
                    success=False,
                    action_label=candidate.label,
                    selection_reason=candidate.reason,
                    error_message=(
                        f"Navigated outside allowed domains: {destination_record.url}"
                    ),
                    metadata={
                        "blocked_url": destination_record.url,
                        "selected_by": candidate.selected_by,
                    },
                ),
                None,
                None,
            )
        destination_screen_id, destination_is_new, _ = self._register_screen(
            destination_record,
            path_actions=path_actions + [candidate.action],
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
                action=candidate.action,
                success=True,
                action_label=candidate.label,
                selection_reason=candidate.reason,
                error_message="",
                metadata={
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

    def _register_screen(
        self,
        screen: ScreenRecord,
        path_actions: list[str],
        annotate_if_new: bool = True,
    ) -> tuple[str, bool, ScreenRecord]:
        semantic_collapse_mode = "hard" if self.config.discovery.mode == "guided_bfs" else "soft"
        soft_revisit_screen_id = ""
        soft_revisit_canonical_screen: ScreenRecord | None = None
        is_new_candidate = not self.pipeline.graph.has_fingerprint(screen.fingerprint)
        if is_new_candidate and annotate_if_new:
            discovered_screens = list(self.pipeline.graph.screens_by_id.values())
            candidate_actions = self.policy.enumerate_actions(screen)
            for candidate in candidate_actions:
                candidate.seen_count = len(self.signature_seen_on_screens[candidate.signature])
            screen.action_candidates = candidate_actions
            memory_summary = self.memory.summarize_for_prompt(screen) if self.memory.visited_screen_order else ""
            revisit_match = self._resolve_semantic_revisit(
                screen=screen,
                candidate_actions=candidate_actions,
                discovered_screens=discovered_screens,
                memory_summary=memory_summary,
            )
            if revisit_match:
                matched_screen_id = revisit_match["matched_screen_id"]
                canonical_screen = self.pipeline.graph.get_screen(matched_screen_id)
                screen.metadata["semantic_revisit_of"] = matched_screen_id
                screen.metadata["semantic_revisit_reason"] = revisit_match["reason"]
                screen.metadata["semantic_revisit_confidence"] = float(revisit_match["confidence"])
                screen.metadata["semantic_revisit_difference_type"] = revisit_match["difference_type"]
                screen.metadata["semantic_revisit_mode"] = semantic_collapse_mode
                if semantic_collapse_mode == "hard":
                    self.pipeline.graph.alias_fingerprint_to_screen(screen.fingerprint, matched_screen_id)
                    self._cleanup_duplicate_capture(screen, canonical_screen)
                    self.memory.register_revisit(
                        canonical_screen=canonical_screen,
                        incoming_screen=screen,
                        reason=revisit_match["reason"],
                        confidence=float(revisit_match["confidence"]),
                    )
                    self.semantic_revisits += 1
                    logger.info(
                        "Collapsed semantic revisit incoming=%s url=%s -> existing=%s page_type=%s confidence=%.2f reason=%s",
                        screen.screen_id,
                        screen.url,
                        matched_screen_id,
                        canonical_screen.page_type or canonical_screen.title or canonical_screen.url,
                        float(revisit_match["confidence"]),
                        revisit_match["reason"],
                    )
                    return matched_screen_id, False, canonical_screen
                logger.info(
                    "Soft semantic revisit incoming=%s url=%s ~ existing=%s page_type=%s confidence=%.2f reason=%s",
                    screen.screen_id,
                    screen.url,
                    matched_screen_id,
                    canonical_screen.page_type or canonical_screen.title or canonical_screen.url,
                    float(revisit_match["confidence"]),
                    revisit_match["reason"],
                )
                soft_revisit_screen_id = matched_screen_id
                soft_revisit_canonical_screen = canonical_screen
            screen = self.llm.annotate_screen(
                screen=screen,
                candidate_actions=candidate_actions,
                path_actions=path_actions,
                discovered_screens=discovered_screens,
                memory_summary=memory_summary,
            )
            screen.recommended_actions = self.llm.rank_action_candidates(
                screen=screen,
                candidate_actions=candidate_actions,
                path_actions=path_actions,
                discovered_screens=discovered_screens,
                memory_summary=memory_summary,
            )
            screen.metadata["annotation_method"] = (
                "llm" if self.config.llm.use_for_screen_annotation and self.llm.is_available else "heuristic"
            )
            screen.metadata["ranking_method"] = (
                "llm" if self.config.llm.use_for_action_ranking and self.llm.is_available else "heuristic"
            )
            for candidate in screen.recommended_actions:
                candidate.seen_count = len(self.signature_seen_on_screens[candidate.signature])
            if soft_revisit_screen_id and soft_revisit_canonical_screen is not None:
                screen.metadata["canonical_screen_id"] = soft_revisit_screen_id
                screen.metadata["is_new_canonical_screen"] = False
                self._cleanup_duplicate_capture(screen, soft_revisit_canonical_screen)
                if not screen.metadata.get("_semantic_revisit_recorded"):
                    self.memory.register_revisit(
                        canonical_screen=soft_revisit_canonical_screen,
                        incoming_screen=screen,
                        reason=str(screen.metadata.get("semantic_revisit_reason", "")),
                        confidence=float(screen.metadata.get("semantic_revisit_confidence", 0.0)),
                    )
                    screen.metadata["_semantic_revisit_recorded"] = True
                    self.semantic_revisits += 1
                return soft_revisit_screen_id, False, soft_revisit_canonical_screen
        screen_id, is_new, canonical_screen = self.pipeline.register_screen(screen)
        screen.metadata["canonical_screen_id"] = screen_id
        screen.metadata["is_new_canonical_screen"] = is_new
        if is_new:
            for candidate in canonical_screen.action_candidates:
                self.signature_seen_on_screens[candidate.signature].add(screen_id)
            for candidate in canonical_screen.recommended_actions:
                candidate.seen_count = len(self.signature_seen_on_screens[candidate.signature])
        else:
            self._cleanup_duplicate_capture(screen, canonical_screen)
            if (
                screen.metadata.get("semantic_revisit_of")
                and not screen.metadata.get("_semantic_revisit_recorded")
            ):
                self.memory.register_revisit(
                    canonical_screen=canonical_screen,
                    incoming_screen=screen,
                    reason=str(screen.metadata.get("semantic_revisit_reason", "")),
                    confidence=float(screen.metadata.get("semantic_revisit_confidence", 0.0)),
                )
                screen.metadata["_semantic_revisit_recorded"] = True
                self.semantic_revisits += 1
            if screen.fingerprint != canonical_screen.fingerprint:
                logger.info(
                    "Live screen variant mapped to canonical=%s live=%s title=%s url=%s",
                    screen_id,
                    screen.screen_id,
                    screen.title or "(no title)",
                    screen.url,
                )
        self.memory.register_screen(canonical_screen, is_new=is_new)
        if is_new:
            logger.info(
                "Discovered screen=%s page_type=%s title=%s url=%s forms=%s candidates=%s recommended=%s",
                screen_id,
                canonical_screen.page_type or "Unclassified Page",
                canonical_screen.title or "(no title)",
                canonical_screen.url,
                len(canonical_screen.form_fields),
                len(canonical_screen.action_candidates),
                len(canonical_screen.recommended_actions),
            )
        else:
            logger.debug(
                "Merged into existing screen=%s title=%s url=%s",
                screen_id,
                canonical_screen.title or "(no title)",
                canonical_screen.url,
            )
        return screen_id, is_new, canonical_screen

    def _get_actions_for_screen(self, screen: ScreenRecord, depth: int) -> list[ActionCandidate]:
        if screen.recommended_actions:
            candidates = screen.recommended_actions
        elif screen.action_candidates:
            candidates = screen.action_candidates[: self.config.llm.selected_actions_per_screen]
        else:
            fallback_actions = self.policy.enumerate_actions(screen)
            for candidate in fallback_actions:
                self.signature_seen_on_screens[candidate.signature].add(screen.screen_id)
                candidate.seen_count = len(self.signature_seen_on_screens[candidate.signature])
            screen.action_candidates = fallback_actions
            screen.recommended_actions = self.llm.rank_action_candidates(
                screen=screen,
                candidate_actions=fallback_actions,
                path_actions=[],
                discovered_screens=list(self.pipeline.graph.screens_by_id.values()),
                memory_summary=self.memory.summarize_for_prompt(screen),
            )
            candidates = screen.recommended_actions or fallback_actions

        filtered = [candidate for candidate in candidates if not self._should_skip_candidate(candidate, depth)]
        if filtered:
            return self._prioritize_candidates(filtered)
        non_shared_main = [
            candidate for candidate in candidates if (not candidate.is_shared_chrome and candidate.zone == "main")
        ]
        if non_shared_main:
            return self._prioritize_candidates(non_shared_main)[:1]
        return []

    def _get_actions_for_agentic_screen(self, screen: ScreenRecord, depth: int) -> list[ActionCandidate]:
        if screen.recommended_actions:
            candidates = screen.recommended_actions[:]
        elif screen.action_candidates:
            candidates = screen.action_candidates[:]
        else:
            candidates = self.policy.enumerate_actions(screen)
            for candidate in candidates:
                self.signature_seen_on_screens[candidate.signature].add(screen.screen_id)
                candidate.seen_count = len(self.signature_seen_on_screens[candidate.signature])
            screen.action_candidates = candidates
            screen.recommended_actions = self.llm.rank_action_candidates(
                screen=screen,
                candidate_actions=candidates,
                path_actions=[],
                discovered_screens=list(self.pipeline.graph.screens_by_id.values()),
                memory_summary=self.memory.summarize_for_prompt(screen),
            )
            candidates = screen.recommended_actions or candidates

        prepared: list[ActionCandidate] = []
        main_candidates = [candidate for candidate in candidates if candidate.zone == "main"]
        shared_candidates = [candidate for candidate in candidates if candidate.zone != "main"]

        for candidate in main_candidates + shared_candidates:
            if candidate.zone == "footer":
                self.skipped_shared_chrome += 1
                continue
            if candidate.signature in self.memory.no_change_signatures and self.signature_attempt_count.get(candidate.signature, 0) >= 1:
                self.skipped_shared_chrome += 1
                continue
            if candidate.is_shared_chrome and depth >= 1 and candidate.signature in self.signature_success_targets:
                candidate.reason = candidate.reason or "Previously explored shared navigation action."
                candidate.novelty_score = min(candidate.novelty_score, 0.15) if candidate.novelty_score else 0.15
            prepared.append(candidate)
        return self._prioritize_candidates(prepared)

    def _should_skip_candidate(self, candidate: ActionCandidate, depth: int) -> bool:
        if not candidate.signature:
            return False
        if candidate.zone == "footer" and depth >= 0:
            self.skipped_shared_chrome += 1
            return True
        if candidate.signature in self.memory.no_change_signatures and self.signature_attempt_count.get(candidate.signature, 0) >= 1:
            self.skipped_shared_chrome += 1
            return True
        if not candidate.is_shared_chrome:
            return False
        if depth <= 0:
            return False
        known_targets = self.signature_success_targets.get(candidate.signature, set())
        if known_targets:
            self.skipped_shared_chrome += 1
            return True
        if self.signature_attempt_count.get(candidate.signature, 0) >= 2:
            self.skipped_shared_chrome += 1
            return True
        return False

    def _record_candidate_outcome(self, candidate: ActionCandidate, destination_screen_id: str) -> None:
        if not candidate.signature:
            return
        if destination_screen_id:
            self.signature_success_targets[candidate.signature].add(destination_screen_id)

    def _record_candidate_attempt(self, candidate: ActionCandidate) -> None:
        if not candidate.signature:
            return
        self.signature_attempt_count[candidate.signature] += 1

    def _prioritize_candidates(self, candidates: list[ActionCandidate]) -> list[ActionCandidate]:
        def score(item: ActionCandidate) -> tuple[float, int, int, str]:
            novelty = item.novelty_score or 0.0
            low_value_penalty = -1 if item.signature in self.memory.low_value_signatures else 0
            shared_penalty = -1 if item.is_shared_chrome else 0
            seen_penalty = -min(item.seen_count, 6)
            zone_bonus = 1 if item.zone == "main" else 0
            return (
                novelty + zone_bonus + low_value_penalty + shared_penalty,
                -item.seen_count,
                0 if item.zone == "main" else 1,
                item.action,
            )

        return sorted(candidates, key=score, reverse=True)

    def _resolve_semantic_revisit(
        self,
        screen: ScreenRecord,
        candidate_actions: list[ActionCandidate],
        discovered_screens: list[ScreenRecord],
        memory_summary: str,
    ) -> dict[str, Any] | None:
        revisit_candidates = self._find_revisit_candidates(screen, candidate_actions, discovered_screens)
        if not revisit_candidates:
            return None
        self.llm_revisit_checks += 1
        result = self.llm.judge_screen_revisit(
            current_screen=screen,
            candidate_screens=revisit_candidates,
            memory_summary=memory_summary,
        )
        if result.get("is_revisit") and result.get("matched_screen_id"):
            return result
        return None

    def _find_revisit_candidates(
        self,
        screen: ScreenRecord,
        candidate_actions: list[ActionCandidate],
        discovered_screens: list[ScreenRecord],
    ) -> list[ScreenRecord]:
        route = self._normalized_route(screen.url)
        title = (screen.title or "").strip().lower()
        form_signature = self._form_signature(screen)
        current_labels = self._candidate_label_set(candidate_actions)
        scored: list[tuple[int, ScreenRecord]] = []
        for candidate in discovered_screens:
            score = 0
            if self._normalized_route(candidate.url) == route:
                score += 5
            if title and title == (candidate.title or "").strip().lower():
                score += 3
            if form_signature and form_signature == self._form_signature(candidate):
                score += 4
            if current_labels:
                overlap = len(current_labels & self._candidate_label_set(candidate.action_candidates or candidate.recommended_actions))
                score += min(overlap, 4)
            if score >= 4:
                scored.append((score, candidate))
        scored.sort(key=lambda item: (-item[0], item[1].screen_id))
        return [item[1] for item in scored[: self.config.llm.revisit_candidate_limit]]

    def _cleanup_duplicate_capture(self, incoming_screen: ScreenRecord, canonical_screen: ScreenRecord) -> None:
        if incoming_screen.screen_id == canonical_screen.screen_id:
            return
        screenshot_path = incoming_screen.screenshot_path
        if screenshot_path and os.path.exists(screenshot_path):
            try:
                os.remove(screenshot_path)
                logger.debug("Removed duplicate screenshot for screen=%s path=%s", incoming_screen.screen_id, screenshot_path)
            except OSError:
                logger.debug("Could not remove duplicate screenshot path=%s", screenshot_path)

    def _normalized_route(self, url: str) -> str:
        try:
            parsed = urlparse(url)
        except Exception:
            return ""
        return parsed.path or "/"

    def _form_signature(self, screen: ScreenRecord) -> str:
        labels = []
        for field in screen.form_fields[:10]:
            label = (field.label or field.field_type).strip().lower()
            if label:
                labels.append(label)
        return "|".join(sorted(dict.fromkeys(labels)))

    def _candidate_label_set(self, candidates: list[ActionCandidate]) -> set[str]:
        labels: set[str] = set()
        for candidate in candidates[:12]:
            label = (candidate.label or candidate.raw_text or "").strip().lower()
            if label:
                labels.add(label)
        return labels

    def _within_limits(self) -> bool:
        if self.explored_actions >= self.config.discovery.max_steps:
            return False
        return self._elapsed_minutes() < self.config.discovery.time_budget_minutes

    def _elapsed_minutes(self) -> float:
        if not self.start_time_monotonic:
            return 0.0
        return (time.monotonic() - self.start_time_monotonic) / 60.0

    def _remaining_minutes(self) -> float:
        return max(0.0, float(self.config.discovery.time_budget_minutes) - self._elapsed_minutes())

    def _classify_transition_effect(
        self,
        source_screen: ScreenRecord,
        destination_screen: ScreenRecord,
        success: bool,
        destination_is_new: bool | None = None,
        semantic_revisit_of: str = "",
    ) -> str:
        if not success:
            return "failure"
        if source_screen.fingerprint == destination_screen.fingerprint:
            return "no_change"
        if semantic_revisit_of:
            return "known_page"
        if destination_is_new is False and source_screen.url == destination_screen.url and source_screen.title == destination_screen.title:
            return "minor_change"
        if destination_is_new is False:
            return "known_page"
        if source_screen.url == destination_screen.url and source_screen.title == destination_screen.title:
            return "minor_change"
        return "new_screen"

    def _apply_go_back_result(
        self,
        destination_screen_id: str,
        current_path_actions: list[str],
    ) -> tuple[str, list[str]]:
        if current_path_actions:
            current_path_actions = current_path_actions[:-1]
        return destination_screen_id, current_path_actions

    def _finalize_run(self, run_dir: Path) -> dict[str, Any]:
        graph_path = self.pipeline.export_graph()
        compact_graph_path = self.pipeline.export_compact_graph()
        page_overview_json_path, page_overview_md_path = self.pipeline.export_page_overviews()
        page_families_json_path, page_families_md_path = self.pipeline.export_page_families()
        sut_overview_json_path, sut_overview_md_path = self.pipeline.export_sut_overview()
        sut_dossier_json_path, sut_dossier_md_path = self.pipeline.export_sut_dossier()
        generated_tasks = self.pipeline.generate_tasks()
        task_plan_path = self.pipeline.export_task_plan()
        task_seeds_path = self.pipeline.export_task_seeds()
        task_dedup_report_path = self.pipeline.export_task_dedup_report()
        summary = {
            "run_dir": str(run_dir),
            "graph_path": str(graph_path),
            "compact_graph_path": str(compact_graph_path),
            "page_overview_json_path": str(page_overview_json_path),
            "page_overview_md_path": str(page_overview_md_path),
            "page_families_json_path": str(page_families_json_path),
            "page_families_md_path": str(page_families_md_path),
            "sut_overview_json_path": str(sut_overview_json_path),
            "sut_overview_md_path": str(sut_overview_md_path),
            "sut_dossier_json_path": str(sut_dossier_json_path),
            "sut_dossier_md_path": str(sut_dossier_md_path),
            "task_plan_path": str(task_plan_path),
            "task_seeds_path": str(task_seeds_path),
            "task_dedup_report_path": str(task_dedup_report_path),
            "generated_tasks": generated_tasks,
            "num_screens": len(self.pipeline.graph.screens_by_id),
            "num_page_families": len(self.pipeline.graph.get_page_families()),
            "num_transitions": len(self.pipeline.graph.transitions),
            "num_task_seeds": len(self.pipeline.last_task_seeds),
            "num_generated_tasks": len(generated_tasks),
            "explored_actions": self.explored_actions,
            "failed_actions": self.failed_actions,
            "failed_replays": self.failed_replays,
            "skipped_shared_chrome": self.skipped_shared_chrome,
            "semantic_revisits": self.semantic_revisits,
            "llm_revisit_checks": self.llm_revisit_checks,
            "elapsed_minutes": round(self._elapsed_minutes(), 3),
            "time_budget_minutes": self.config.discovery.time_budget_minutes,
            "mode": self.config.discovery.mode,
            "llm_enabled": self.config.llm.enabled,
            "llm_available": self.llm.is_available,
            "llm_availability_error": self.llm.availability_error,
        }
        self.pipeline.write_summary(summary)
        logger.info(
            "Discovery artifacts written: graph=%s compact_graph=%s page_overview=%s page_families=%s sut_overview=%s sut_dossier=%s task_plan=%s task_seeds=%s",
            graph_path,
            compact_graph_path,
            page_overview_json_path,
            page_families_json_path,
            sut_overview_json_path,
            sut_dossier_json_path,
            task_plan_path,
            task_seeds_path,
        )
        logger.info(
            "Discovery summary: screens=%s families=%s transitions=%s seeds=%s tasks=%s failed_actions=%s skipped_shared_chrome=%s semantic_revisits=%s llm_revisit_checks=%s elapsed=%.2fm",
            summary["num_screens"],
            summary["num_page_families"],
            summary["num_transitions"],
            summary["num_task_seeds"],
            summary["num_generated_tasks"],
            summary["failed_actions"],
            summary["skipped_shared_chrome"],
            summary["semantic_revisits"],
            summary["llm_revisit_checks"],
            summary["elapsed_minutes"],
        )
        return summary

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


def run_discovery_from_config_path(config_path: str | Path) -> dict[str, Any]:
    try:
        yaml = import_module("yaml")
    except ModuleNotFoundError as exc:
        raise DiscoveryDependencyError(
            "PyYAML is required to load discovery configs. "
            "Install the required packages from requirements.txt first."
        ) from exc
    config_data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    config = DiscoveryRunConfig.from_dict(config_data)
    logger.info("Loaded discovery config from %s", config_path)
    executor = DiscoveryExecutor(config)
    return executor.run()
