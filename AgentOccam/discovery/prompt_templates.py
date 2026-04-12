"""Discovery prompt file loading helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any

from AgentOccam.utils import CURRENT_DIR

DISCOVERY_PROMPTS_DIR = Path(CURRENT_DIR) / "AgentOccam" / "prompts" / "discovery"

PROMPT_FILES = {
    "screen_annotation": "screen_annotation.txt",
    "action_ranking": "action_ranking.txt",
    "page_revisit": "page_revisit.txt",
    "next_action": "next_action.txt",
    "task_generation": "task_generation.txt",
}

ROLE_PROMPT_FILES = {
    "screen_annotation": "role_screen_annotation.txt",
    "action_ranking": "role_action_ranking.txt",
    "page_revisit": "role_page_revisit.txt",
    "next_action": "role_next_action.txt",
    "task_generation": "role_task_generation.txt",
}


@lru_cache(maxsize=None)
def _read_prompt_file(filename: str) -> str:
    return (DISCOVERY_PROMPTS_DIR / filename).read_text(encoding="utf-8")


def render_discovery_prompt(name: str, **context: Any) -> str:
    template = Template(_read_prompt_file(PROMPT_FILES[name]))
    normalized_context = {
        key: "" if value is None else str(value)
        for key, value in context.items()
    }
    return template.substitute(normalized_context)


def get_discovery_role_instruction(role: str) -> str:
    return _read_prompt_file(ROLE_PROMPT_FILES[role]).strip()
