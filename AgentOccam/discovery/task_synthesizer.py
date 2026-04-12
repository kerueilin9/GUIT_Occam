"""Task synthesis for discovered navigation and workflow seeds."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from AgentOccam.adk_discovery import ADKDiscoveryClient
from AgentOccam.discovery.llm_helpers import (
    DiscoveryLLMCoordinator,
    extract_json_payload,
    load_json_if_exists,
    truncate_text,
)
from AgentOccam.discovery.models import CandidateTask, DiscoveryRunConfig, GeneratedTask, TaskSeed
from AgentOccam.discovery.prompt_templates import render_discovery_prompt
from AgentOccam.discovery.screen_graph import ScreenGraph
from AgentOccam.logger import logger

CLICK_RE = re.compile(r"click \[(?P<element_id>\d+)\]")
TYPE_RE = re.compile(r"type \[(?P<element_id>\d+)\] \[(?P<value>.*)\] \[(?P<enter>[01])\]")
GOTO_RE = re.compile(r"goto \[(?P<url>.*)\] \[(?P<new_tab>[01])\]")
CLICK_SUMMARY_RE = re.compile(r'^Click "(?P<label>.+?)"(?: \((?P<zone>[^/()]+)\/(?P<role>[^)]+)\))?$')


class TaskSynthesizer:
    """Creates task_absence-style tasks from discovery seeds."""

    def __init__(
        self,
        config: DiscoveryRunConfig,
        graph: ScreenGraph,
        adk_client: ADKDiscoveryClient | None = None,
    ) -> None:
        self.config = config
        self.graph = graph
        self.llm = DiscoveryLLMCoordinator(config, adk_client=adk_client)
        self.reference_task_template = self._load_reference_task_template()
        self.reference_task_corpus = self._load_reference_task_corpus()
        self.page_families = {
            family.family_id: family
            for family in self.graph.get_page_families()
        }
        self.last_dedup_report: dict[str, object] = {
            "input_candidate_count": 0,
            "kept_count": 0,
            "dropped": [],
        }

    def synthesize(self, seeds: list[TaskSeed]) -> list[GeneratedTask]:
        generated: list[GeneratedTask] = []
        task_counter = 1
        logger.info(
            "Starting task synthesis: seeds=%s max_tasks=%s tasks_per_seed=%s",
            len(seeds),
            self.config.task_generation.max_tasks,
            self.config.llm.tasks_per_seed,
        )

        first_pass_limits = {seed.seed_id: 1 for seed in seeds}
        generated.extend(
            self._synthesize_pass(
                seeds=seeds,
                per_seed_limits=first_pass_limits,
                start_index=task_counter,
            )
        )
        task_counter = len(generated) + 1

        extra_per_seed = max(0, self.config.llm.tasks_per_seed - 1)
        if extra_per_seed > 0 and len(generated) < self.config.task_generation.max_tasks:
            second_pass_limits = {seed.seed_id: extra_per_seed for seed in seeds}
            generated.extend(
                self._synthesize_pass(
                    seeds=seeds,
                    per_seed_limits=second_pass_limits,
                    start_index=task_counter,
                    already_generated=generated,
                )
            )
        deduped = self._deduplicate_generated_tasks(generated)
        logger.info(
            "Task synthesis finished: generated=%s deduped=%s",
            len(generated),
            len(deduped),
        )
        return deduped

    def _synthesize_pass(
        self,
        seeds: list[TaskSeed],
        per_seed_limits: dict[str, int],
        start_index: int,
        already_generated: list[GeneratedTask] | None = None,
    ) -> list[GeneratedTask]:
        generated: list[GeneratedTask] = []
        task_counter = start_index
        existing = already_generated or []
        for seed in seeds:
            if len(existing) + len(generated) >= self.config.task_generation.max_tasks:
                break
            seed_limit = max(0, per_seed_limits.get(seed.seed_id, 0))
            if seed_limit == 0:
                continue

            remaining = self.config.task_generation.max_tasks - len(existing) - len(generated)
            limit = min(seed_limit, remaining)
            llm_generated: list[GeneratedTask] = []
            if self.config.llm.use_for_task_generation and self.llm.can_generate_tasks():
                try:
                    logger.info(
                        "Generating tasks for seed=%s priority=%s target=%s limit=%s",
                        seed.seed_id,
                        seed.priority_bucket,
                        seed.target_screen_id,
                        limit,
                    )
                    llm_generated = self._generate_with_llm(
                        seed=seed,
                        start_index=task_counter,
                        limit=limit,
                    )
                except Exception:
                    logger.exception(
                        "LLM task generation failed for seed=%s; falling back to deterministic task synthesis.",
                        seed.seed_id,
                    )
                    llm_generated = []

            if llm_generated:
                generated.extend(llm_generated[:limit])
                task_counter += len(llm_generated[:limit])
                continue

            candidate = self._build_fallback_candidate(seed, index=task_counter)
            logger.info(
                "Using fallback task synthesis for seed=%s task_id=%s",
                seed.seed_id,
                candidate.task_id,
            )
            generated.append(GeneratedTask(seed=seed, candidate=candidate))
            task_counter += 1
        return generated

    def _generate_with_llm(
        self,
        seed: TaskSeed,
        start_index: int,
        limit: int,
    ) -> list[GeneratedTask]:
        target_screen = self.graph.get_screen(seed.target_screen_id)
        target_family = self._get_family(seed)
        reference_examples = self._select_reference_examples(seed)
        prompt = render_discovery_prompt(
            "task_generation",
            limit=limit,
            sut_name=self.config.sut.name,
            start_url=self.config.sut.start_url,
            requires_login=bool(self.config.sut.storage_state),
            seed_title=seed.title,
            seed_task_idea=seed.task_idea,
            seed_task_type=seed.task_type,
            seed_priority_bucket=seed.priority_bucket,
            seed_operation_type=seed.operation_type or "unspecified",
            seed_preferred_eval_type=seed.preferred_eval_type,
            seed_coverage_key=seed.coverage_key,
            seed_domain_object=seed.domain_object or "unknown",
            seed_rationale=seed.rationale,
            seed_path_actions=seed.path_actions,
            seed_planner_notes=seed.planner_notes,
            target_page_family=(
                seed.target_family_name
                or (target_family.name if target_family else seed.target_page_type)
            ),
            target_page_type=seed.target_page_type,
            target_route_patterns=seed.target_route_patterns,
            target_forms=seed.target_forms,
            target_summary=seed.target_screen_summary,
            family_supported_operations=seed.family_supported_operations,
            acceptance_hints=seed.acceptance_hints,
            target_family_dossier=json.dumps(
                target_family.to_dict() if target_family else {},
                ensure_ascii=False,
                indent=2,
            ),
            target_url=target_screen.url,
            target_title=target_screen.title,
            target_screen_summary=target_screen.summary,
            target_form_fields=[
                {
                    "label": field.label,
                    "type": field.field_type,
                    "required": field.required,
                }
                for field in target_screen.form_fields[:12]
            ],
            target_task_opportunities=target_screen.task_opportunities,
            target_important_dom=target_screen.important_dom,
            target_secondary_dom=target_screen.secondary_dom,
            accessibility_tree_excerpt=truncate_text(
                target_screen.observation_text,
                self.config.llm.task_generation_context_chars,
            ),
            reference_examples=(
                json.dumps(reference_examples, ensure_ascii=False, indent=2)
                if reference_examples
                else "No reference examples provided."
            ),
            reference_task_template=(
                json.dumps(self.reference_task_template, ensure_ascii=False, indent=2)
                if self.reference_task_template
                else "No canonical reference example provided."
            ),
        )
        response = self.llm.task_call(prompt)
        payload = extract_json_payload(response)
        raw_tasks = payload.get("tasks", [])
        generated: list[GeneratedTask] = []
        for offset, raw_task in enumerate(raw_tasks, start=0):
            task_id = f"{self.config.sut.name}_generated_{start_index + offset:03d}"
            candidate = self._candidate_from_llm_payload(seed, raw_task, task_id=task_id)
            candidate = self._postprocess_candidate(seed, candidate)
            generated.append(GeneratedTask(seed=seed, candidate=candidate))
        logger.info(
            "LLM generated %s task(s) for seed=%s target=%s",
            len(generated),
            seed.seed_id,
            seed.target_screen_id,
        )
        return generated

    def _candidate_from_llm_payload(
        self,
        seed: TaskSeed,
        raw_task: dict,
        task_id: str,
    ) -> CandidateTask:
        task_type = str(raw_task.get("task_type", seed.task_type or "workflow")).strip() or "workflow"
        given = self._normalize_step_list(raw_task.get("given")) or self._default_given(task_type)
        when = self._normalize_step_list(raw_task.get("when")) or self._describe_path(seed)
        then = self._normalize_step_list(raw_task.get("then")) or self._default_then(seed)
        eval_type = str(raw_task.get("eval_type", seed.preferred_eval_type or self.config.task_generation.eval_mode)).strip() or (seed.preferred_eval_type or self.config.task_generation.eval_mode)
        if eval_type not in {"gherkin_criteria", "llm_judge"}:
            eval_type = seed.preferred_eval_type or self.config.task_generation.eval_mode
        review_notes = self._normalize_step_list(raw_task.get("review_notes")) or [
            "Generated by discovery task generator.",
        ]
        reference_criteria = self._normalize_step_list(raw_task.get("reference_acceptance_criteria")) or then[:]
        feature = str(raw_task.get("feature", self._feature_name(seed))).strip()
        scenario = str(raw_task.get("scenario", seed.title or seed.task_idea or task_id)).strip()

        task_config = {
            "sites": [self.config.sut.name],
            "task_id": task_id,
            "require_login": bool(self.config.sut.storage_state),
            "storage_state": self.config.sut.storage_state,
            "start_url": self.config.sut.start_url,
            "geolocation": None,
            "gherkin": {
                "feature": feature,
                "scenario": scenario,
                "given": given,
                "when": when,
                "then": then,
            },
            "require_reset": False,
            "eval": {"eval_types": [eval_type]},
        }
        if eval_type == "gherkin_criteria":
            task_config["eval"]["reference_answers"] = {
                "gherkin_acceptance_criteria": reference_criteria,
            }

        return CandidateTask(
            task_id=task_id,
            task_type=task_type,
            source_seed_id=seed.seed_id,
            task_config=task_config,
            confidence=self._coerce_float(raw_task.get("confidence", 0.5)),
            generation_method="llm",
            review_notes=review_notes,
        )

    def _build_fallback_candidate(self, seed: TaskSeed, index: int) -> CandidateTask:
        task_id = f"{self.config.sut.name}_generated_{index:03d}"
        when_steps = self._default_when(seed)
        then_steps = self._default_then(seed)
        task_type = seed.task_type or "navigation"
        feature = self._feature_name(seed)
        scenario = seed.title or seed.target_family_name or self._target_name(self.graph.get_screen(seed.target_screen_id))

        task_config = {
            "sites": [self.config.sut.name],
            "task_id": task_id,
            "require_login": bool(self.config.sut.storage_state),
            "storage_state": self.config.sut.storage_state,
            "start_url": self.config.sut.start_url,
            "geolocation": None,
            "gherkin": {
                "feature": feature,
                "scenario": scenario,
                "given": self._default_given(task_type),
                "when": when_steps,
                "then": then_steps,
            },
            "require_reset": False,
            "eval": {"eval_types": [seed.preferred_eval_type or self.config.task_generation.eval_mode]},
        }
        if (seed.preferred_eval_type or self.config.task_generation.eval_mode) == "gherkin_criteria":
            task_config["eval"]["reference_answers"] = {
                "gherkin_acceptance_criteria": then_steps[:],
            }

        return CandidateTask(
            task_id=task_id,
            task_type=task_type,
            source_seed_id=seed.seed_id,
            task_config=task_config,
            confidence=0.35,
            generation_method="deterministic",
            review_notes=[
                "Fallback task synthesized without LLM task generation.",
                "Prefer replay validation before promoting to benchmark tasks.",
            ],
        )

    def _describe_path(self, seed: TaskSeed) -> list[str]:
        steps: list[str] = []
        for source_screen_id, action in zip(seed.path_screen_ids, seed.path_actions):
            source_screen = self.graph.get_screen(source_screen_id)
            steps.append(self._describe_action(source_screen, action))
        if not steps:
            steps.append("I review the discovered page")
        return steps

    def _describe_action(self, source_screen, action: str) -> str:
        summary_match = CLICK_SUMMARY_RE.match(action)
        if summary_match:
            return f'I click on "{summary_match.group("label")}"'

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

        if action in {"go_back", "Go back"}:
            return "I go back to the previous page"

        if action.startswith("scroll"):
            direction = "down" if "down" in action else "up"
            return f"I scroll {direction}"

        return f'I perform the action "{action}"'

    def _default_given(self, task_type: str) -> list[str]:
        if self.config.sut.storage_state:
            return ["I am logged in to the application"]
        if task_type == "navigation":
            return ["I am on the application start page"]
        return ["I am on the application start page"]

    def _default_when(self, seed: TaskSeed) -> list[str]:
        family = self._get_family(seed)
        family_name = seed.target_family_name or (family.name if family else seed.target_page_type) or "target page"
        if seed.priority_bucket == "navigation":
            return [f'I navigate through the application to the "{family_name}" page']
        if seed.operation_type in {"create", "request", "submit"}:
            object_name = seed.domain_object or family_name.lower()
            return [
                f'I navigate to the "{family_name}" page',
                f'I fill in the form for the {object_name} workflow',
                f'I submit the {object_name} form',
            ]
        if seed.operation_type == "edit":
            object_name = seed.domain_object or family_name.lower()
            return [
                f'I navigate to the "{family_name}" page',
                f'I update the {object_name} details',
                "I save the changes",
            ]
        if seed.operation_type == "delete":
            object_name = seed.domain_object or family_name.lower()
            return [
                f'I navigate to the "{family_name}" page',
                f'I remove or revoke the selected {object_name}',
            ]
        if seed.operation_type == "import":
            object_name = seed.domain_object or family_name.lower()
            return [
                f'I navigate to the "{family_name}" page',
                f'I start the import workflow for {object_name}',
            ]
        if seed.operation_type == "filter":
            return [
                f'I navigate to the "{family_name}" page',
                "I apply a meaningful filter to narrow the results",
            ]
        if seed.operation_type == "search":
            return [
                f'I navigate to the "{family_name}" page',
                "I search for a relevant record",
            ]
        if seed.operation_type == "export":
            return [
                f'I navigate to the "{family_name}" page',
                "I trigger the export action",
            ]
        return [
            f'I navigate to the "{family_name}" page',
            "I interact with the main workflow on the page",
        ]

    def _default_then(self, seed: TaskSeed) -> list[str]:
        family = self._get_family(seed)
        if seed.acceptance_hints:
            return seed.acceptance_hints[:]
        if family:
            criteria: list[str] = []
            if family.summary:
                criteria.append(family.summary)
            route = next((item for item in seed.target_route_patterns if item and item != "/"), "")
            if route:
                tail = route.rstrip("/").split("/")[-1]
                if tail and tail != ":id":
                    criteria.append(f'The URL should contain "{tail}"')
            if family.name:
                criteria.append(f'The page should display "{family.name}"')
            for dom_item in family.important_dom[:2]:
                criteria.append(f'I should see {dom_item} on the page')
            if family.form_fields:
                criteria.append(f'I should see field "{family.form_fields[0].label}" on the page')
            return self._normalize_step_list(criteria)[:4]

        target_screen = self.graph.get_screen(seed.target_screen_id)
        criteria: list[str] = []
        if target_screen.title:
            criteria.append(f'The page title should contain "{target_screen.title}"')
        parsed_target = urlparse(target_screen.url)
        path = parsed_target.path.rstrip("/")
        if path:
            criteria.append(f'The URL should contain "{path}"')
        if target_screen.summary:
            criteria.append(target_screen.summary)
        if not criteria:
            criteria.append("The target screen should be visible")
        return criteria[:3]

    def _feature_name(self, seed: TaskSeed) -> str:
        object_name = (seed.domain_object or "").strip()
        if seed.priority_bucket == "navigation":
            return f"{self.config.sut.name} Navigation"
        if object_name:
            return f"{self.config.sut.name} {object_name.title()} Workflow"
        if seed.target_family_name:
            return f"{self.config.sut.name} {seed.target_family_name} Workflow"
        return f"{self.config.sut.name} Discovered Workflow"

    def _get_family(self, seed: TaskSeed):
        if seed.target_family_id:
            return self.page_families.get(seed.target_family_id)
        return None

    def _postprocess_candidate(self, seed: TaskSeed, candidate: CandidateTask) -> CandidateTask:
        gherkin = candidate.task_config.setdefault("gherkin", {})
        if not gherkin.get("given"):
            gherkin["given"] = self._default_given(seed.task_type or "workflow")
        if seed.priority_bucket == "navigation":
            gherkin["when"] = self._default_when(seed)
            gherkin["then"] = self._default_then(seed)
            candidate.task_config["eval"] = {
                "eval_types": ["gherkin_criteria"],
                "reference_answers": {
                    "gherkin_acceptance_criteria": gherkin["then"][:],
                },
            }
        else:
            if not gherkin.get("when"):
                gherkin["when"] = self._default_when(seed)
            if not gherkin.get("then"):
                gherkin["then"] = self._default_then(seed)
            eval_type = seed.preferred_eval_type or self.config.task_generation.eval_mode
            if eval_type == "llm_judge":
                candidate.task_config["eval"] = {"eval_types": ["llm_judge"]}
            else:
                candidate.task_config["eval"] = {
                    "eval_types": ["gherkin_criteria"],
                    "reference_answers": {
                        "gherkin_acceptance_criteria": gherkin["then"][:],
                    },
                }
        gherkin["feature"] = gherkin.get("feature") or self._feature_name(seed)
        gherkin["scenario"] = gherkin.get("scenario") or seed.title or candidate.task_id
        return candidate

    def _deduplicate_generated_tasks(self, generated: list[GeneratedTask]) -> list[GeneratedTask]:
        kept: list[GeneratedTask] = []
        seen_primary: dict[tuple[str, str], GeneratedTask] = {}
        seen_secondary: dict[str, GeneratedTask] = {}
        dropped: list[dict[str, object]] = []

        for item in generated:
            primary_key = (
                item.seed.target_family_id or item.seed.target_screen_id,
                item.seed.operation_type or item.seed.priority_bucket,
            )
            secondary_key = self._normalize_signature(
                [
                    item.candidate.task_config.get("gherkin", {}).get("scenario", ""),
                    " ".join(item.candidate.task_config.get("gherkin", {}).get("when", [])),
                    " ".join(item.candidate.task_config.get("gherkin", {}).get("then", [])),
                ]
            )
            existing = seen_primary.get(primary_key) or seen_secondary.get(secondary_key)
            if existing is None:
                seen_primary[primary_key] = item
                seen_secondary[secondary_key] = item
                kept.append(item)
                continue
            if item.candidate.confidence > existing.candidate.confidence:
                kept.remove(existing)
                old_secondary_key = self._normalize_signature(
                    [
                        existing.candidate.task_config.get("gherkin", {}).get("scenario", ""),
                        " ".join(existing.candidate.task_config.get("gherkin", {}).get("when", [])),
                        " ".join(existing.candidate.task_config.get("gherkin", {}).get("then", [])),
                    ]
                )
                seen_secondary.pop(old_secondary_key, None)
                kept.append(item)
                seen_primary[primary_key] = item
                seen_secondary[secondary_key] = item
                dropped.append(
                    {
                        "dropped_task_id": existing.candidate.task_id,
                        "kept_task_id": item.candidate.task_id,
                        "reason": "Higher-confidence task replaced a duplicate family/operation task.",
                    }
                )
                continue
            dropped.append(
                {
                    "dropped_task_id": item.candidate.task_id,
                    "kept_task_id": existing.candidate.task_id,
                    "reason": "Duplicate family/operation task was removed during post-generation dedupe.",
                }
            )

        self.last_dedup_report = {
            "input_candidate_count": len(generated),
            "kept_count": len(kept),
            "dropped": dropped,
        }
        return kept

    def _normalize_signature(self, parts: list[str]) -> str:
        return re.sub(r"[^a-z0-9]+", " ", " ".join(parts).lower()).strip()

    def _lookup_label(self, source_screen, element_id: str) -> str:
        for element in source_screen.interactive_elements:
            if element.element_id == element_id:
                return element.label
        for candidate in source_screen.action_candidates:
            if candidate.element_id == element_id:
                return candidate.label
        return ""

    def _target_name(self, target_screen) -> str:
        if target_screen.title:
            return target_screen.title

        parsed = urlparse(target_screen.url)
        tail = parsed.path.rstrip("/").split("/")[-1]
        if tail:
            return tail.replace("-", " ").replace("_", " ")

        return target_screen.url

    def _normalize_step_list(self, value) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        return [str(value).strip()]

    def _coerce_float(self, value) -> float:
        try:
            number = float(value)
        except Exception:
            return 0.5
        return max(0.0, min(1.0, number))

    def _load_reference_task_template(self) -> dict | None:
        configured_path = self.config.task_generation.reference_task_path
        if configured_path:
            return load_json_if_exists(configured_path)

        inferred_path = Path("config_files") / self.config.sut.name / "task_absence01.json"
        if inferred_path.exists():
            return load_json_if_exists(str(inferred_path))
        return None

    def _load_reference_task_corpus(self) -> list[dict]:
        task_dir = Path("config_files") / self.config.sut.name
        if not task_dir.exists():
            logger.warning("Reference task directory not found: %s", task_dir)
            return []

        corpus: list[dict] = []
        for task_path in sorted(task_dir.glob("*.json")):
            raw = load_json_if_exists(str(task_path))
            if not raw:
                continue
            corpus.append(
                {
                    "task_id": raw.get("task_id", task_path.stem),
                    "feature": raw.get("gherkin", {}).get("feature", ""),
                    "scenario": raw.get("gherkin", {}).get("scenario", ""),
                    "when": raw.get("gherkin", {}).get("when", []),
                    "then": raw.get("gherkin", {}).get("then", []),
                    "eval_type": (raw.get("eval", {}).get("eval_types") or ["gherkin_criteria"])[0],
                    "path": str(task_path),
                }
            )
        logger.info("Loaded %s reference task example(s) from %s", len(corpus), task_dir)
        return corpus

    def _select_reference_examples(self, seed: TaskSeed) -> list[dict]:
        if not self.reference_task_corpus:
            return []
        ranked = sorted(
            self.reference_task_corpus,
            key=lambda item: self._reference_similarity(seed, item),
            reverse=True,
        )
        return ranked[: self.config.task_generation.max_reference_examples]

    def _reference_similarity(self, seed: TaskSeed, item: dict) -> tuple[int, int]:
        seed_text = " ".join(
            [
                seed.task_type,
                seed.priority_bucket,
                seed.operation_type,
                seed.task_idea,
                seed.target_page_type,
                seed.target_family_name,
                seed.domain_object,
                " ".join(seed.target_forms),
                " ".join(seed.family_supported_operations),
            ]
        ).lower()
        ref_text = " ".join(
            [
                item.get("feature", ""),
                item.get("scenario", ""),
                " ".join(item.get("when", [])),
                " ".join(item.get("then", [])),
                item.get("eval_type", ""),
            ]
        ).lower()
        overlap = sum(1 for token in self._seed_tokens(seed_text) if token in ref_text)
        eval_bonus = 2 if item.get("eval_type") == (seed.preferred_eval_type or self.config.task_generation.eval_mode) else 0
        nav_bonus = 1 if seed.priority_bucket == "navigation" and "navigate" in ref_text else 0
        crud_bonus = 2 if seed.priority_bucket == "crud_form" and any(token in ref_text for token in ("save", "submit", "update", "add", "edit")) else 0
        return (overlap + eval_bonus + nav_bonus + crud_bonus, len(item.get("when", [])))

    def _seed_tokens(self, text: str) -> list[str]:
        return [
            token
            for token in re.split(r"[^a-z0-9]+", text)
            if len(token) >= 3
        ]
