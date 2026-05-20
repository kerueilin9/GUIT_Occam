"""Compact trajectory evidence for evaluator prompts."""

from __future__ import annotations

import re
from typing import Any

from browser_env.actions import ActionTypes, action2str
from evaluation_harness.page_snapshot import (
    PageSnapshot,
    extract_accessibility_tree_text,
)


DISAPPEARANCE_KEYWORDS = (
    "disappear",
    "disappeared",
    "no longer",
    "delete",
    "deleted",
    "remove",
    "removed",
    "not appear",
    "not visible",
    "should not see",
    "gone",
)


def build_trajectory_evidence(
    trajectory: list | None,
    criterion: str,
    final_snapshot: PageSnapshot,
    *,
    max_actions: int = 40,
    max_matches_per_value: int = 5,
) -> str:
    """Build compact action/history context for evaluating one Gherkin criterion."""
    if not isinstance(trajectory, list) or not trajectory:
        return ""

    action_records = _collect_action_records(trajectory, max_actions=max_actions)
    if not action_records:
        return ""

    typed_values = _typed_values_from_records(action_records)
    lines = [
        "--- EXECUTION EVIDENCE (compact, use as supporting context) ---",
        "Use this to understand what the actor attempted. Final scoring should "
        "still be based on whether the final page state satisfies the criterion.",
        "",
        "Action trace:",
    ]
    lines.extend(record["line"] for record in action_records)

    if typed_values:
        lines.extend(["", "Typed values observed in actions:"])
        lines.extend(f'- "{value}"' for value in typed_values)

    if _is_disappearance_criterion(criterion):
        lines.extend(
            [
                "",
                "Disappearance/removal check:",
                "For disappearance criteria, absence on the final page is not "
                "enough by itself. Look for evidence that the target item was "
                "created or existed earlier, then deleted/removed, and is "
                "absent at the end.",
            ]
        )
        lines.extend(
            _build_value_presence_lines(
                trajectory,
                typed_values,
                final_snapshot,
                max_matches_per_value=max_matches_per_value,
            )
        )

    return "\n".join(lines)


def _collect_action_records(trajectory: list, *, max_actions: int) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    previous_observation_text = ""

    for entry in trajectory:
        if _is_state_entry(entry):
            previous_observation_text = _extract_state_text(entry)
            continue

        if not isinstance(entry, dict) or "action_type" not in entry:
            continue

        action_text = _action_to_text(entry)
        if not action_text or action_text == "none":
            continue

        semantic_line = _element_line_for_action(entry, previous_observation_text)
        line = f"{len(records) + 1}. {action_text}"
        if semantic_line:
            line = f"{line} | element: {semantic_line}"
        records.append({"line": line, "action": action_text})

    return records[-max_actions:]


def _action_to_text(action: dict[str, Any]) -> str:
    try:
        action_text = action2str(action, "id_accessibility_tree").strip()
        return re.sub(r"\s+where\s+\[[^\]]*\]\s+is\s*$", "", action_text)
    except Exception:
        return _fallback_action_to_text(action)


def _fallback_action_to_text(action: dict[str, Any]) -> str:
    action_type = action.get("action_type")
    if action_type == ActionTypes.STOP:
        return f"stop [{action.get('answer', '')}]"
    if action_type == ActionTypes.CLICK:
        return f"click [{action.get('element_id', '')}]"
    if action_type == ActionTypes.TYPE:
        return f"type [{action.get('element_id', '')}] [{action.get('text', '')}]"
    if action_type == ActionTypes.GO_BACK:
        return "go_back"
    if action_type == ActionTypes.GOTO_URL:
        return f"goto [{action.get('url', '')}]"
    if action_type == ActionTypes.SCROLL:
        return f"scroll [{action.get('direction', '')}]"
    return str(action_type or "")


def _element_line_for_action(action: dict[str, Any], observation_text: str) -> str:
    element_id = str(action.get("element_id", "")).strip()
    if not element_id or not observation_text:
        return ""

    pattern = re.compile(rf"^\s*\[{re.escape(element_id)}\]\s+(.+)$", re.MULTILINE)
    match = pattern.search(observation_text)
    if not match:
        return ""
    return f"[{element_id}] {match.group(1).strip()}"[:240]


def _typed_values_from_records(action_records: list[dict[str, str]]) -> list[str]:
    values: list[str] = []
    for record in action_records:
        match = re.search(
            r"type\s+\[[^\]]*\]\s+\[(.*?)\]",
            record["action"],
            flags=re.DOTALL,
        )
        if not match:
            continue
        value = match.group(1).replace("\n", "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _build_value_presence_lines(
    trajectory: list,
    typed_values: list[str],
    final_snapshot: PageSnapshot,
    *,
    max_matches_per_value: int,
) -> list[str]:
    if not typed_values:
        return [
            "- No typed values were found in the action trace, so a created "
            "comment/reply cannot be identified from actions alone."
        ]

    final_text = "\n".join(
        [
            final_snapshot.get("accessibility_tree_text", ""),
            final_snapshot.get("body_text", ""),
        ]
    ).lower()

    lines: list[str] = []
    states = _collect_state_texts(trajectory)
    prior_states = states[:-1] if len(states) > 1 else states
    for value in typed_values:
        normalized_value = value.lower()
        prior_matches = [
            f"state #{state_idx}"
            for state_idx, state_text in prior_states
            if normalized_value and normalized_value in state_text.lower()
        ][:max_matches_per_value]
        final_contains = normalized_value in final_text if normalized_value else False
        if prior_matches:
            lines.append(
                f'- "{value}" appeared before final state in '
                f'{", ".join(prior_matches)}; final page contains it: '
                f"{str(final_contains).lower()}."
            )
        else:
            lines.append(
                f'- "{value}" was typed, but no prior accessibility snapshot '
                f"showed it before final state; final page contains it: "
                f"{str(final_contains).lower()}."
            )
    return lines


def _collect_state_texts(trajectory: list) -> list[tuple[int, str]]:
    states: list[tuple[int, str]] = []
    for entry in trajectory:
        if not _is_state_entry(entry):
            continue
        text = _extract_state_text(entry)
        if text:
            states.append((len(states) + 1, text))
    return states


def _extract_state_text(state_entry: dict[str, Any]) -> str:
    return extract_accessibility_tree_text([state_entry])


def _is_state_entry(entry: Any) -> bool:
    return isinstance(entry, dict) and "observation" in entry


def _is_disappearance_criterion(criterion: str) -> bool:
    criterion_lower = criterion.lower()
    return any(keyword in criterion_lower for keyword in DISAPPEARANCE_KEYWORDS)
