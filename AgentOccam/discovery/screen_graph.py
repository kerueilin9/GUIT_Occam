"""Screen graph for discovery runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from AgentOccam.discovery.models import ScreenRecord, TaskSeed, TransitionRecord


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

    def add_screen(self, screen: ScreenRecord) -> tuple[str, bool]:
        existing_id = self.screen_id_by_fingerprint.get(screen.fingerprint)
        if existing_id:
            return existing_id, False

        self.screens_by_id[screen.screen_id] = screen
        self.screen_id_by_fingerprint[screen.fingerprint] = screen.screen_id
        if self.root_screen_id is None:
            self.root_screen_id = screen.screen_id
        return screen.screen_id, True

    def add_transition(self, transition: TransitionRecord) -> None:
        self.transitions.append(transition)
        if transition.success and transition.to_screen_id not in self.parent_by_screen_id:
            if transition.from_screen_id != transition.to_screen_id:
                self.parent_by_screen_id[transition.to_screen_id] = ParentLink(
                    from_screen_id=transition.from_screen_id,
                    action=transition.action,
                )

    def get_screen(self, screen_id: str) -> ScreenRecord:
        return self.screens_by_id[screen_id]

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

    def extract_navigation_seeds(self, max_seeds: int = 20) -> list[TaskSeed]:
        if not self.root_screen_id:
            return []

        seeds: list[TaskSeed] = []
        root_screen = self.screens_by_id[self.root_screen_id]
        candidate_ids = [
            screen_id
            for screen_id in self.screens_by_id
            if screen_id != self.root_screen_id
        ]

        for index, screen_id in enumerate(candidate_ids, start=1):
            target = self.screens_by_id[screen_id]
            path_screen_ids, path_actions = self.build_path_to(screen_id)
            if not path_actions:
                continue

            if target.url == root_screen.url and target.title == root_screen.title:
                continue

            seed = TaskSeed(
                seed_id=f"seed_{index:03d}",
                task_type="navigation",
                entry_screen_id=self.root_screen_id,
                target_screen_id=screen_id,
                path_screen_ids=path_screen_ids,
                path_actions=path_actions,
                rationale=f"Reach discovered screen '{target.title or target.url}'.",
            )
            seeds.append(seed)
            if len(seeds) >= max_seeds:
                break

        return seeds

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_screen_id": self.root_screen_id,
            "screens": {
                screen_id: screen.to_dict()
                for screen_id, screen in self.screens_by_id.items()
            },
            "transitions": [transition.to_dict() for transition in self.transitions],
        }
