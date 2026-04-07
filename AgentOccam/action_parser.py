"""Utilities for parsing and validating AgentOccam action strings."""

from __future__ import annotations

import re
from typing import Optional

CLICK_RE = re.compile(r"click ?\[(\d+)\]")
HOVER_RE = re.compile(r"hover ?\[(\d+)\]")
TYPE_RE = re.compile(r"type ?\[(\d+)\] ?\[(.*)\] ?\[(\d+)\]", re.DOTALL)


def action_name(action_str: str) -> str:
    value = (action_str or "").strip()
    if not value:
        return ""
    if "[" in value:
        return value.split("[")[0].strip()
    parts = value.split()
    return parts[0].strip() if parts else ""


def normalize_type_action(action_str: str) -> str:
    value = (action_str or "").strip()
    if not (value.endswith("[0]") or value.endswith("[1]")):
        value += " [1]"
    return value


def parse_click_or_hover_element_id(action_str: str, action: str) -> Optional[int]:
    pattern = CLICK_RE if action == "click" else HOVER_RE
    match = pattern.search((action_str or "").strip())
    if not match:
        return None
    return int(match.group(1))


def parse_type(action_str: str) -> Optional[tuple[int, str, bool]]:
    value = normalize_type_action(action_str)
    match = TYPE_RE.search(value)
    if not match:
        return None
    element_id = int(match.group(1))
    text = match.group(2)
    enter_flag = match.group(3) == "1"
    return element_id, text, enter_flag


def parse_element_id(action_str: str) -> Optional[int]:
    name = action_name(action_str)
    if name in {"click", "hover"}:
        return parse_click_or_hover_element_id(action_str, name)
    if name == "type":
        parsed = parse_type(action_str)
        return parsed[0] if parsed else None
    return None
