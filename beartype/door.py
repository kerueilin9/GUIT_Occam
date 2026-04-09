"""Minimal subset of beartype.door used by this project."""

from __future__ import annotations

from typing import Any


def is_bearable(obj: Any, hint: Any) -> bool:
    """Best-effort runtime type check with a permissive fallback."""

    try:
        return isinstance(obj, hint)
    except Exception:
        return True


__all__ = ["is_bearable"]
