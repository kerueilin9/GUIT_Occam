"""Stable action summaries for discovery memory, prompts, and artifacts."""

from __future__ import annotations

import re

from AgentOccam.discovery.models import ActionCandidate, TransitionRecord

_WHITESPACE_RE = re.compile(r"\s+")


def summarize_candidate_action(candidate: ActionCandidate) -> str:
    return summarize_action_fields(
        action=candidate.action,
        label=candidate.label,
        raw_text=candidate.raw_text,
        role=candidate.role,
        zone=candidate.zone,
        signature=candidate.signature,
    )


def summarize_transition_action(transition: TransitionRecord) -> str:
    metadata = transition.metadata if isinstance(transition.metadata, dict) else {}
    summary = str(metadata.get("action_summary", "")).strip()
    if summary:
        return summary
    return summarize_action_fields(
        action=transition.action,
        label=transition.action_label,
        raw_text="",
        role="",
        zone="",
        signature=str(metadata.get("action_signature", "")).strip(),
    )


def summarize_path_steps(path_steps: list[ActionCandidate]) -> list[str]:
    return [summarize_candidate_action(step) for step in path_steps]


def snapshot_candidate_for_path(candidate: ActionCandidate) -> ActionCandidate:
    return ActionCandidate(
        action="",
        element_id="",
        role=candidate.role,
        label=candidate.label,
        raw_text=candidate.raw_text,
        source="path_step",
        zone=candidate.zone,
        signature=candidate.signature,
        is_shared_chrome=candidate.is_shared_chrome,
        seen_count=0,
        reason="",
        novelty_score=0.0,
        selected_by="path_step",
    )


def matches_path_step(candidate: ActionCandidate, path_step: ActionCandidate) -> bool:
    if path_step.signature and candidate.signature == path_step.signature:
        return True

    candidate_label = _normalize_text(candidate.label or candidate.raw_text)
    path_label = _normalize_text(path_step.label or path_step.raw_text)
    if not candidate_label or not path_label or candidate_label != path_label:
        return False

    if path_step.role and candidate.role and path_step.role != candidate.role:
        return False
    if path_step.zone and candidate.zone and path_step.zone != candidate.zone:
        return False
    return True


def summarize_action_fields(
    *,
    action: str,
    label: str,
    raw_text: str,
    role: str,
    zone: str,
    signature: str,
) -> str:
    action_name = (action or "").strip()
    action_signature = (signature or "").strip()
    if action_name == "go_back" or action_signature == "navigation:go_back":
        return "Go back"

    zone_name = (zone or "main").strip() or "main"
    role_name = (role or "element").strip() or "element"
    label_text = (label or raw_text or "").strip()
    if not label_text:
        label_text = _label_from_signature(action_signature)

    if label_text:
        return f'Click "{label_text}" ({zone_name}/{role_name})'
    if action_signature:
        return f"Activate ({action_signature})"
    if action_name:
        return action_name
    return "Interact with page control"


def _label_from_signature(signature: str) -> str:
    if not signature:
        return ""
    parts = [part.strip() for part in signature.split(":") if part.strip()]
    if len(parts) < 3:
        return ""
    label = parts[-1]
    if label == "unlabeled":
        return ""
    return _WHITESPACE_RE.sub(" ", label.replace("_", " ").replace("-", " ")).strip()


def _normalize_text(text: str) -> str:
    cleaned = _WHITESPACE_RE.sub(" ", (text or "").strip().lower())
    cleaned = re.sub(r"[^\w\s/-]+", "", cleaned)
    return cleaned.strip()
