"""Top-level package exports for AgentOccam."""

from __future__ import annotations

from importlib import import_module

_LAZY_EXPORTS = {
    "parse_node_descendants": ("AgentOccam.obs_opt", "parse_node_descendants"),
    "parse_node_ancestors": ("AgentOccam.obs_opt", "parse_node_ancestors"),
    "parse_node_siblings": ("AgentOccam.obs_opt", "parse_node_siblings"),
    "action_set_invisible": ("AgentOccam.obs_opt", "action_set_invisible"),
    "action_set_visible": ("AgentOccam.obs_opt", "action_set_visible"),
    "action_set_visible_if_with_name": (
        "AgentOccam.obs_opt",
        "action_set_visible_if_with_name",
    ),
    "translate_node_to_str": ("AgentOccam.obs_opt", "translate_node_to_str"),
    "construct_new_DOM_with_visible_nodes": (
        "AgentOccam.obs_opt",
        "construct_new_DOM_with_visible_nodes",
    ),
    "CURRENT_DIR": ("AgentOccam.utils", "CURRENT_DIR"),
    "HOMEPAGE_URL": ("AgentOccam.utils", "HOMEPAGE_URL"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module 'AgentOccam' has no attribute {name!r}")
    module_name, attribute_name = _LAZY_EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
