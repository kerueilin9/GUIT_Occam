"""Screen registration and revisit helpers for the discovery runtime."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from AgentOccam.discovery.models import ActionCandidate, ScreenRecord, TransitionRecord
from AgentOccam.logger import logger


class DiscoveryRuntimeScreeningMixin:
    def _register_screen(
        self,
        screen: ScreenRecord,
        path_actions: list[str],
        annotate_if_new: bool = True,
    ) -> tuple[str, bool, ScreenRecord]:
        soft_revisit_screen_id = ""
        soft_revisit_canonical_screen: ScreenRecord | None = None
        is_new_candidate = not self.pipeline.graph.has_fingerprint(screen.fingerprint)
        if is_new_candidate and annotate_if_new:
            discovered_screens = list(self.pipeline.graph.screens_by_id.values())
            candidate_actions = self._filter_candidate_actions_by_config(
                self.policy.enumerate_actions(screen)
            )
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
                screen.metadata["semantic_revisit_mode"] = "soft"
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
                self._record_adk_screen(
                    canonical_screen=soft_revisit_canonical_screen,
                    is_new=False,
                    path_actions=path_actions,
                    live_screen=screen,
                )
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
        self._record_adk_screen(
            canonical_screen=canonical_screen,
            is_new=is_new,
            path_actions=path_actions,
            live_screen=screen,
        )
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

    def _record_adk_screen(
        self,
        canonical_screen: ScreenRecord,
        is_new: bool,
        path_actions: list[str],
        live_screen: ScreenRecord | None = None,
    ) -> None:
        self.adk_client.record_screen(
            screen=canonical_screen,
            is_new=is_new,
            path_actions=path_actions,
            live_screen_id=live_screen.screen_id if live_screen is not None else canonical_screen.screen_id,
            semantic_revisit_of=str((live_screen or canonical_screen).metadata.get("semantic_revisit_of", "")),
        )
        self.adk_client.record_memory(
            self.memory,
            current_screen=canonical_screen,
        )

    def _record_adk_transition(
        self,
        transition: TransitionRecord,
        effect: str,
        source_screen: ScreenRecord,
        destination_screen: ScreenRecord,
    ) -> None:
        self.adk_client.record_transition(
            transition=transition,
            effect=effect,
            source_screen=source_screen,
            destination_screen=destination_screen,
        )
        self.adk_client.record_memory(
            self.memory,
            current_screen=destination_screen,
        )

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
