"""Exploration control-flow helpers for the discovery runtime."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from AgentOccam.discovery.action_summary import (
    snapshot_candidate_for_path,
    summarize_path_steps,
)
from AgentOccam.discovery.models import ActionCandidate, ScreenRecord
from AgentOccam.logger import logger

REVEALED_BRANCH_SELECTED_BY_PREFIX = "revealed_branch::"
REVEALED_BRANCH_REOPEN_SELECTED_BY_PREFIX = "revealed_branch_reopen::"
MODAL_DISMISS_SELECTED_BY_PREFIX = "modal_dismiss::"
OBSERVATION_NODE_RE = re.compile(
    r"^(?P<indent>\t*)\[(?P<element_id>\d+)\]\s+(?P<role>[A-Za-z_]+)(?:\s+['\"](?P<label>[^'\"]*)['\"])?",
)
MODAL_ROLE_SET = {"dialog", "alertdialog"}
MODAL_DISMISS_LABEL_RE = re.compile(r"\b(close|cancel|dismiss)\b", re.IGNORECASE)
MODAL_CLOSE_ICON_RE = re.compile(r"^[xX×✕✖✗]$")
FORM_INPUT_ROLE_SET = {
    "checkbox",
    "combobox",
    "listbox",
    "radio",
    "radiobutton",
    "searchbox",
    "spinbutton",
    "switch",
    "textbox",
    "textarea",
}
FORM_SUBMIT_LABEL_RE = re.compile(
    r"\b(create|submit|save|confirm|approve|send)\b",
    re.IGNORECASE,
)
ICON_ONLY_LABEL_RE = re.compile(r"^(?:\\?u?f[0-9a-f]{3,5}|[^\w]+)$", re.IGNORECASE)
MAX_REVEALED_BRANCH_REOPENS = 3


class DiscoveryRuntimeStrategyMixin:
    def _run_agentic_session(self, run_dir: Path) -> dict[str, Any]:
        root_record = self._reset_and_capture()
        current_screen_id, _, current_screen = self._register_screen(root_record, path_actions=[])
        current_live_screen = root_record
        current_path_steps: list[ActionCandidate] = []
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
            current_path_actions = summarize_path_steps(current_path_steps)
            depth = len(current_path_steps)
            logger.debug(
                "Agentic loop canonical=%s live=%s depth=%s tried_actions=%s elapsed=%.2fm remaining=%.2fm",
                current_screen_id,
                current_screen.screen_id,
                depth,
                len(self.tried_actions_by_screen[current_screen_id]),
                self._elapsed_minutes(),
                self._remaining_minutes(),
            )

            candidates = self._get_actions_for_agentic_screen(
                current_screen,
                depth,
                current_path_actions=current_path_actions,
                canonical_screen_id=current_screen_id,
            )
            untried_candidates = [
                candidate
                for candidate in candidates
                if self._can_attempt_candidate(current_screen_id, candidate)
            ]

            if current_screen.metadata.get("can_go_back", False) and current_path_steps:
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

            priority_candidates = self._select_priority_candidates(untried_candidates)
            decision_candidates = priority_candidates or untried_candidates
            if priority_candidates:
                priority_reason = self._priority_candidate_reason(priority_candidates)
                logger.info(
                    "Prioritizing %s on canonical=%s live=%s actions=%s",
                    priority_reason,
                    current_screen_id,
                    current_screen.screen_id,
                    [item.action for item in priority_candidates],
                )

            if not untried_candidates:
                self.memory.mark_screen_done(canonical_screen, "No meaningful actions remain on this page.")
                logger.info(
                    "No remaining actions on live=%s canonical=%s title=%s; attempting go_back=%s",
                    current_screen.screen_id,
                    current_screen_id,
                    current_screen.title or "(no title)",
                    bool(current_path_steps and current_screen.metadata.get("can_go_back", False)),
                )
                if current_path_steps and current_screen.metadata.get("can_go_back", False):
                    back_candidate = ActionCandidate(
                        action="go_back",
                        role="navigation",
                        label="Go Back",
                        zone="navigation",
                        signature="navigation:go_back",
                        source="navigation",
                        selected_by="agentic",
                    )
                    self._mark_candidate_tried(current_screen_id, back_candidate)
                    self.explored_actions += 1
                    self._record_candidate_attempt(back_candidate)
                    transition, destination_screen_id, destination_live_screen = self._execute_from_current_screen(
                        source_screen_id=current_screen_id,
                        source_live_screen=current_screen,
                        candidate=back_candidate,
                        path_steps=current_path_steps,
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
                    self._record_adk_transition(
                        transition=transition,
                        effect=effect,
                        source_screen=current_screen,
                        destination_screen=destination_screen,
                    )
                    if transition.success and destination_live_screen is not None:
                        current_live_screen = destination_live_screen
                    current_screen_id, current_path_steps = self._apply_go_back_result(
                        destination_screen_id or current_screen_id,
                        current_path_steps,
                    )
                    continue
                break

            decision = self.llm.decide_next_action(
                screen=current_screen,
                candidate_actions=decision_candidates,
                memory_summary=self.memory.summarize_for_prompt(current_screen),
                elapsed_minutes=self._elapsed_minutes(),
                remaining_minutes=self._remaining_minutes(),
            )
            chosen_action = decision.get("action", "").strip()
            if decision.get("mark_screen_done") == "true":
                self.memory.mark_screen_done(
                    canonical_screen,
                    decision.get("reason", "Marked done by LLM"),
                )
            if chosen_action == "stop":
                logger.info(
                    "LLM requested stop on canonical=%s live=%s title=%s reason=%s",
                    current_screen_id,
                    current_screen.screen_id,
                    current_screen.title or "(no title)",
                    decision.get("reason", ""),
                )
                break

            candidate = next((item for item in decision_candidates if item.action == chosen_action), None)
            if candidate is None:
                candidate = next((item for item in decision_candidates if item.action != "go_back"), decision_candidates[0])
                logger.debug(
                    "LLM action fallback applied on canonical=%s live=%s requested=%s actual=%s",
                    current_screen_id,
                    current_screen.screen_id,
                    chosen_action,
                    candidate.action,
                )

            self._mark_candidate_tried(current_screen_id, candidate)
            self.explored_actions += 1
            self._record_candidate_attempt(candidate)
            self.memory.register_attempt(candidate)

            transition, destination_screen_id, destination_live_screen = self._execute_from_current_screen(
                source_screen_id=current_screen_id,
                source_live_screen=current_screen,
                candidate=candidate,
                path_steps=current_path_steps,
            )
            destination_screen = destination_live_screen or current_screen
            revealed_action_count = 0
            if transition.success and destination_live_screen is not None:
                revealed_action_count = self._register_revealed_action_branch(
                    source_screen_id=current_screen_id,
                    source_screen=current_screen,
                    destination_screen=destination_live_screen,
                    opener_candidate=candidate,
                    source_path_actions=current_path_actions,
                )
            effect = self._classify_transition_effect(
                source_screen=current_screen,
                destination_screen=destination_screen,
                success=transition.success,
                destination_is_new=transition.metadata.get("destination_is_new"),
                semantic_revisit_of=str(transition.metadata.get("semantic_revisit_of", "")),
                revealed_action_count=revealed_action_count,
            )
            self._record_revealed_branch_result(candidate, success=transition.success)
            transition.metadata["effect"] = effect
            transition.metadata["decision_reason"] = decision.get("reason", "")
            transition.metadata["revealed_action_count"] = revealed_action_count
            self.pipeline.register_transition(transition)
            self.memory.register_transition(
                source_screen=current_screen,
                destination_screen=destination_screen,
                candidate=candidate,
                effect=effect,
                success=transition.success,
            )
            self._record_adk_transition(
                transition=transition,
                effect=effect,
                source_screen=current_screen,
                destination_screen=destination_screen,
            )

            if not transition.success:
                self.failed_actions += 1
                continue

            if destination_live_screen is not None:
                current_live_screen = destination_live_screen

            if candidate.action == "go_back":
                current_screen_id, current_path_steps = self._apply_go_back_result(
                    destination_screen_id or current_screen_id,
                    current_path_steps,
                )
                continue

            if effect != "no_change":
                current_path_steps.append(snapshot_candidate_for_path(candidate))
            current_screen_id = destination_screen_id or current_screen_id

        return self._finalize_run(run_dir)

    def _get_actions_for_agentic_screen(
        self,
        screen: ScreenRecord,
        depth: int,
        current_path_actions: list[str],
        canonical_screen_id: str | None = None,
    ) -> list[ActionCandidate]:
        owner_screen_id = canonical_screen_id or str(
            screen.metadata.get("canonical_screen_id") or screen.screen_id
        )
        had_candidates = bool(screen.action_candidates)
        full_candidates = self._ensure_screen_action_candidates(
            screen=screen,
            owner_screen_id=owner_screen_id,
        )
        if screen.recommended_actions:
            candidates = screen.recommended_actions[:]
        elif had_candidates:
            candidates = full_candidates[:]
        else:
            screen.recommended_actions = self.llm.rank_action_candidates(
                screen=screen,
                candidate_actions=full_candidates,
                path_actions=current_path_actions,
                discovered_screens=list(self.pipeline.graph.screens_by_id.values()),
                memory_summary=self.memory.summarize_for_prompt(screen),
            )
            for candidate in screen.recommended_actions:
                if candidate.signature:
                    self.signature_seen_on_screens[candidate.signature].add(owner_screen_id)
                    candidate.seen_count = len(self.signature_seen_on_screens[candidate.signature])
            candidates = screen.recommended_actions or full_candidates

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
        modal_candidates = self._resolve_modal_dismiss_candidates(
            screen=screen,
            full_candidates=full_candidates,
        )
        if modal_candidates:
            return self._prioritize_candidates(modal_candidates)
        branch_candidates = self._resolve_revealed_branch_candidates(
            canonical_screen_id=owner_screen_id,
            full_candidates=full_candidates,
        )
        return self._prioritize_candidates(
            self._merge_priority_candidates(branch_candidates, prepared)
        )

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
            zone_bonus = 1 if item.zone == "main" else 0
            branch_bonus = 0
            if item.selected_by.startswith(REVEALED_BRANCH_SELECTED_BY_PREFIX):
                branch_bonus = 4
            elif item.selected_by.startswith(REVEALED_BRANCH_REOPEN_SELECTED_BY_PREFIX):
                branch_bonus = 3
            return (
                novelty + zone_bonus + low_value_penalty + shared_penalty + branch_bonus,
                -item.seen_count,
                0 if item.zone == "main" else 1,
                item.action,
            )

        return sorted(candidates, key=score, reverse=True)

    def _ensure_screen_action_candidates(
        self,
        screen: ScreenRecord,
        owner_screen_id: str,
    ) -> list[ActionCandidate]:
        if not screen.action_candidates:
            screen.action_candidates = self.policy.enumerate_actions(screen)
        screen.action_candidates = self._filter_candidate_actions_by_config(screen.action_candidates)
        for candidate in screen.action_candidates:
            if candidate.signature:
                self.signature_seen_on_screens[candidate.signature].add(owner_screen_id)
                candidate.seen_count = len(self.signature_seen_on_screens[candidate.signature])
        return screen.action_candidates[:]

    def _register_revealed_action_branch(
        self,
        source_screen_id: str,
        source_screen: ScreenRecord,
        destination_screen: ScreenRecord,
        opener_candidate: ActionCandidate,
        source_path_actions: list[str],
    ) -> int:
        opener_signature = self._candidate_identity(opener_candidate)
        if not opener_signature or opener_candidate.action == "go_back":
            return 0
        if self._branch_candidate_kind(opener_candidate)[0] == "child":
            return 0
        if self._normalized_page_url(source_screen.url) != self._normalized_page_url(destination_screen.url):
            return 0

        source_candidates = self._ensure_screen_action_candidates(source_screen, source_screen_id)
        destination_candidates = self._ensure_screen_action_candidates(destination_screen, source_screen_id)
        modal_element_ids, modal_titles = self._extract_modal_element_ids(destination_screen)
        if modal_element_ids:
            modal_viable_children = [
                item
                for item in destination_candidates
                if item.element_id in modal_element_ids
                and item.action != opener_candidate.action
                and self._is_revealed_branch_candidate_viable(
                    item,
                    modal_element_ids=modal_element_ids,
                )
            ]
            if not modal_viable_children:
                logger.info(
                    "Skipping modal branch registration on screen=%s opener=%s modal=%s because no modal child actions remain under current form permissions.",
                    source_screen_id,
                    opener_candidate.action,
                    modal_titles[:1] or ["dialog"],
                )
                return 0
        source_signatures = {
            identity
            for identity in (self._candidate_identity(item) for item in source_candidates)
            if identity
        }
        revealed_candidates = [
            item
            for item in destination_candidates
            if self._candidate_identity(item)
            and self._candidate_identity(item) not in source_signatures
            and item.action != opener_candidate.action
            and item.zone != "footer"
            and (item.label or item.raw_text)
            and self._is_revealed_branch_candidate_viable(
                item,
                modal_element_ids=modal_element_ids,
            )
        ]
        if len(revealed_candidates) < 2:
            if modal_element_ids:
                logger.info(
                    "Skipping modal branch registration on screen=%s opener=%s modal=%s because no actionable children remain under current form permissions.",
                    source_screen_id,
                    opener_candidate.action,
                    modal_titles[:1] or ["dialog"],
                )
            return 0

        group_id = f"{source_screen_id}|{opener_signature}"
        group = self.revealed_action_groups.get(group_id)
        if group is None:
            group = {
                "group_id": group_id,
                "source_screen_id": source_screen_id,
                "source_path_actions": source_path_actions[:],
                "source_url": source_screen.url,
                "source_title": source_screen.title,
                "opener_signature": opener_signature,
                "opener_label": opener_candidate.label or opener_candidate.raw_text or opener_candidate.action,
                "pending_signatures": [],
                "completed_signatures": set(),
            }
            self.revealed_action_groups[group_id] = group
            self.revealed_branches_detected += 1

        group["source_path_actions"] = source_path_actions[:]
        pending_signatures = set(group["pending_signatures"])
        completed_signatures = set(group["completed_signatures"])
        visible_remaining = 0
        for candidate in revealed_candidates:
            signature = self._candidate_identity(candidate)
            if not signature or signature in completed_signatures:
                continue
            visible_remaining += 1
            if signature in pending_signatures:
                continue
            group["pending_signatures"].append(signature)
            pending_signatures.add(signature)

        if visible_remaining > 0:
            logger.info(
                "Registered revealed branch on screen=%s opener=%s remaining=%s pending=%s",
                source_screen_id,
                opener_candidate.action,
                visible_remaining,
                [
                    signature
                    for signature in group["pending_signatures"]
                    if signature not in group["completed_signatures"]
                ],
            )
        return visible_remaining

    def _filter_candidate_actions_by_config(
        self,
        candidates: list[ActionCandidate],
    ) -> list[ActionCandidate]:
        return [
            candidate
            for candidate in candidates
            if self._is_candidate_allowed_by_discovery_config(candidate)
        ]

    def _is_candidate_allowed_by_discovery_config(
        self,
        candidate: ActionCandidate,
    ) -> bool:
        if candidate.action == "go_back":
            return True
        selected_by = candidate.selected_by or ""
        if selected_by.startswith(MODAL_DISMISS_SELECTED_BY_PREFIX):
            return True
        if (
            candidate.role in FORM_INPUT_ROLE_SET
            and not self.config.discovery.allow_form_fill
        ):
            return False
        if (
            self._looks_like_form_submit_candidate(candidate)
            and not self.config.discovery.allow_form_submit
        ):
            return False
        return True

    def _is_revealed_branch_candidate_viable(
        self,
        candidate: ActionCandidate,
        *,
        modal_element_ids: set[str],
    ) -> bool:
        if candidate.element_id not in modal_element_ids:
            return True
        if self._is_modal_dismiss_candidate(candidate):
            return False
        if self._looks_like_icon_only_modal_candidate(candidate):
            return False
        if (
            candidate.role in FORM_INPUT_ROLE_SET
            and not self.config.discovery.allow_form_fill
        ):
            return False
        candidate_text = " ".join(
            part.strip()
            for part in (candidate.label or "", candidate.raw_text or "")
            if part and part.strip()
        )
        if (
            self._looks_like_form_submit_candidate(candidate)
            and not self.config.discovery.allow_form_submit
        ):
            return False
        return True

    def _looks_like_form_submit_candidate(self, candidate: ActionCandidate) -> bool:
        candidate_text = " ".join(
            part.strip()
            for part in (candidate.label or "", candidate.raw_text or "")
            if part and part.strip()
        )
        return bool(candidate_text and FORM_SUBMIT_LABEL_RE.search(candidate_text))

    def _looks_like_icon_only_modal_candidate(self, candidate: ActionCandidate) -> bool:
        label = (candidate.label or "").strip()
        if not label:
            return True
        compact = re.sub(r"\s+", "", label)
        return bool(ICON_ONLY_LABEL_RE.match(compact))

    def _resolve_revealed_branch_candidates(
        self,
        canonical_screen_id: str,
        full_candidates: list[ActionCandidate],
    ) -> list[ActionCandidate]:
        if not self.revealed_action_groups:
            return []

        candidates_by_signature: dict[str, ActionCandidate] = {}
        for candidate in full_candidates:
            signature = self._candidate_identity(candidate)
            if signature and signature not in candidates_by_signature:
                candidates_by_signature[signature] = candidate

        injected: list[ActionCandidate] = []
        for group_id, group in list(self.revealed_action_groups.items()):
            if group.get("source_screen_id") != canonical_screen_id:
                continue
            remaining_signatures = [
                signature
                for signature in group.get("pending_signatures", [])
                if signature not in group.get("completed_signatures", set())
            ]
            if not remaining_signatures:
                self.revealed_action_groups.pop(group_id, None)
                continue

            visible_children = [
                candidates_by_signature[signature]
                for signature in remaining_signatures
                if signature in candidates_by_signature
            ]
            if visible_children:
                for candidate in visible_children:
                    injected.append(
                        self._clone_candidate(
                            candidate,
                            source="revealed_branch",
                            selected_by=f"{REVEALED_BRANCH_SELECTED_BY_PREFIX}{group_id}",
                            reason=(
                                candidate.reason
                                or "Harvest a newly revealed dropdown or overlay link before leaving this branch."
                            ),
                            novelty_score=max(candidate.novelty_score or 0.0, 0.98),
                            is_shared_chrome=False,
                        )
                    )
                continue

            opener_signature = str(group.get("opener_signature", ""))
            reopen_count = int(group.get("reopen_count", 0))
            if reopen_count >= MAX_REVEALED_BRANCH_REOPENS:
                logger.info(
                    "Dropping revealed branch group=%s opener=%s after %s reopen attempt(s).",
                    group_id,
                    group.get("opener_label", opener_signature),
                    reopen_count,
                )
                self.revealed_action_groups.pop(group_id, None)
                continue
            opener_candidate = candidates_by_signature.get(opener_signature)
            if opener_candidate is None:
                continue
            injected.append(
                self._clone_candidate(
                    opener_candidate,
                    source="revealed_branch_reopen",
                    selected_by=f"{REVEALED_BRANCH_REOPEN_SELECTED_BY_PREFIX}{group_id}",
                    reason=(
                        f'Reopen "{group.get("opener_label", opener_candidate.label or opener_candidate.action)}" '
                        "to continue harvesting revealed links."
                    ),
                    novelty_score=max(opener_candidate.novelty_score or 0.0, 0.95),
                    is_shared_chrome=False,
                )
            )
        return injected

    def _merge_priority_candidates(
        self,
        priority_candidates: list[ActionCandidate],
        candidates: list[ActionCandidate],
    ) -> list[ActionCandidate]:
        if not priority_candidates:
            return candidates
        merged: list[ActionCandidate] = []
        seen_actions: set[str] = set()
        for candidate in priority_candidates + candidates:
            if candidate.action in seen_actions:
                continue
            merged.append(candidate)
            seen_actions.add(candidate.action)
        return merged

    def _select_priority_candidates(
        self,
        candidates: list[ActionCandidate],
    ) -> list[ActionCandidate]:
        modal_dismissers = [
            candidate
            for candidate in candidates
            if candidate.selected_by.startswith(MODAL_DISMISS_SELECTED_BY_PREFIX)
        ]
        if modal_dismissers:
            return self._prioritize_candidates(modal_dismissers)
        revealed_children = [
            candidate
            for candidate in candidates
            if candidate.selected_by.startswith(REVEALED_BRANCH_SELECTED_BY_PREFIX)
        ]
        if revealed_children:
            return self._prioritize_candidates(revealed_children)
        revealed_reopeners = [
            candidate
            for candidate in candidates
            if candidate.selected_by.startswith(REVEALED_BRANCH_REOPEN_SELECTED_BY_PREFIX)
        ]
        if revealed_reopeners:
            return self._prioritize_candidates(revealed_reopeners)
        return []

    def _priority_candidate_reason(
        self,
        candidates: list[ActionCandidate],
    ) -> str:
        if any(
            candidate.selected_by.startswith(MODAL_DISMISS_SELECTED_BY_PREFIX)
            for candidate in candidates
        ):
            return "modal dismiss candidates"
        if any(
            candidate.selected_by.startswith(REVEALED_BRANCH_SELECTED_BY_PREFIX)
            for candidate in candidates
        ):
            return "revealed branch candidates"
        if any(
            candidate.selected_by.startswith(REVEALED_BRANCH_REOPEN_SELECTED_BY_PREFIX)
            for candidate in candidates
        ):
            return "revealed branch reopen candidates"
        return "priority candidates"

    def _record_revealed_branch_result(
        self,
        candidate: ActionCandidate,
        success: bool,
    ) -> None:
        branch_kind, group_id = self._branch_candidate_kind(candidate)
        if branch_kind == "reopen" and group_id:
            group = self.revealed_action_groups.get(group_id)
            if group is not None:
                group["reopen_count"] = int(group.get("reopen_count", 0)) + 1
                if not success:
                    group["failed_reopen_count"] = int(group.get("failed_reopen_count", 0)) + 1
            return
        if branch_kind != "child" or not success or not group_id:
            return
        group = self.revealed_action_groups.get(group_id)
        if group is None:
            return
        signature = self._candidate_identity(candidate)
        if not signature:
            return
        group["completed_signatures"].add(signature)
        self.revealed_branch_actions_completed += 1
        remaining_signatures = [
            item
            for item in group.get("pending_signatures", [])
            if item not in group.get("completed_signatures", set())
        ]
        if remaining_signatures:
            logger.info(
                "Harvested revealed branch action group=%s action=%s remaining=%s",
                group_id,
                candidate.action,
                remaining_signatures,
            )
            return
        self.revealed_action_groups.pop(group_id, None)
        logger.info(
            "Completed revealed branch group=%s opener=%s",
            group_id,
            group.get("opener_label", ""),
        )

    def _resolve_modal_dismiss_candidates(
        self,
        screen: ScreenRecord,
        full_candidates: list[ActionCandidate],
    ) -> list[ActionCandidate]:
        modal_element_ids, modal_titles = self._extract_modal_element_ids(screen)
        if not modal_element_ids:
            return []

        dismiss_candidates: list[ActionCandidate] = []
        for candidate in full_candidates:
            if candidate.element_id not in modal_element_ids:
                continue
            if not self._is_modal_dismiss_candidate(candidate):
                continue
            dismiss_candidates.append(
                self._clone_candidate(
                    candidate,
                    source="modal_dismiss",
                    selected_by=f"{MODAL_DISMISS_SELECTED_BY_PREFIX}{screen.screen_id}",
                    reason=self._modal_dismiss_reason(candidate, modal_titles),
                    novelty_score=max(candidate.novelty_score or 0.0, 1.0),
                    is_shared_chrome=False,
                )
            )

        if dismiss_candidates:
            logger.info(
                "Modal detected on live=%s title=%s dismiss_candidates=%s",
                screen.screen_id,
                modal_titles[:2],
                [item.action for item in dismiss_candidates],
            )
        return dismiss_candidates

    def _extract_modal_element_ids(
        self,
        screen: ScreenRecord,
    ) -> tuple[set[str], list[str]]:
        modal_depths: list[int] = []
        modal_element_ids: set[str] = set()
        modal_titles: list[str] = []

        for raw_line in screen.observation_text.splitlines():
            match = OBSERVATION_NODE_RE.match(raw_line)
            if match is None:
                continue
            depth = len(match.group("indent") or "")
            while modal_depths and depth <= modal_depths[-1]:
                modal_depths.pop()

            role = (match.group("role") or "").lower()
            element_id = str(match.group("element_id") or "").strip()
            label = (match.group("label") or "").strip()
            if role in MODAL_ROLE_SET:
                modal_depths.append(depth)
                if label:
                    modal_titles.append(label)
                continue

            if modal_depths and element_id:
                modal_element_ids.add(element_id)

        return modal_element_ids, modal_titles

    def _is_modal_dismiss_candidate(self, candidate: ActionCandidate) -> bool:
        label = (candidate.label or "").strip()
        raw_text = (candidate.raw_text or "").strip()
        if label and MODAL_DISMISS_LABEL_RE.search(label):
            return True
        if raw_text and MODAL_DISMISS_LABEL_RE.search(raw_text):
            return True
        if label and MODAL_CLOSE_ICON_RE.match(label):
            return True
        return False

    def _modal_dismiss_reason(
        self,
        candidate: ActionCandidate,
        modal_titles: list[str],
    ) -> str:
        modal_name = modal_titles[0] if modal_titles else "the modal dialog"
        action_label = candidate.label or candidate.raw_text or candidate.action
        return (
            f'Close "{modal_name}" via "{action_label}" before interacting with the background page.'
        )

    def _can_attempt_candidate(
        self,
        canonical_screen_id: str,
        candidate: ActionCandidate,
    ) -> bool:
        if candidate.selected_by.startswith(MODAL_DISMISS_SELECTED_BY_PREFIX):
            return True
        tried_signatures = self.tried_signatures_by_screen[canonical_screen_id]
        if (
            candidate.signature
            and candidate.signature in tried_signatures
            and not self._is_signature_repeat_allowed(candidate)
        ):
            return False
        if candidate.action not in self.tried_actions_by_screen[canonical_screen_id]:
            return True
        branch_kind, group_id = self._branch_candidate_kind(candidate)
        if branch_kind != "reopen" or not group_id:
            return False
        group = self.revealed_action_groups.get(group_id)
        if group is None:
            return False
        return any(
            signature not in group.get("completed_signatures", set())
            for signature in group.get("pending_signatures", [])
        )

    def _mark_candidate_tried(
        self,
        canonical_screen_id: str,
        candidate: ActionCandidate,
    ) -> None:
        self.tried_actions_by_screen[canonical_screen_id].add(candidate.action)
        if candidate.signature and not self._is_signature_repeat_allowed(candidate):
            self.tried_signatures_by_screen[canonical_screen_id].add(candidate.signature)

    def _is_signature_repeat_allowed(self, candidate: ActionCandidate) -> bool:
        selected_by = candidate.selected_by or ""
        if selected_by.startswith(MODAL_DISMISS_SELECTED_BY_PREFIX):
            return True
        if selected_by.startswith(REVEALED_BRANCH_REOPEN_SELECTED_BY_PREFIX):
            return True
        return False

    def _branch_candidate_kind(self, candidate: ActionCandidate) -> tuple[str, str]:
        selected_by = candidate.selected_by or ""
        if selected_by.startswith(REVEALED_BRANCH_SELECTED_BY_PREFIX):
            return "child", selected_by[len(REVEALED_BRANCH_SELECTED_BY_PREFIX) :]
        if selected_by.startswith(REVEALED_BRANCH_REOPEN_SELECTED_BY_PREFIX):
            return "reopen", selected_by[len(REVEALED_BRANCH_REOPEN_SELECTED_BY_PREFIX) :]
        return "", ""

    def _candidate_identity(self, candidate: ActionCandidate) -> str:
        if candidate.signature:
            return candidate.signature
        label = (candidate.label or candidate.raw_text or candidate.action).strip().lower()
        return f"{candidate.zone}:{candidate.role}:{label}".strip(":")

    def _clone_candidate(
        self,
        candidate: ActionCandidate,
        *,
        source: str | None = None,
        selected_by: str | None = None,
        reason: str | None = None,
        novelty_score: float | None = None,
        is_shared_chrome: bool | None = None,
    ) -> ActionCandidate:
        return ActionCandidate(
            action=candidate.action,
            element_id=candidate.element_id,
            role=candidate.role,
            label=candidate.label,
            raw_text=candidate.raw_text,
            source=source if source is not None else candidate.source,
            zone=candidate.zone,
            signature=candidate.signature,
            is_shared_chrome=(
                is_shared_chrome
                if is_shared_chrome is not None
                else candidate.is_shared_chrome
            ),
            seen_count=candidate.seen_count,
            reason=reason if reason is not None else candidate.reason,
            novelty_score=(
                novelty_score
                if novelty_score is not None
                else candidate.novelty_score
            ),
            selected_by=selected_by if selected_by is not None else candidate.selected_by,
        )

    def _normalized_page_url(self, url: str) -> str:
        try:
            parts = urlsplit(url)
        except Exception:
            return url
        path = parts.path or "/"
        return f"{parts.scheme}://{parts.netloc}{path}"

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
        revealed_action_count: int = 0,
    ) -> str:
        if not success:
            return "failure"
        if revealed_action_count > 0:
            return "revealed_actions"
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
        current_path_steps: list[ActionCandidate],
    ) -> tuple[str, list[ActionCandidate]]:
        if current_path_steps:
            current_path_steps = current_path_steps[:-1]
        return destination_screen_id, current_path_steps

    def _finalize_run(self, run_dir: Path) -> dict[str, Any]:
        graph_path = self.pipeline.export_graph()
        compact_graph_path = self.pipeline.export_compact_graph()
        page_overview_json_path, page_overview_md_path = self.pipeline.export_page_overviews()
        page_families_json_path, page_families_md_path = self.pipeline.export_page_families()
        sut_overview_json_path, sut_overview_md_path = self.pipeline.export_sut_overview()
        sut_dossier_json_path, sut_dossier_md_path = self.pipeline.export_sut_dossier()
        generated_tasks = self.pipeline.generate_tasks()
        self.adk_client.record_task_plan(self.pipeline.last_task_seeds)
        self.adk_client.record_generated_tasks(generated_tasks)
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
            "skipped_shared_chrome": self.skipped_shared_chrome,
            "semantic_revisits": self.semantic_revisits,
            "llm_revisit_checks": self.llm_revisit_checks,
            "revealed_branches_detected": self.revealed_branches_detected,
            "revealed_branch_actions_completed": self.revealed_branch_actions_completed,
            "elapsed_minutes": round(self._elapsed_minutes(), 3),
            "time_budget_minutes": self.config.discovery.time_budget_minutes,
            "mode": self.config.discovery.mode,
            "llm_enabled": self.config.llm.enabled,
            "llm_available": self.llm.is_available,
            "llm_availability_error": self.llm.availability_error,
            "adk_enabled": self.adk_client.enabled,
            "adk_available": self.adk_client.available,
            "adk_availability_error": self.adk_client.availability_error,
        }
        self.adk_client.record_summary(summary)
        adk_snapshot_path = self.adk_client.export_snapshot(run_dir)
        summary["adk_snapshot_path"] = str(adk_snapshot_path) if adk_snapshot_path else ""
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
