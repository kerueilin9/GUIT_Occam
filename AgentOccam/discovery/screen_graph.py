"""Screen graph for discovery runs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from AgentOccam.discovery.action_summary import summarize_transition_action
from AgentOccam.discovery.dossier import DiscoveryDossierBuilder, PageFamilyBundle
from AgentOccam.discovery.models import PageFamily, ScreenRecord, TaskSeed, TransitionRecord
from AgentOccam.logger import logger


@dataclass
class ParentLink:
    from_screen_id: str
    action: str


class ScreenGraph:
    """Stores deduplicated screens and the transitions between them."""

    def __init__(self) -> None:
        self.root_screen_id: str | None = None
        self.screens_by_id: dict[str, ScreenRecord] = {}
        self.transitions: list[TransitionRecord] = []
        self.screen_id_by_fingerprint: dict[str, str] = {}
        self.parent_by_screen_id: dict[str, ParentLink] = {}
        self._page_family_bundle: PageFamilyBundle | None = None

    def add_screen(self, screen: ScreenRecord) -> tuple[str, bool]:
        self._page_family_bundle = None
        existing_id = self.screen_id_by_fingerprint.get(screen.fingerprint)
        if existing_id:
            self._merge_screen(existing_id, screen)
            return existing_id, False

        self.screens_by_id[screen.screen_id] = screen
        self.screen_id_by_fingerprint[screen.fingerprint] = screen.screen_id
        if self.root_screen_id is None:
            self.root_screen_id = screen.screen_id
        return screen.screen_id, True

    def add_transition(self, transition: TransitionRecord) -> None:
        self._page_family_bundle = None
        self.transitions.append(transition)
        if (
            transition.success
            and transition.to_screen_id not in self.parent_by_screen_id
            and transition.from_screen_id != transition.to_screen_id
        ):
            self.parent_by_screen_id[transition.to_screen_id] = ParentLink(
                from_screen_id=transition.from_screen_id,
                action=summarize_transition_action(transition),
            )

    def get_screen(self, screen_id: str) -> ScreenRecord:
        return self.screens_by_id[screen_id]

    def has_fingerprint(self, fingerprint: str) -> bool:
        return fingerprint in self.screen_id_by_fingerprint

    def alias_fingerprint_to_screen(self, fingerprint: str, screen_id: str) -> None:
        if fingerprint and screen_id in self.screens_by_id:
            self.screen_id_by_fingerprint[fingerprint] = screen_id

    def build_path_to(self, screen_id: str) -> tuple[list[str], list[str]]:
        path_screen_ids = [screen_id]
        path_actions: list[str] = []
        cursor = screen_id
        while cursor != self.root_screen_id and cursor in self.parent_by_screen_id:
            link = self.parent_by_screen_id[cursor]
            path_actions.append(link.action)
            path_screen_ids.append(link.from_screen_id)
            cursor = link.from_screen_id

        path_screen_ids.reverse()
        path_actions.reverse()
        return path_screen_ids, path_actions

    def extract_task_seeds(
        self,
        max_seeds: int = 50,
        max_tasks_per_screen: int = 2,
    ) -> list[TaskSeed]:
        if not self.root_screen_id:
            return []
        selected = self._dossier_builder().build_task_plan(
            max_seeds=max_seeds,
            max_tasks_per_family=max(1, max_tasks_per_screen),
        )
        priority_counts = Counter(item.priority_bucket for item in selected)
        logger.info(
            "Selected %s task seeds with priority distribution=%s",
            len(selected),
            dict(priority_counts),
        )
        return selected

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_screen_id": self.root_screen_id,
            "screens": {
                screen_id: screen.to_dict()
                for screen_id, screen in self.screens_by_id.items()
            },
            "transitions": [transition.to_dict() for transition in self.transitions],
        }

    def to_compact_dict(self) -> dict[str, Any]:
        compact = self._dossier_builder().build_compact_graph(self._get_page_family_bundle())
        compact["root_screen_id"] = self.root_screen_id
        compact["num_screens"] = len(self.screens_by_id)
        return compact

    def build_page_overviews(self) -> list[dict[str, Any]]:
        return self._dossier_builder().build_page_overviews(self._get_page_family_bundle())

    def build_page_families(self) -> list[dict[str, Any]]:
        return self._dossier_builder().build_page_families(self._get_page_family_bundle())

    def build_sut_overview(self) -> dict[str, Any]:
        return self._dossier_builder().build_sut_overview(self._get_page_family_bundle())

    def build_sut_dossier(self, sut_name: str) -> dict[str, Any]:
        return self._dossier_builder().build_sut_dossier(
            sut_name=sut_name,
            bundle=self._get_page_family_bundle(),
        )

    def get_page_family(self, family_id: str) -> PageFamily:
        for family in self._get_page_family_bundle().families:
            if family.family_id == family_id:
                return family
        raise KeyError(f"Unknown page family id: {family_id}")

    def get_page_families(self) -> list[PageFamily]:
        return self._get_page_family_bundle().families[:]

    def _dossier_builder(self) -> DiscoveryDossierBuilder:
        return DiscoveryDossierBuilder(self)

    def _get_page_family_bundle(self) -> PageFamilyBundle:
        if self._page_family_bundle is None:
            self._page_family_bundle = self._dossier_builder().build_page_family_bundle()
        return self._page_family_bundle

    def _merge_screen(self, existing_id: str, incoming: ScreenRecord) -> None:
        existing = self.screens_by_id[existing_id]
        if not existing.screenshot_path and incoming.screenshot_path:
            existing.screenshot_path = incoming.screenshot_path
        if not existing.summary and incoming.summary:
            existing.summary = incoming.summary
        if not existing.page_type and incoming.page_type:
            existing.page_type = incoming.page_type
        if existing.write_risk == "unknown" and incoming.write_risk != "unknown":
            existing.write_risk = incoming.write_risk
        if not existing.key_entities and incoming.key_entities:
            existing.key_entities = incoming.key_entities[:]
        if not existing.domain_objects and incoming.domain_objects:
            existing.domain_objects = incoming.domain_objects[:]
        if not existing.form_fields and incoming.form_fields:
            existing.form_fields = incoming.form_fields[:]
        if not existing.forms_detected and incoming.forms_detected:
            existing.forms_detected = incoming.forms_detected[:]
        if not existing.task_opportunities and incoming.task_opportunities:
            existing.task_opportunities = incoming.task_opportunities[:]
        if not existing.important_dom and incoming.important_dom:
            existing.important_dom = incoming.important_dom[:]
        if not existing.secondary_dom and incoming.secondary_dom:
            existing.secondary_dom = incoming.secondary_dom[:]
        if not existing.action_candidates and incoming.action_candidates:
            existing.action_candidates = incoming.action_candidates[:]
        if not existing.recommended_actions and incoming.recommended_actions:
            existing.recommended_actions = incoming.recommended_actions[:]
        merged_metadata = dict(existing.metadata)
        merged_metadata.update(incoming.metadata)
        existing.metadata = merged_metadata
