"""Discovery and task generation helpers for SUT exploration."""

from __future__ import annotations

from importlib import import_module

_LAZY_EXPORTS = {
    "ActionCandidate": ("AgentOccam.discovery.models", "ActionCandidate"),
    "CandidateTask": ("AgentOccam.discovery.models", "CandidateTask"),
    "DiscoveryExecutor": ("AgentOccam.discovery.runtime", "DiscoveryExecutor"),
    "DiscoveryPipeline": ("AgentOccam.discovery.pipeline", "DiscoveryPipeline"),
    "DiscoveryRunConfig": ("AgentOccam.discovery.models", "DiscoveryRunConfig"),
    "DiscoverySettings": ("AgentOccam.discovery.models", "DiscoverySettings"),
    "ADKSettings": ("AgentOccam.discovery.models", "ADKSettings"),
    "FormField": ("AgentOccam.discovery.models", "FormField"),
    "GeneratedTask": ("AgentOccam.discovery.models", "GeneratedTask"),
    "InteractiveElement": ("AgentOccam.discovery.models", "InteractiveElement"),
    "LLMSettings": ("AgentOccam.discovery.models", "LLMSettings"),
    "ScreenGraph": ("AgentOccam.discovery.screen_graph", "ScreenGraph"),
    "ScreenRecord": ("AgentOccam.discovery.models", "ScreenRecord"),
    "SUTConfig": ("AgentOccam.discovery.models", "SUTConfig"),
    "TaskGenerationSettings": (
        "AgentOccam.discovery.models",
        "TaskGenerationSettings",
    ),
    "TaskSeed": ("AgentOccam.discovery.models", "TaskSeed"),
    "TransitionRecord": ("AgentOccam.discovery.models", "TransitionRecord"),
    "ValidationSettings": ("AgentOccam.discovery.models", "ValidationSettings"),
    "run_discovery_from_config_path": (
        "AgentOccam.discovery.runtime",
        "run_discovery_from_config_path",
    ),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module 'AgentOccam.discovery' has no attribute {name!r}")
    module_name, attribute_name = _LAZY_EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
