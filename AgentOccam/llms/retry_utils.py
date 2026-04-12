"""Retry helpers for quota and transient LLM provider errors."""

from __future__ import annotations

import re
from typing import Any

RETRY_DELAY_PATTERNS = (
    re.compile(r"retry in (?P<seconds>\d+(?:\.\d+)?)s", re.IGNORECASE),
    re.compile(r"retrydelay['\"]?\s*[:=]\s*['\"]?(?P<seconds>\d+(?:\.\d+)?)s", re.IGNORECASE),
)
QUOTA_ERROR_MARKERS = (
    "resource_exhausted",
    "quota exceeded",
    "rate limit",
    "429",
)
TRANSIENT_ERROR_MARKERS = (
    "unavailable",
    "deadline_exceeded",
    "internal",
    "temporarily overloaded",
    "try again later",
    "retry",
    "no response generated from adk",
    "503",
    "504",
)


def stringify_error(error: Any) -> str:
    if isinstance(error, BaseException):
        return f"{type(error).__name__}: {error}"
    return str(error)


def extract_retry_delay_seconds(error: Any) -> float | None:
    text = stringify_error(error)
    for pattern in RETRY_DELAY_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        try:
            return max(0.0, float(match.group("seconds")))
        except (TypeError, ValueError):
            continue
    return None


def is_quota_error(error: Any) -> bool:
    text = stringify_error(error).lower()
    return any(marker in text for marker in QUOTA_ERROR_MARKERS)


def should_retry_error(error: Any) -> bool:
    text = stringify_error(error).lower()
    return is_quota_error(text) or any(marker in text for marker in TRANSIENT_ERROR_MARKERS)


def compute_retry_delay_seconds(
    error: Any,
    attempt_index: int,
    *,
    default_seconds: float = 10.0,
    quota_seconds: float = 30.0,
    max_seconds: float = 120.0,
) -> float:
    parsed_delay = extract_retry_delay_seconds(error)
    if parsed_delay is not None:
        return min(max(1.0, parsed_delay), max_seconds)

    if is_quota_error(error):
        return min(quota_seconds * (attempt_index + 1), max_seconds)

    return min(default_seconds * (attempt_index + 1), max_seconds)
