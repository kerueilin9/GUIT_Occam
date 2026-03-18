"""
ISP (Input Space Partitioning) Generator for AgentOccam.

This module provides:
- FieldMetadata   – dataclass that stores field info extracted from the
                    accessibility-tree observation text.
- FieldAnalyzer   – static analyser: given an element_id and the raw
                    accessibility-tree string, returns a FieldMetadata.
- ISPPartition    – dataclass representing one ISP test-case value.
- ISPGenerator    – generates ISP partitions by combining static fallback
                    rules with an LLM-powered analysis step.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from functools import partial
from typing import List

# ─────────────────────────── Model registry ──────────────────────────────────
_MODEL_FAMILIES = [
    "claude", "mistral", "cohere", "llama", "titan", "gpt", "adk", "gemini"
]


def _build_call_model(model_id: str):
    """Return a ``call_model(prompt)`` callable for *model_id*.

    If the provider's call function accepts a ``system_prompt`` parameter,
    it is bound to ``""`` so that ISP prompts (which are self-contained) do
    not accidentally inherit a ``None`` default that would crash on string
    concatenation (as in ``call_gemini``).
    """
    from AgentOccam.llms.claude import call_claude
    from AgentOccam.llms.gpt import call_gpt
    from AgentOccam.llms.gemini import call_gemini
    from AgentOccam.llms.mistral import call_mistral
    from AgentOccam.llms.cohere import call_cohere
    from AgentOccam.llms.llama import call_llama
    from AgentOccam.llms.titan import call_titan
    from AgentOccam.llms.adk import call_adk
    import inspect

    _map = {
        "claude": call_claude,
        "gpt": call_gpt,
        "gemini": call_gemini,
        "mistral": call_mistral,
        "cohere": call_cohere,
        "llama": call_llama,
        "titan": call_titan,
        "adk": call_adk,
    }
    family = next((f for f in _MODEL_FAMILIES if f in model_id), None)
    if family is None:
        raise ValueError(f"Cannot determine model family for '{model_id}'")

    fn = _map[family]
    # Bind system_prompt="" if the provider supports it, to avoid None
    # default crashing on string concatenation (e.g. call_gemini).
    if "system_prompt" in inspect.signature(fn).parameters:
        return partial(fn, model_id=model_id, system_prompt="")
    return partial(fn, model_id=model_id)


# ───────────────────────── Data-classes ──────────────────────────────────────

@dataclass
class FieldMetadata:
    """Structured information about a web-form field."""
    element_id: str
    label: str
    input_type: str        # "text" | "password" | "email" | "number" | "textarea"
    required: bool
    surrounding_context: str  # ~300-char window of accessibility-tree text
    original_value: str        # value the agent originally chose to type


@dataclass
class ISPPartition:
    """A single ISP test-case partition for one field."""
    value: str
    category: str           # "valid" | "boundary" | "invalid" | "empty" | "original"
    description: str

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "category": self.category,
            "description": self.description,
        }


# ──────────────────────── FieldAnalyzer ──────────────────────────────────────

class FieldAnalyzer:
    """
    Extracts :class:`FieldMetadata` for a given *element_id* from the raw
    accessibility-tree text produced by WebArena's ``observation_type=
    "accessibility_tree"`` mode.

    Typical accessibility-tree lines look like::

        [42] textbox 'Title' required
        [15] textbox 'Email Address' type="email"
        [7]  textbox 'Content'
    """

    _REQUIRED_RE = re.compile(r'\brequired\b', re.IGNORECASE)
    _EMAIL_RE    = re.compile(r'\bemail\b',    re.IGNORECASE)
    _NUMBER_RE   = re.compile(r'\bnumber\b|\bnumeric\b', re.IGNORECASE)
    _PASSWORD_RE = re.compile(r'\bpassword\b', re.IGNORECASE)
    _TEXTAREA_RE = re.compile(r'\btextarea\b|\bmultiline\b', re.IGNORECASE)

    @classmethod
    def extract(
        cls,
        element_id: str,
        obs_text: str,
        field_hints: list | None = None,
    ) -> FieldMetadata:
        """Return a :class:`FieldMetadata` for *element_id* in *obs_text*.

        Parameters
        ----------
        element_id   : str
        obs_text     : str   Raw accessibility-tree text.
        field_hints  : list  Optional list of dicts from the task config
                             ``isp.field_hints`` section.  Each dict may
                             contain ``label_keywords``, ``input_type``,
                             ``required`` and ``description`` to enrich the
                             metadata when the accessibility tree is sparse.
        """
        eid = str(element_id)

        # ── 1. Context window ────────────────────────────────────────────────
        pattern = re.compile(r'\[' + re.escape(eid) + r'\]')
        match = pattern.search(obs_text)
        if match:
            start = max(0, match.start() - 150)
            end   = min(len(obs_text), match.end() + 150)
            context = obs_text[start:end].strip()
        else:
            context = ""

        # ── 2. Per-line attribute extraction from accessibility tree ─────────
        label      = ""
        input_type = "text"
        required   = False

        for line in obs_text.splitlines():
            if f"[{eid}]" not in line:
                continue

            # Label: first single- or double-quoted substring on the line
            lbl_match = re.search(r"['\"]([^'\"]+)['\"]", line)
            if lbl_match:
                label = lbl_match.group(1)

            # Input type inference
            if cls._PASSWORD_RE.search(line) or ("password" in label.lower()):
                input_type = "password"
            elif cls._EMAIL_RE.search(line) or ("email" in label.lower()):
                input_type = "email"
            elif cls._NUMBER_RE.search(line):
                input_type = "number"
            elif cls._TEXTAREA_RE.search(line) or ("content" in label.lower()):
                input_type = "textarea"
            else:
                input_type = "text"

            required = bool(cls._REQUIRED_RE.search(line))
            break  # only use first matching line

        # ── 3. Enrich with task-level field_hints (if provided) ──────────────
        if field_hints and label:
            label_lower = label.lower()
            for hint in field_hints:
                keywords = [kw.lower() for kw in hint.get("label_keywords", [])]
                if any(kw in label_lower or label_lower in kw
                       for kw in keywords):
                    # hint overrides only if the accessibility tree was vague
                    if hint.get("input_type") and input_type == "text":
                        input_type = hint["input_type"]
                    if hint.get("required") is not None and not required:
                        required = bool(hint["required"])
                    # Append hint description to context for the LLM prompt
                    hint_desc = hint.get("description", "")
                    if hint_desc:
                        context = f"{context}\n[hint] {hint_desc}".strip()
                    break

        return FieldMetadata(
            element_id=eid,
            label=label,
            input_type=input_type,
            required=required,
            surrounding_context=context,
            original_value="",   # filled in by the caller
        )


# ───────────────────────── ISPGenerator ──────────────────────────────────────

class ISPGenerator:
    """
    Generates ISP partitions for a web-form field.

    Strategy
    --------
    1. Always include the *original* value as the baseline "original" partition.
    2. Ask the configured LLM to generate ``n - 1`` additional partitions that
       cover valid, boundary, invalid, and empty equivalence classes.
    3. On LLM failure, fall back to a small set of static partitions.
    """

    # Static fallback partitions used when the LLM call fails.
    _STATIC_FALLBACKS: List[ISPPartition] = [
        ISPPartition("", "empty",    "Empty string — required-field validation"),
        ISPPartition("a" * 201, "boundary", "201-char string — exceeds typical limit"),
        ISPPartition("<script>alert(1)</script>", "invalid", "XSS injection attempt"),
        ISPPartition("'; DROP TABLE posts;--",    "invalid", "SQL injection attempt"),
        ISPPartition("   ",  "boundary", "Whitespace-only input"),
    ]

    def __init__(self, isp_config, actor_config):
        """
        Parameters
        ----------
        isp_config   : DotDict
            Must expose ``max_partitions_per_field`` and optionally ``isp_model``.
        actor_config : DotDict
            Used as fallback to determine which LLM to call.
        """
        self.n = getattr(isp_config, "max_partitions_per_field", 5)
        isp_model = getattr(isp_config, "isp_model", None) or actor_config.model
        self._call_model = _build_call_model(isp_model)

    # ── public API ────────────────────────────────────────────────────────────

    def generate(self, field_meta: FieldMetadata) -> List[ISPPartition]:
        """
        Generate up to ``self.n`` ISP partitions for *field_meta*.

        The list always starts with the *original* value.  Additional values
        come from the LLM; if the LLM fails, static fallbacks are used instead.

        Parameters
        ----------
        field_meta : FieldMetadata
            Must have ``original_value`` set by the caller before passing here.
        """
        from AgentOccam.prompts.isp_prompt import build_isp_generation_prompt

        partitions: List[ISPPartition] = []

        # ── Baseline: always include the original value ───────────────────────
        if field_meta.original_value is not None:
            partitions.append(ISPPartition(
                value=field_meta.original_value,
                category="original",
                description="Original value from task specification",
            ))

        remaining = max(1, self.n - len(partitions))

        # ── LLM-generated partitions ─────────────────────────────────────────
        prompt = build_isp_generation_prompt(field_meta, remaining)
        try:
            response = self._call_model(prompt=prompt)
            llm_parts = self._parse_llm_response(response)
        except Exception as exc:
            print(f"[ISPGenerator] LLM call failed for field "
                  f"'{field_meta.label}' [{field_meta.element_id}]: {exc}")
            llm_parts = []

        for p in llm_parts:
            if p.value != field_meta.original_value:
                partitions.append(p)
            if len(partitions) >= self.n:
                break

        # ── Static fallbacks if LLM produced too few partitions ──────────────
        if len(partitions) < self.n:
            for p in self._STATIC_FALLBACKS:
                if p.value != field_meta.original_value and p not in partitions:
                    partitions.append(p)
                if len(partitions) >= self.n:
                    break

        return partitions[: self.n]

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_llm_response(response: str) -> List[ISPPartition]:
        """Extract a JSON array from the LLM response and convert to
        :class:`ISPPartition` objects.  Returns ``[]`` on any parse error."""
        # Find the outermost JSON array in the response.
        json_match = re.search(r'\[.*?\]', response, re.DOTALL)
        if not json_match:
            return []
        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            return []

        result: List[ISPPartition] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            result.append(ISPPartition(
                value       = str(item.get("value", "")),
                category    = str(item.get("category", "valid")),
                description = str(item.get("description", "")),
            ))
        return result


# ───────────────────── select_isp_combinations ───────────────────────────────

def select_isp_combinations(
    field_partitions: dict,
    gherkin_context: dict,
    call_model,
    max_combinations: int = 8,
) -> list:
    """Ask an LLM to choose a representative subset of ISP test combinations.

    Parameters
    ----------
    field_partitions : dict
        ``{field_label: List[ISPPartition]}``
    gherkin_context : dict
        The ``gherkin`` block from the task config (used as context).
    call_model : callable
        A bound ``call_model(prompt=...)`` function (from
        :func:`_build_call_model`).
    max_combinations : int
        Suggested ceiling on returned combinations.

    Returns
    -------
    list of dict
        ``[{field_label: ISPPartition, ...}, ...]``
        Falls back to the full cartesian product (capped) on any failure.
    """
    from itertools import product as cartesian_product
    from AgentOccam.prompts.isp_prompt import build_isp_combination_prompt

    prompt = build_isp_combination_prompt(
        field_partitions, gherkin_context, max_combinations
    )

    raw_combinations: list = []
    try:
        response = call_model(prompt=prompt)
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            raw_combinations = json.loads(json_match.group(0))
    except Exception as exc:
        print(f"[ISPGenerator] select_isp_combinations LLM call failed: {exc}")

    # Parse LLM output into {label: ISPPartition} dicts
    combinations: list = []
    if raw_combinations and isinstance(raw_combinations, list):
        for combo in raw_combinations:
            if not isinstance(combo, dict):
                continue
            parsed: dict = {}
            for label, part_dict in combo.items():
                if not isinstance(part_dict, dict):
                    continue
                parsed[label] = ISPPartition(
                    value       = str(part_dict.get("value", "")),
                    category    = str(part_dict.get("category", "valid")),
                    description = str(part_dict.get("description", "")),
                )
            if parsed:
                combinations.append(parsed)

    # Fallback: cartesian product capped at max_combinations
    if not combinations:
        print("[ISPGenerator] Falling back to cartesian product for combinations.")
        labels = list(field_partitions.keys())
        parts  = [field_partitions[l] for l in labels]
        for combo_tuple in list(cartesian_product(*parts))[:max_combinations]:
            combinations.append({labels[i]: combo_tuple[i] for i in range(len(labels))})

    return combinations[:max_combinations]
