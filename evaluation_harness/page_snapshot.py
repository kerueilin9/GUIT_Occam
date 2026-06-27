"""Shared page-state extraction helpers for evaluators."""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Page


PageSnapshot = dict[str, str]


def extract_accessibility_tree_text(trajectory: list | None) -> str:
    """Extract the final accessibility-tree text from trajectory, if available."""
    if not isinstance(trajectory, list):
        return ""

    for entry in reversed(trajectory):
        if not isinstance(entry, dict) or "observation" not in entry:
            continue

        metadata_value = _extract_from_observation_metadata(entry)
        if metadata_value:
            return metadata_value

        observation_value = _extract_from_observation(entry.get("observation", {}))
        if observation_value:
            return observation_value

    return ""


def get_page_snapshot(
    page: Page,
    trajectory: list | None = None,
    *,
    accessibility_limit: int = 20000,
    body_limit: int = 3000,
) -> PageSnapshot:
    """Capture the current page state with accessibility-tree text as primary evidence."""
    snapshot: PageSnapshot = {
        "url": "",
        "title": "",
        "accessibility_tree_text": "",
        "body_text": "",
        "snapshot_source": "",
    }

    try:
        snapshot["url"] = page.url
    except Exception:
        pass
    try:
        snapshot["title"] = page.title()
    except Exception:
        pass

    accessibility_tree_text = extract_accessibility_tree_text(trajectory)
    if accessibility_tree_text:
        accessibility_tree_text = compact_accessibility_tree_text(accessibility_tree_text)
        snapshot["accessibility_tree_text"] = accessibility_tree_text[:accessibility_limit]
        snapshot["snapshot_source"] = "accessibility_tree"

    try:
        snapshot["body_text"] = page.inner_text("body")[:body_limit]
    except Exception:
        pass

    if not snapshot["snapshot_source"]:
        snapshot["snapshot_source"] = "body_text"

    return snapshot


def capture_page_screenshot(page: Page) -> bytes:
    """Capture the final page as PNG bytes for optional vision-assisted evaluation."""
    return page.screenshot(full_page=True, type="png", timeout=60000)


def _extract_from_observation_metadata(entry: dict[str, Any]) -> str:
    info = entry.get("info", {})
    if not isinstance(info, dict):
        return ""

    metadata = info.get("observation_metadata", {})
    if not isinstance(metadata, dict):
        return ""

    text_meta = metadata.get("text", {})
    if not isinstance(text_meta, dict):
        return ""

    for key in ("accessibility_tree_text", "accessibility_tree"):
        value = text_meta.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _filter_unselected_items(accessibility_tree_text: str) -> str:
    """Drop unselected option/menuitem rows that otherwise dominate snapshots."""
    return "\n".join(
        line
        for line in str(accessibility_tree_text).splitlines()
        if "selected: False" not in line
    )


def compact_accessibility_tree_text(accessibility_tree_text: str) -> str:
    """Keep evaluator snapshots focused on form evidence, not calendar grids."""
    return _drop_static_calendar_cells(_filter_unselected_items(accessibility_tree_text))


_STATIC_CALENDAR_CELL_RE = re.compile(
    r"^\s*\[\d+\]\s+gridcell\s+'(?:\\xa0|\s*|\d{1,2})'\s+required:\s*False\b",
    re.IGNORECASE,
)
_INTERACTIVE_ROLES = (
    "textbox",
    "button",
    "link",
    "combobox",
    "checkbox",
    "radio",
    "menuitem",
    "option",
)


def _drop_static_calendar_cells(accessibility_tree_text: str) -> str:
    lines = str(accessibility_tree_text).splitlines()
    kept: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if _STATIC_CALENDAR_CELL_RE.match(line):
            depth = _indent_depth(line)
            end = idx + 1
            while end < len(lines) and _indent_depth(lines[end]) > depth:
                end += 1
            block = "\n".join(lines[idx:end]).lower()
            if not any(role in block for role in _INTERACTIVE_ROLES):
                idx = end
                continue
        kept.append(line)
        idx += 1
    return "\n".join(kept)


def _indent_depth(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def _extract_from_observation(observation: Any) -> str:
    if not isinstance(observation, dict):
        return ""

    text_obs = observation.get("text")
    if isinstance(text_obs, (list, tuple)) and text_obs:
        candidate = text_obs[0]
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    if isinstance(text_obs, str) and text_obs.strip():
        return text_obs
    return ""
