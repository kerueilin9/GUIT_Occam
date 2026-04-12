"""Data models for SUT discovery and task generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SUTConfig:
    name: str
    start_url: str
    storage_state: str | None = None
    allowed_domains: list[str] = field(default_factory=list)


@dataclass
class DiscoverySettings:
    mode: str = "agentic_session"
    max_steps: int = 100
    time_budget_minutes: int = 60
    safe_mode: bool = True
    screenshot_on_each_step: bool = True
    allow_form_fill: bool = False
    allow_form_submit: bool = False
    max_candidate_actions_per_screen: int = 16
    headless: bool = True
    slow_mo_ms: int = 0
    observation_type: str = "accessibility_tree"
    current_viewport_only: bool = False
    viewport_width: int = 1280
    viewport_height: int = 720
    sleep_after_execution_sec: float = 0.5

    def __post_init__(self) -> None:
        if self.mode != "agentic_session":
            raise ValueError(
                "Discovery only supports mode='agentic_session'. guided_bfs has been removed."
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LLMSettings:
    enabled: bool = True
    use_for_action_ranking: bool = True
    use_for_screen_annotation: bool = True
    use_for_task_generation: bool = True
    use_for_page_revisit_check: bool = True
    exploration_model: str = "gemini-2.5-flash"
    annotation_model: str = ""
    task_generation_model: str = ""
    selected_actions_per_screen: int = 8
    ranking_observation_chars: int = 0
    annotation_observation_chars: int = 0
    revisit_observation_chars: int = 0
    revisit_candidate_limit: int = 6
    task_generation_context_chars: int = 12000
    max_task_ideas_per_screen: int = 4
    tasks_per_seed: int = 1
    memory_recent_events: int = 12
    decision_observation_chars: int = 0
    use_for_next_action_decision: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def resolved_annotation_model(self) -> str:
        return self.annotation_model or self.exploration_model

    def resolved_task_generation_model(self) -> str:
        return self.task_generation_model or self.exploration_model


@dataclass
class ADKSettings:
    enabled: bool = False
    use_for_discovery_llm_calls: bool = True
    sessioned_llm_roles: list[str] = field(default_factory=lambda: ["task_generation"])
    app_name: str = "AgentOccamDiscovery"
    user_id: str = "sut_explorer"
    session_prefix: str = "discovery"
    role_session_mode: str = "role_scoped"
    export_session_snapshot: bool = True
    prompt_state_max_chars: int = 4000
    max_recorded_role_invocations: int = 80

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskGenerationSettings:
    max_tasks: int = 50
    task_types: list[str] = field(default_factory=lambda: ["navigation", "workflow"])
    eval_mode: str = "gherkin_criteria"
    rewrite_with_llm: bool = True
    llm_model: str = "gemini-2.5-flash"
    reference_task_path: str | None = None
    max_reference_examples: int = 4
    max_tasks_per_screen: int = 2
    max_tasks_per_family: int = 3
    prefer_llm_judge_for_write_tasks: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationSettings:
    enabled: bool = False
    replay_runs: int = 1
    min_pass_rate: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiscoveryRunConfig:
    sut: SUTConfig
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    adk: ADKSettings = field(default_factory=ADKSettings)
    task_generation: TaskGenerationSettings = field(default_factory=TaskGenerationSettings)
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    output_root: str = "output/discovery"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveryRunConfig":
        return cls(
            sut=SUTConfig(**data["sut"]),
            discovery=DiscoverySettings(**data.get("discovery", {})),
            llm=LLMSettings(**data.get("llm", {})),
            adk=ADKSettings(**data.get("adk", {})),
            task_generation=TaskGenerationSettings(**data.get("task_generation", {})),
            validation=ValidationSettings(**data.get("validation", {})),
            output_root=data.get("output_root", "output/discovery"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InteractiveElement:
    element_id: str
    role: str
    label: str = ""
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FormField:
    element_id: str
    label: str
    field_type: str
    required: bool = False
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionCandidate:
    action: str
    element_id: str = ""
    role: str = ""
    label: str = ""
    raw_text: str = ""
    source: str = "interactive_element"
    zone: str = "main"
    signature: str = ""
    is_shared_chrome: bool = False
    seen_count: int = 0
    reason: str = ""
    novelty_score: float = 0.0
    selected_by: str = "policy"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScreenRecord:
    screen_id: str
    url: str
    title: str
    observation_text: str
    fingerprint: str
    interactive_elements: list[InteractiveElement] = field(default_factory=list)
    form_fields: list[FormField] = field(default_factory=list)
    action_candidates: list[ActionCandidate] = field(default_factory=list)
    recommended_actions: list[ActionCandidate] = field(default_factory=list)
    page_type: str = ""
    summary: str = ""
    key_entities: list[str] = field(default_factory=list)
    domain_objects: list[str] = field(default_factory=list)
    forms_detected: list[str] = field(default_factory=list)
    task_opportunities: list[str] = field(default_factory=list)
    important_dom: list[str] = field(default_factory=list)
    secondary_dom: list[str] = field(default_factory=list)
    write_risk: str = "unknown"
    screenshot_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["interactive_elements"] = [item.to_dict() for item in self.interactive_elements]
        data["form_fields"] = [item.to_dict() for item in self.form_fields]
        data["action_candidates"] = [item.to_dict() for item in self.action_candidates]
        data["recommended_actions"] = [item.to_dict() for item in self.recommended_actions]
        return data


@dataclass
class TransitionRecord:
    from_screen_id: str
    to_screen_id: str
    action: str
    success: bool
    action_label: str = ""
    selection_reason: str = ""
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageFamily:
    family_id: str
    canonical_screen_id: str
    name: str
    page_type: str
    summary: str = ""
    purpose: str = ""
    route_patterns: list[str] = field(default_factory=list)
    url_examples: list[str] = field(default_factory=list)
    member_screen_ids: list[str] = field(default_factory=list)
    domain_objects: list[str] = field(default_factory=list)
    supported_operations: list[str] = field(default_factory=list)
    task_opportunities: list[str] = field(default_factory=list)
    important_dom: list[str] = field(default_factory=list)
    secondary_dom: list[str] = field(default_factory=list)
    form_fields: list[FormField] = field(default_factory=list)
    forms_detected: list[str] = field(default_factory=list)
    variant_summaries: list[str] = field(default_factory=list)
    screenshot_path: str | None = None
    write_risk: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["form_fields"] = [item.to_dict() for item in self.form_fields]
        return data


@dataclass
class PageFamilyEdge:
    from_family_id: str
    to_family_id: str
    action_labels: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    count: int = 0
    effects: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskSeed:
    seed_id: str
    task_type: str
    priority_bucket: str
    entry_screen_id: str
    target_screen_id: str
    target_family_id: str = ""
    target_family_name: str = ""
    operation_type: str = ""
    title: str = ""
    task_idea: str = ""
    coverage_key: str = ""
    domain_object: str = ""
    preferred_eval_type: str = "gherkin_criteria"
    acceptance_hints: list[str] = field(default_factory=list)
    path_screen_ids: list[str] = field(default_factory=list)
    path_actions: list[str] = field(default_factory=list)
    rationale: str = ""
    source_screen_summary: str = ""
    target_screen_summary: str = ""
    target_page_type: str = ""
    target_forms: list[str] = field(default_factory=list)
    target_route_patterns: list[str] = field(default_factory=list)
    family_supported_operations: list[str] = field(default_factory=list)
    evidence_screen_ids: list[str] = field(default_factory=list)
    planner_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateTask:
    task_id: str
    task_type: str
    source_seed_id: str
    task_config: dict[str, Any]
    confidence: float = 0.0
    generation_method: str = "deterministic"
    review_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GeneratedTask:
    seed: TaskSeed
    candidate: CandidateTask

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed.to_dict(),
            "candidate": self.candidate.to_dict(),
        }
