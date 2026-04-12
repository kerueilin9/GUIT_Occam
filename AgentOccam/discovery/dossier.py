"""Family-level consolidation and task planning for discovery outputs."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from AgentOccam.discovery.action_summary import summarize_transition_action
from AgentOccam.discovery.models import FormField, PageFamily, PageFamilyEdge, TaskSeed

if TYPE_CHECKING:
    from AgentOccam.discovery.screen_graph import ScreenGraph


WRITE_OPERATION_PRIORITY = [
    "create",
    "request",
    "submit",
    "edit",
    "delete",
    "import",
]
SECONDARY_OPERATION_PRIORITY = [
    "review",
    "detail",
    "filter",
    "search",
    "export",
    "view",
]
ALL_OPERATION_PRIORITY = WRITE_OPERATION_PRIORITY + SECONDARY_OPERATION_PRIORITY
RISK_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
OBJECT_SYNONYMS = {
    "absence (leave request)": "absence request",
    "timeoffrequest": "absence request",
    "time off request": "absence request",
    "employee profile": "employee",
    "csv data": "employee import",
}


@dataclass
class PageFamilyBundle:
    root_family_id: str | None
    families: list[PageFamily]
    edges: list[PageFamilyEdge]
    screen_to_family_id: dict[str, str]


@dataclass
class _PlannedSeed:
    family_id: str
    canonical_screen_id: str
    priority_bucket: str
    operation_type: str
    task_type: str
    title: str
    task_idea: str
    coverage_key: str
    domain_object: str
    preferred_eval_type: str
    acceptance_hints: list[str]
    path_screen_ids: list[str]
    path_actions: list[str]
    rationale: str
    source_screen_summary: str
    target_screen_summary: str
    target_page_type: str
    target_forms: list[str]
    target_route_patterns: list[str]
    family_supported_operations: list[str]
    evidence_screen_ids: list[str]
    planner_notes: list[str]


class DiscoveryDossierBuilder:
    """Builds family-level discovery artifacts and task plans."""

    def __init__(self, graph: "ScreenGraph") -> None:
        self.graph = graph

    def build_page_family_bundle(self) -> PageFamilyBundle:
        grouped: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
        for screen in self.graph.screens_by_id.values():
            grouped[self._family_key(screen)].append(screen)

        families: list[PageFamily] = []
        screen_to_family_id: dict[str, str] = {}
        ordered_groups = sorted(
            grouped.values(),
            key=lambda members: min(member.screen_id for member in members),
        )
        for index, members in enumerate(ordered_groups, start=1):
            family_id = f"family_{index:03d}"
            family = self._build_family(family_id, members)
            families.append(family)
            for member in members:
                screen_to_family_id[member.screen_id] = family_id

        edges = self._build_family_edges(screen_to_family_id)
        root_family_id = screen_to_family_id.get(self.graph.root_screen_id or "")
        return PageFamilyBundle(
            root_family_id=root_family_id,
            families=families,
            edges=edges,
            screen_to_family_id=screen_to_family_id,
        )

    def build_compact_graph(self, bundle: PageFamilyBundle | None = None) -> dict[str, Any]:
        bundle = bundle or self.build_page_family_bundle()
        return {
            "root_family_id": bundle.root_family_id,
            "num_page_families": len(bundle.families),
            "num_successful_edges": len(bundle.edges),
            "page_families": [self._family_digest(item) for item in bundle.families],
            "transitions": [edge.to_dict() for edge in bundle.edges],
        }

    def build_page_overviews(self, bundle: PageFamilyBundle | None = None) -> list[dict[str, Any]]:
        bundle = bundle or self.build_page_family_bundle()
        return [self._family_digest(item) for item in bundle.families]

    def build_page_families(self, bundle: PageFamilyBundle | None = None) -> list[dict[str, Any]]:
        bundle = bundle or self.build_page_family_bundle()
        return [family.to_dict() for family in bundle.families]

    def build_sut_overview(self, bundle: PageFamilyBundle | None = None) -> dict[str, Any]:
        bundle = bundle or self.build_page_family_bundle()
        families = bundle.families
        page_type_counts = Counter(family.page_type or family.name for family in families)
        object_counts = Counter(
            item
            for family in families
            for item in family.domain_objects
        )
        capability_counts = Counter(
            item
            for family in families
            for item in family.supported_operations
        )
        purpose = self._infer_sut_purpose(families)
        core_capabilities = self._dedupe(
            opportunity
            for family in families
            for opportunity in family.task_opportunities
        )[:18]
        form_pages = [
            {
                "family_id": family.family_id,
                "page_name": family.name,
                "page_type": family.page_type,
                "route_patterns": family.route_patterns[:],
                "form_field_count": len(family.form_fields),
                "fields": [
                    {
                        "label": field.label,
                        "type": field.field_type,
                        "required": field.required,
                    }
                    for field in family.form_fields[:10]
                ],
            }
            for family in families
            if family.form_fields
        ]
        return {
            "purpose": purpose,
            "page_family_count": len(families),
            "screen_count": len(self.graph.screens_by_id),
            "transition_count": len(self.graph.transitions),
            "feature_areas": [
                {"page_type": name, "count": count}
                for name, count in page_type_counts.most_common(14)
            ],
            "domain_objects": [
                {"name": name, "count": count}
                for name, count in object_counts.most_common(14)
            ],
            "operation_coverage": [
                {"operation": name, "count": count}
                for name, count in capability_counts.most_common(14)
            ],
            "core_capabilities": core_capabilities,
            "form_pages": form_pages[:14],
            "overview_text": self._build_overview_text(
                purpose=purpose,
                page_type_counts=page_type_counts,
                object_counts=object_counts,
                capability_counts=capability_counts,
            ),
        }

    def build_sut_dossier(
        self,
        sut_name: str,
        bundle: PageFamilyBundle | None = None,
    ) -> dict[str, Any]:
        bundle = bundle or self.build_page_family_bundle()
        overview = self.build_sut_overview(bundle)
        return {
            "sut_name": sut_name,
            "purpose": overview["purpose"],
            "overview_text": overview["overview_text"],
            "page_family_count": len(bundle.families),
            "family_graph": self.build_compact_graph(bundle),
            "page_families": [family.to_dict() for family in bundle.families],
            "core_capabilities": overview["core_capabilities"],
            "domain_objects": overview["domain_objects"],
            "operation_coverage": overview["operation_coverage"],
        }

    def build_task_plan(
        self,
        max_seeds: int = 50,
        max_tasks_per_family: int = 3,
    ) -> list[TaskSeed]:
        bundle = self.build_page_family_bundle()
        if not bundle.root_family_id:
            return []

        root_screen = self.graph.screens_by_id.get(self.graph.root_screen_id or "")
        root_summary = root_screen.summary if root_screen else ""
        candidates = self._build_seed_candidates(bundle, root_summary=root_summary)
        selected = self._select_diverse_candidates(
            candidates=candidates,
            bundle=bundle,
            max_seeds=max_seeds,
            max_tasks_per_family=max_tasks_per_family,
        )
        seeds: list[TaskSeed] = []
        for index, item in enumerate(selected, start=1):
            seeds.append(
                TaskSeed(
                    seed_id=f"seed_{index:03d}",
                    task_type=item.task_type,
                    priority_bucket=item.priority_bucket,
                    entry_screen_id=self.graph.root_screen_id or "",
                    target_screen_id=item.canonical_screen_id,
                    target_family_id=item.family_id,
                    target_family_name=self._family_name(item.family_id, bundle),
                    operation_type=item.operation_type,
                    title=item.title,
                    task_idea=item.task_idea,
                    coverage_key=item.coverage_key,
                    domain_object=item.domain_object,
                    preferred_eval_type=item.preferred_eval_type,
                    acceptance_hints=item.acceptance_hints,
                    path_screen_ids=item.path_screen_ids,
                    path_actions=item.path_actions,
                    rationale=item.rationale,
                    source_screen_summary=item.source_screen_summary,
                    target_screen_summary=item.target_screen_summary,
                    target_page_type=item.target_page_type,
                    target_forms=item.target_forms,
                    target_route_patterns=item.target_route_patterns,
                    family_supported_operations=item.family_supported_operations,
                    evidence_screen_ids=item.evidence_screen_ids,
                    planner_notes=item.planner_notes,
                )
            )
        return seeds

    def _build_family(self, family_id: str, members: list[Any]) -> PageFamily:
        canonical = self._pick_canonical_screen(members)
        route_patterns = self._dedupe(self._normalized_path(member.url) for member in members)
        url_examples = self._dedupe(member.url for member in members)[:6]
        domain_objects = self._merge_domain_objects(members)
        task_opportunities = self._dedupe(
            opportunity
            for member in members
            for opportunity in member.task_opportunities
        )[:10]
        important_dom = self._dedupe(
            item
            for member in members
            for item in member.important_dom
        )[:12]
        secondary_dom = self._dedupe(
            item
            for member in members
            for item in member.secondary_dom
        )[:12]
        form_fields = self._merge_form_fields(members)
        forms_detected = self._dedupe(
            item
            for member in members
            for item in member.forms_detected
        )
        variant_summaries = self._dedupe(
            self._variant_summary(member)
            for member in members
            if member.screen_id != canonical.screen_id
        )[:8]
        supported_operations = self._infer_supported_operations(
            canonical=canonical,
            members=members,
            form_fields=form_fields,
            task_opportunities=task_opportunities,
            important_dom=important_dom,
        )
        name = canonical.page_type or canonical.title or self._display_name_from_route(route_patterns[0] if route_patterns else canonical.url)
        purpose = canonical.summary or self._purpose_from_family(
            name=name,
            operations=supported_operations,
            domain_objects=domain_objects,
        )
        return PageFamily(
            family_id=family_id,
            canonical_screen_id=canonical.screen_id,
            name=name,
            page_type=canonical.page_type or name,
            summary=canonical.summary,
            purpose=purpose,
            route_patterns=route_patterns,
            url_examples=url_examples,
            member_screen_ids=[member.screen_id for member in sorted(members, key=lambda item: item.screen_id)],
            domain_objects=domain_objects,
            supported_operations=supported_operations,
            task_opportunities=task_opportunities,
            important_dom=important_dom,
            secondary_dom=secondary_dom,
            form_fields=form_fields,
            forms_detected=forms_detected,
            variant_summaries=variant_summaries,
            screenshot_path=canonical.screenshot_path,
            write_risk=self._highest_risk(member.write_risk for member in members),
            metadata={
                "screen_count": len(members),
                "canonical_title": canonical.title,
                "canonical_url": canonical.url,
                "has_form": bool(form_fields),
                "member_page_types": self._dedupe(member.page_type for member in members if member.page_type)[:8],
                "member_titles": self._dedupe(member.title for member in members if member.title)[:8],
            },
        )

    def _build_family_edges(self, screen_to_family_id: dict[str, str]) -> list[PageFamilyEdge]:
        edge_map: dict[tuple[str, str], dict[str, Any]] = {}
        for transition in self.graph.transitions:
            if not transition.success:
                continue
            from_family_id = screen_to_family_id.get(transition.from_screen_id)
            to_family_id = screen_to_family_id.get(transition.to_screen_id)
            if not from_family_id or not to_family_id or from_family_id == to_family_id:
                continue
            bucket = edge_map.setdefault(
                (from_family_id, to_family_id),
                {
                    "labels": Counter(),
                    "actions": Counter(),
                    "effects": Counter(),
                    "count": 0,
                },
            )
            bucket["count"] += 1
            if transition.action_label:
                bucket["labels"][transition.action_label] += 1
            bucket["actions"][summarize_transition_action(transition)] += 1
            effect = str(transition.metadata.get("effect", "")).strip()
            if effect:
                bucket["effects"][effect] += 1
        edges: list[PageFamilyEdge] = []
        for (from_family_id, to_family_id), payload in sorted(edge_map.items()):
            edges.append(
                PageFamilyEdge(
                    from_family_id=from_family_id,
                    to_family_id=to_family_id,
                    action_labels=[item for item, _count in payload["labels"].most_common(4)],
                    actions=[item for item, _count in payload["actions"].most_common(4)],
                    count=payload["count"],
                    effects=[item for item, _count in payload["effects"].most_common(3)],
                )
            )
        return edges

    def _build_seed_candidates(
        self,
        bundle: PageFamilyBundle,
        root_summary: str,
    ) -> list[_PlannedSeed]:
        root_family_id = bundle.root_family_id
        candidates: list[_PlannedSeed] = []
        for family in bundle.families:
            if family.family_id == root_family_id and not family.form_fields:
                continue

            path_screen_ids, path_actions = self.graph.build_path_to(family.canonical_screen_id)
            acceptance_hints = self._build_acceptance_hints(family)
            domain_object = family.domain_objects[0] if family.domain_objects else ""
            if path_actions:
                candidates.append(
                    _PlannedSeed(
                        family_id=family.family_id,
                        canonical_screen_id=family.canonical_screen_id,
                        priority_bucket="navigation",
                        operation_type="navigate",
                        task_type="navigation",
                        title=f"Navigate to the {family.name} page",
                        task_idea=f"Navigate to the {family.name} page",
                        coverage_key=f"navigation|{family.family_id}|navigate|{domain_object or 'generic'}",
                        domain_object=domain_object,
                        preferred_eval_type="gherkin_criteria",
                        acceptance_hints=acceptance_hints,
                        path_screen_ids=path_screen_ids,
                        path_actions=path_actions,
                        rationale=f"Cover the {family.name} page family at least once.",
                        source_screen_summary=root_summary,
                        target_screen_summary=family.summary or family.purpose,
                        target_page_type=family.page_type or family.name,
                        target_forms=self._format_family_forms(family),
                        target_route_patterns=family.route_patterns[:],
                        family_supported_operations=family.supported_operations[:],
                        evidence_screen_ids=family.member_screen_ids[:],
                        planner_notes=[
                            "Navigation coverage is highest priority for each distinct page family.",
                            "Prefer a concise reach-and-verify task instead of replaying the exact exploratory clicks.",
                        ],
                    )
                )

            for operation_type in self._preferred_family_operations(family):
                priority_bucket = "crud_form" if operation_type in WRITE_OPERATION_PRIORITY else "dom_interaction"
                candidates.append(
                    _PlannedSeed(
                        family_id=family.family_id,
                        canonical_screen_id=family.canonical_screen_id,
                        priority_bucket=priority_bucket,
                        operation_type=operation_type,
                        task_type="workflow",
                        title=self._workflow_title(family, operation_type),
                        task_idea=self._workflow_task_idea(family, operation_type),
                        coverage_key=f"{priority_bucket}|{family.family_id}|{operation_type}|{domain_object or 'generic'}",
                        domain_object=domain_object,
                        preferred_eval_type=self._preferred_eval_type(family, operation_type),
                        acceptance_hints=acceptance_hints,
                        path_screen_ids=path_screen_ids,
                        path_actions=path_actions,
                        rationale=self._workflow_rationale(family, operation_type),
                        source_screen_summary=root_summary,
                        target_screen_summary=family.summary or family.purpose,
                        target_page_type=family.page_type or family.name,
                        target_forms=self._format_family_forms(family),
                        target_route_patterns=family.route_patterns[:],
                        family_supported_operations=family.supported_operations[:],
                        evidence_screen_ids=family.member_screen_ids[:],
                        planner_notes=self._planner_notes(family, operation_type),
                    )
                )
        return candidates

    def _select_diverse_candidates(
        self,
        candidates: list[_PlannedSeed],
        bundle: PageFamilyBundle,
        max_seeds: int,
        max_tasks_per_family: int,
    ) -> list[_PlannedSeed]:
        by_bucket: dict[str, list[_PlannedSeed]] = {
            "navigation": [item for item in candidates if item.priority_bucket == "navigation"],
            "crud_form": [item for item in candidates if item.priority_bucket == "crud_form"],
            "dom_interaction": [item for item in candidates if item.priority_bucket == "dom_interaction"],
        }
        selected: list[_PlannedSeed] = []
        counts_by_family: Counter[str] = Counter()
        seen_page_types: set[str] = set()
        seen_operations: set[tuple[str, str]] = set()
        seen_objects: set[str] = set()
        family_order = [family.family_id for family in bundle.families]

        for bucket_name in ("navigation", "crud_form", "dom_interaction"):
            groups = {
                family_id: [item for item in by_bucket[bucket_name] if item.family_id == family_id]
                for family_id in family_order
            }
            made_progress = True
            while made_progress and len(selected) < max_seeds:
                made_progress = False
                for family_id in family_order:
                    if counts_by_family[family_id] >= max_tasks_per_family:
                        continue
                    family_candidates = groups.get(family_id, [])
                    if not family_candidates:
                        continue
                    best = max(
                        family_candidates,
                        key=lambda item: self._coverage_score(
                            item=item,
                            bundle=bundle,
                            counts_by_family=counts_by_family,
                            seen_page_types=seen_page_types,
                            seen_operations=seen_operations,
                            seen_objects=seen_objects,
                        ),
                    )
                    selected.append(best)
                    counts_by_family[family_id] += 1
                    seen_page_types.add(self._family_page_type(best.family_id, bundle).lower())
                    if best.domain_object:
                        seen_objects.add(best.domain_object.lower())
                    seen_operations.add((best.family_id, best.operation_type))
                    family_candidates.remove(best)
                    made_progress = True
                    if len(selected) >= max_seeds:
                        break
        return selected

    def _coverage_score(
        self,
        item: _PlannedSeed,
        bundle: PageFamilyBundle,
        counts_by_family: Counter[str],
        seen_page_types: set[str],
        seen_operations: set[tuple[str, str]],
        seen_objects: set[str],
    ) -> int:
        score = 0
        if item.priority_bucket == "navigation":
            score += 400
        elif item.priority_bucket == "crud_form":
            score += 250
        else:
            score += 120

        family = self._family_lookup(item.family_id, bundle)
        page_type = (family.page_type or family.name).lower()
        if counts_by_family[item.family_id] == 0:
            score += 120
        if page_type and page_type not in seen_page_types:
            score += 90
        if item.domain_object and item.domain_object.lower() not in seen_objects:
            score += 70
        if (item.family_id, item.operation_type) not in seen_operations:
            score += 85
        if family.form_fields and item.priority_bucket == "crud_form":
            score += min(len(family.form_fields), 10) * 2
        score += min(len(item.path_actions), 6) * 2
        score -= counts_by_family[item.family_id] * 55
        if item.operation_type in WRITE_OPERATION_PRIORITY:
            score += 40
        return score

    def _family_key(self, screen: Any) -> tuple[str, str, str]:
        path_key = self._normalized_path(screen.url)
        form_signature = self._form_signature(screen.form_fields)
        page_mode = "form" if form_signature else "page"
        object_hint = self._canonicalize_object(
            (screen.domain_objects[0] if screen.domain_objects else "")
            or self._route_object_hint(path_key)
        )
        return (path_key, page_mode, form_signature or object_hint)

    def _form_signature(self, form_fields: list[FormField]) -> str:
        if not form_fields:
            return ""
        labels = []
        for field in form_fields:
            label = self._normalize_text(field.label or field.field_type)
            if not label:
                continue
            labels.append(label)
        if not labels:
            return "generic-form"
        return "|".join(sorted(dict.fromkeys(labels))[:10])

    def _pick_canonical_screen(self, members: list[Any]) -> Any:
        def score(member: Any) -> tuple[int, int, int, int]:
            _path_screens, path_actions = self.graph.build_path_to(member.screen_id)
            richness = (
                len(member.form_fields) * 15
                + len(member.task_opportunities) * 8
                + len(member.important_dom) * 4
                + len(member.domain_objects) * 3
            )
            return (
                richness,
                -len(path_actions),
                len(member.summary or ""),
                -int(member.screen_id.split("_")[-1]),
            )

        return max(members, key=score)

    def _merge_domain_objects(self, members: list[Any]) -> list[str]:
        counter: Counter[str] = Counter()
        for member in members:
            for item in member.domain_objects:
                canonical = self._canonicalize_object(item)
                if canonical:
                    counter[canonical] += 1
        return [item for item, _count in counter.most_common(8)]

    def _merge_form_fields(self, members: list[Any]) -> list[FormField]:
        merged: dict[tuple[str, str], FormField] = {}
        for member in members:
            for field in member.form_fields:
                normalized_label = self._normalize_text(field.label or field.field_type)
                key = (normalized_label, field.field_type)
                if key not in merged:
                    merged[key] = FormField(
                        element_id=field.element_id,
                        label=field.label or normalized_label or field.field_type,
                        field_type=field.field_type,
                        required=field.required,
                        raw_text=field.raw_text,
                    )
                    continue
                if field.required:
                    merged[key].required = True
                if not merged[key].label and field.label:
                    merged[key].label = field.label
        return sorted(merged.values(), key=lambda item: (item.label.lower(), item.field_type))

    def _infer_supported_operations(
        self,
        canonical: Any,
        members: list[Any],
        form_fields: list[FormField],
        task_opportunities: list[str],
        important_dom: list[str],
    ) -> list[str]:
        text = " ".join(
            [
                canonical.page_type,
                canonical.summary,
                canonical.title,
                canonical.url,
                " ".join(task_opportunities),
                " ".join(important_dom),
                " ".join(field.label for field in form_fields),
                " ".join(item for member in members for item in member.secondary_dom),
            ]
        ).lower()
        operations = {"view"}
        if form_fields:
            operations.update({"create", "submit"})
        keyword_map = {
            "create": ("create", "add ", "new ", "request", "submit"),
            "edit": ("edit", "update", "save change", "save details"),
            "delete": ("delete", "remove", "revoke", "cancel"),
            "import": ("import", "upload", "csv"),
            "review": ("review", "audit", "approval", "history"),
            "detail": ("detail", "profile", "calendar"),
            "filter": ("filter", "department", "date range"),
            "search": ("search", "find"),
            "export": ("export", "download"),
        }
        for operation, tokens in keyword_map.items():
            if any(token in text for token in tokens):
                operations.add(operation)
        return [item for item in ALL_OPERATION_PRIORITY if item in operations] + [
            item for item in sorted(operations) if item not in ALL_OPERATION_PRIORITY
        ]

    def _build_acceptance_hints(self, family: PageFamily) -> list[str]:
        hints: list[str] = []
        if family.summary:
            hints.append(family.summary)
        route = next((item for item in family.route_patterns if item and item != "/"), "")
        if route:
            tail = route.rstrip("/").split("/")[-1]
            if tail and tail != ":id":
                hints.append(f'The URL should contain "{tail}"')
        if family.name:
            hints.append(f'The page should display "{family.name}"')
        for dom_item in family.important_dom[:3]:
            hints.append(f'I should see {dom_item} on the page')
        if family.form_fields:
            field = family.form_fields[0]
            hints.append(f'I should see field "{field.label}" on the page')
        return self._dedupe(hints)[:6]

    def _preferred_family_operations(self, family: PageFamily) -> list[str]:
        operation_groups = [
            ("create", "request", "submit"),
            ("edit",),
            ("delete",),
            ("import",),
            ("review", "detail"),
            ("filter", "search"),
            ("export",),
            ("view",),
        ]
        preferred: list[str] = []
        for group in operation_groups:
            for operation in group:
                if operation in family.supported_operations:
                    preferred.append(operation)
                    break
        if family.form_fields and not any(item in preferred for item in {"create", "request", "submit"}):
            preferred.insert(0, "create")
        return self._dedupe(preferred)[:3]

    def _workflow_title(self, family: PageFamily, operation_type: str) -> str:
        object_name = family.domain_objects[0] if family.domain_objects else family.name
        pretty_object = object_name or family.name
        if operation_type in {"create", "request", "submit"}:
            return f"Create or submit {pretty_object} on the {family.name} page"
        if operation_type == "edit":
            return f"Edit {pretty_object} on the {family.name} page"
        if operation_type == "delete":
            return f"Delete or revoke {pretty_object} from the {family.name} page"
        if operation_type == "import":
            return f"Import {pretty_object} data on the {family.name} page"
        if operation_type == "filter":
            return f"Filter records on the {family.name} page"
        if operation_type == "search":
            return f"Search within the {family.name} page"
        if operation_type == "export":
            return f"Export data from the {family.name} page"
        return f"Review the main workflow on the {family.name} page"

    def _workflow_task_idea(self, family: PageFamily, operation_type: str) -> str:
        object_name = family.domain_objects[0] if family.domain_objects else family.name
        if operation_type in {"create", "request", "submit"}:
            return f"Fill the form and submit new {object_name} data"
        if operation_type == "edit":
            return f"Edit existing {object_name} data"
        if operation_type == "delete":
            return f"Remove or revoke {object_name} data"
        if operation_type == "import":
            return f"Import {object_name} data"
        if operation_type == "filter":
            return f"Filter or narrow down records on {family.name}"
        if operation_type == "search":
            return f"Search for relevant records on {family.name}"
        if operation_type == "export":
            return f"Export data from {family.name}"
        return f"Interact with the main workflow on {family.name}"

    def _preferred_eval_type(self, family: PageFamily, operation_type: str) -> str:
        if operation_type in WRITE_OPERATION_PRIORITY:
            return "llm_judge"
        if family.form_fields and family.write_risk in {"medium", "high"}:
            return "llm_judge"
        return "gherkin_criteria"

    def _workflow_rationale(self, family: PageFamily, operation_type: str) -> str:
        if operation_type in WRITE_OPERATION_PRIORITY:
            return (
                f"Cover the {operation_type} workflow for the {family.name} family "
                "without overfitting to a single exploratory screen variant."
            )
        return f"Cover a meaningful non-write workflow on the {family.name} family."

    def _planner_notes(self, family: PageFamily, operation_type: str) -> list[str]:
        notes = [
            f"Target the canonical page family '{family.name}' instead of one specific screen variant.",
            "Keep the task grounded in the listed important DOM and supported operations.",
        ]
        if operation_type in WRITE_OPERATION_PRIORITY:
            notes.append("Prefer llm_judge for write-heavy workflows when success messages are uncertain.")
        if family.form_fields:
            notes.append("Use the summarized form fields to guide the workflow steps.")
        return notes

    def _format_family_forms(self, family: PageFamily) -> list[str]:
        if not family.form_fields:
            return family.forms_detected[:]
        field_names = ", ".join(field.label for field in family.form_fields[:10] if field.label)
        form_name = family.forms_detected[0] if family.forms_detected else f"{family.name} form"
        return [f"{form_name} (fields: {field_names})".strip()]

    def _variant_summary(self, member: Any) -> str:
        page_type = member.page_type or member.title or self._normalized_path(member.url)
        if member.summary:
            return f"{page_type}: {member.summary}"
        return f"{page_type}: {member.url}"

    def _family_digest(self, family: PageFamily) -> dict[str, Any]:
        return {
            "family_id": family.family_id,
            "canonical_screen_id": family.canonical_screen_id,
            "name": family.name,
            "page_type": family.page_type,
            "summary": family.summary or family.purpose,
            "purpose": family.purpose,
            "route_patterns": family.route_patterns[:],
            "domain_objects": family.domain_objects[:8],
            "supported_operations": family.supported_operations[:8],
            "task_opportunities": family.task_opportunities[:8],
            "important_dom": family.important_dom[:10],
            "secondary_dom": family.secondary_dom[:8],
            "form_fields": [
                {
                    "label": field.label,
                    "type": field.field_type,
                    "required": field.required,
                }
                for field in family.form_fields[:12]
            ],
            "member_screen_ids": family.member_screen_ids[:],
            "screen_count": len(family.member_screen_ids),
            "write_risk": family.write_risk,
            "screenshot_path": family.screenshot_path,
        }

    def _infer_sut_purpose(self, families: list[PageFamily]) -> str:
        if not families:
            return "This SUT exposes multiple discovered pages and workflows."
        objects = self._dedupe(
            item
            for family in families
            for item in family.domain_objects
        )[:5]
        if not objects:
            names = ", ".join(family.name for family in families[:4])
            return f"This SUT exposes these main areas: {names}."
        return (
            "This SUT appears to manage these core domains: "
            + ", ".join(objects)
            + ". It includes navigation, review, and data-management workflows across the discovered page families."
        )

    def _build_overview_text(
        self,
        purpose: str,
        page_type_counts: Counter[str],
        object_counts: Counter[str],
        capability_counts: Counter[str],
    ) -> str:
        top_areas = ", ".join(name for name, _count in page_type_counts.most_common(5))
        top_objects = ", ".join(name for name, _count in object_counts.most_common(5))
        top_ops = ", ".join(name for name, _count in capability_counts.most_common(5))
        sentences = [purpose.strip()]
        if top_areas:
            sentences.append(f"Key page families include {top_areas}.")
        if top_objects:
            sentences.append(f"Important domain objects include {top_objects}.")
        if top_ops:
            sentences.append(f"Common supported operations include {top_ops}.")
        return " ".join(item for item in sentences if item)

    def _purpose_from_family(
        self,
        name: str,
        operations: list[str],
        domain_objects: list[str],
    ) -> str:
        parts = [f"This page family centers on {name}."]
        if domain_objects:
            parts.append(f"It primarily works with {', '.join(domain_objects[:4])}.")
        if operations:
            parts.append(f"Supported operations include {', '.join(operations[:5])}.")
        return " ".join(parts)

    def _family_lookup(self, family_id: str, bundle: PageFamilyBundle) -> PageFamily:
        for family in bundle.families:
            if family.family_id == family_id:
                return family
        raise KeyError(f"Unknown family_id: {family_id}")

    def _family_name(self, family_id: str, bundle: PageFamilyBundle) -> str:
        return self._family_lookup(family_id, bundle).name

    def _family_page_type(self, family_id: str, bundle: PageFamilyBundle) -> str:
        family = self._family_lookup(family_id, bundle)
        return family.page_type or family.name

    def _route_object_hint(self, path_key: str) -> str:
        text = path_key.lower()
        for token in (
            "absence",
            "employee",
            "user",
            "department",
            "report",
            "audit",
            "calendar",
            "feed",
            "api",
            "integration",
        ):
            if token in text:
                return token
        return ""

    def _highest_risk(self, risks) -> str:
        risks = list(risks)
        if not risks:
            return "unknown"
        return max(risks, key=lambda item: RISK_ORDER.get(item, 0))

    def _normalized_path(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path or "/"
        normalized_segments: list[str] = []
        for segment in path.split("/"):
            if not segment:
                continue
            lowered = segment.lower()
            if re.fullmatch(r"\d+", lowered) or re.fullmatch(r"[0-9a-f-]{8,}", lowered):
                normalized_segments.append(":id")
            else:
                normalized_segments.append(lowered)
        if not normalized_segments:
            return "/"
        return "/" + "/".join(normalized_segments) + "/"

    def _display_name_from_route(self, route: str) -> str:
        tail = route.strip("/").split("/")[-1] if route and route != "/" else "home"
        tail = tail.replace("-", " ").replace("_", " ")
        return tail.title()

    def _canonicalize_object(self, value: str) -> str:
        normalized = self._normalize_text(value)
        if not normalized:
            return ""
        return OBJECT_SYNONYMS.get(normalized, normalized)

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    def _dedupe(self, values) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(text)
        return deduped
