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
    max_steps: int = 100
    max_depth: int = 3
    strategy: str = "bfs"
    safe_mode: bool = True
    screenshot_on_each_step: bool = True
    allow_form_fill: bool = False
    allow_form_submit: bool = False


@dataclass
class TaskGenerationSettings:
    max_tasks: int = 20
    task_types: list[str] = field(default_factory=lambda: ["navigation"])
    eval_mode: str = "llm_judge"
    rewrite_with_llm: bool = False
    llm_model: str = "gemini-2.5-flash"


@dataclass
class ValidationSettings:
    enabled: bool = False
    replay_runs: int = 1
    min_pass_rate: float = 1.0


@dataclass
class DiscoveryRunConfig:
    sut: SUTConfig
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings)
    task_generation: TaskGenerationSettings = field(default_factory=TaskGenerationSettings)
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    output_root: str = "output/discovery"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveryRunConfig":
        return cls(
            sut=SUTConfig(**data["sut"]),
            discovery=DiscoverySettings(**data.get("discovery", {})),
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
class ScreenRecord:
    screen_id: str
    url: str
    title: str
    observation_text: str
    fingerprint: str
    interactive_elements: list[InteractiveElement] = field(default_factory=list)
    screenshot_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["interactive_elements"] = [item.to_dict() for item in self.interactive_elements]
        return data


@dataclass
class TransitionRecord:
    from_screen_id: str
    to_screen_id: str
    action: str
    success: bool
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskSeed:
    seed_id: str
    task_type: str
    entry_screen_id: str
    target_screen_id: str
    path_screen_ids: list[str] = field(default_factory=list)
    path_actions: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateTask:
    task_id: str
    task_type: str
    source_seed_id: str
    task_config: dict[str, Any]
    confidence: float = 0.0
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
