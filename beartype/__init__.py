"""Minimal compatibility shim for environments without the beartype package."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, overload

F = TypeVar("F", bound=Callable[..., Any])


@overload
def beartype(func: F) -> F:
    ...


@overload
def beartype(*, conf: Any | None = None) -> Callable[[F], F]:
    ...


def beartype(func: F | None = None, **_: Any):
    """Return the wrapped function unchanged."""

    if func is None:
        def decorator(inner: F) -> F:
            return inner

        return decorator
    return func


__all__ = ["beartype"]
