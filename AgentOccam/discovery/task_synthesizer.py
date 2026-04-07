"""Draft task synthesis for discovered navigation paths."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from AgentOccam.discovery.models import CandidateTask, DiscoveryRunConfig, GeneratedTask, TaskSeed
from AgentOccam.discovery.screen_graph import ScreenGraph

CLICK_RE = re.compile(r"click \[(?P<element_id>\d+)\]")
TYPE_RE = re.compile(r"type \[(?P<element_id>\d+)\] \[(?P<value>.*)\] \[(?P<enter>[01])\]")
GOTO_RE = re.compile(r"goto \[(?P<url>.*)\] \[(?P<new_tab>[01])\]")


class TaskSynthesizer:
    """Creates deterministic draft tasks from discovery seeds."""

    def __init__(self, config: DiscoveryRunConfig, graph: ScreenGraph) -> None:
        self.config = config
        self.graph = graph

    def synthesize(self, seeds: list[TaskSeed]) -> list[GeneratedTask]:
        generated: list[GeneratedTask] = []
        for index, seed in enumerate(seeds, start=1):
            if seed.task_type != "navigation":
                continue
            candidate = self._build_navigation_candidate(seed, index=index)
            generated.append(GeneratedTask(seed=seed, candidate=candidate))
        return generated

    def _build_navigation_candidate(self, seed: TaskSeed, index: int) -> CandidateTask:
        target_screen = self.graph.get_screen(seed.target_screen_id)
        scenario_name = self._target_name(target_screen)
        when_steps = self._describe_path(seed)
        then_steps = self._infer_acceptance_criteria(target_screen)

        task_id = f"{self.config.sut.name}_generated_{index:03d}"
        task_config = {
            "sites": [self.config.sut.name],
            "task_id": task_id,
            "require_login": bool(self.config.sut.storage_state),
            "storage_state": self.config.sut.storage_state,
            "start_url": self.config.sut.start_url,
            "gherkin": {
                "feature": f"{self.config.sut.name} discovered navigation flow",
                "scenario": f"Reach {scenario_name}",
                "given": ["I am on the application start page"],
                "when": when_steps,
                "then": then_steps,
            },
            "require_reset": False,
            "eval": {"eval_types": [self.config.task_generation.eval_mode]},
        }
        return CandidateTask(
            task_id=task_id,
            task_type="navigation",
            source_seed_id=seed.seed_id,
            task_config=task_config,
            confidence=0.4,
            review_notes=[
                "Draft synthesized from discovery graph.",
                "Prefer replay validation before promoting to benchmark tasks.",
            ],
        )

    def _describe_path(self, seed: TaskSeed) -> list[str]:
        steps: list[str] = []
        for source_screen_id, action in zip(seed.path_screen_ids, seed.path_actions):
            source_screen = self.graph.get_screen(source_screen_id)
            steps.append(self._describe_action(source_screen, action))
        return steps

    def _describe_action(self, source_screen, action: str) -> str:
        click_match = CLICK_RE.match(action)
        if click_match:
            element_id = click_match.group("element_id")
            label = self._lookup_label(source_screen, element_id)
            if label:
                return f'I click on "{label}"'
            return f"I click on element [{element_id}]"

        type_match = TYPE_RE.match(action)
        if type_match:
            element_id = type_match.group("element_id")
            label = self._lookup_label(source_screen, element_id) or f"element [{element_id}]"
            return f'I type into "{label}"'

        goto_match = GOTO_RE.match(action)
        if goto_match:
            return f'I navigate to "{goto_match.group("url")}"'

        if action == "go_back":
            return "I go back to the previous page"

        if action.startswith("scroll"):
            direction = "down" if "down" in action else "up"
            return f"I scroll {direction}"

        return f'I perform the action "{action}"'

    def _infer_acceptance_criteria(self, target_screen) -> list[str]:
        criteria: list[str] = []
        if target_screen.title:
            criteria.append(f'The page title should contain "{target_screen.title}"')

        parsed_target = urlparse(target_screen.url)
        path = parsed_target.path.rstrip("/")
        if path:
            criteria.append(f'The URL should contain "{path}"')

        visible_labels = [item.label for item in target_screen.interactive_elements if item.label][:2]
        for label in visible_labels:
            criteria.append(f'I should see "{label}" on the page')

        if not criteria:
            criteria.append("The target screen should be visible")

        return criteria[:3]

    def _lookup_label(self, source_screen, element_id: str) -> str:
        for element in source_screen.interactive_elements:
            if element.element_id == element_id:
                return element.label
        return ""

    def _target_name(self, target_screen) -> str:
        if target_screen.title:
            return target_screen.title

        parsed = urlparse(target_screen.url)
        tail = parsed.path.rstrip("/").split("/")[-1]
        if tail:
            return tail.replace("-", " ").replace("_", " ")

        return target_screen.url
