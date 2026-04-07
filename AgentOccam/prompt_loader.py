"""Shared prompt/spec file loading helpers."""

from __future__ import annotations

import os
from typing import Iterable

from AgentOccam.utils import CURRENT_DIR


PROMPTS_DIR = os.path.join(CURRENT_DIR, "AgentOccam", "prompts")


def _read_prompt_file(folder: str, filename: str) -> str:
    path = os.path.join(PROMPTS_DIR, folder, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_output_specifications(output_keys: Iterable[str], character: str | None = None) -> str:
    sections = []
    for key in output_keys:
        stem = key.replace(" ", "_")
        filename = f"{stem}.txt"
        if character:
            alt_filename = f"{stem}_{character}.txt"
            alt_path = os.path.join(PROMPTS_DIR, "output_specifications", alt_filename)
            if os.path.exists(alt_path):
                filename = alt_filename
        sections.append(f"{key.upper()}:\n" + _read_prompt_file("output_specifications", filename))
    return "\n".join(sections)


def build_bulleted_specifications(folder: str, spec_names: Iterable[str]) -> str:
    return "\n".join(["- " + _read_prompt_file(folder, f"{name}.txt") for name in spec_names])
