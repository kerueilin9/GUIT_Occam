"""
ISP testcase generator for AgentOccam.

This module provides:
- FieldMetadata   – field info extracted from the accessibility tree.
- FieldAnalyzer   – metadata extraction for typed form fields.
- ISPPartition    – heuristic single-field candidate values used by fallback.
- ISPTestCase     – complete multi-field ISP testcase output.
- ISPGenerator    – single-call testcase generation with deterministic fallback.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from typing import List
from AgentOccam.logger import logger
from AgentOccam.model_registry import build_call_model

# ───────────────────────── Data-classes ──────────────────────────────────────

@dataclass
class FieldMetadata:
    """Structured information about a web-form field."""
    element_id: str
    label: str
    input_type: str        # "text" | "password" | "email" | "number" | "phone" | "textarea"
    required: bool
    surrounding_context: str  # ~300-char window of accessibility-tree text
    original_value: str        # value the agent originally chose to type


@dataclass
class ISPPartition:
    """A single ISP test-case partition for one field."""
    value: str
    category: str           # "valid" | "boundary" | "invalid" | "empty" | "original"
    description: str


@dataclass
class ISPTestCase:
    """A complete ISP testcase covering multiple form fields."""
    name: str
    expected: str
    inputs: dict[str, str]


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
    _PHONE_RE    = re.compile(r'\bphone\b|\bmobile\b|\btelephone\b|\btel\b', re.IGNORECASE)
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
            start = max(0, match.start() - 300)
            end   = min(len(obs_text), match.end() + 300)
            context = obs_text[start:end].strip()
        else:
            context = ""

        # ── 2. Per-line attribute extraction from accessibility tree ─────────
        label      = ""
        input_type = "text"
        required   = False

        lines = obs_text.splitlines()
        target_idx = -1
        target_line = ""
        for idx, line in enumerate(lines):
            if f"[{eid}]" in line:
                target_idx = idx
                target_line = line
                break

        if target_line:
            # Label: first single- or double-quoted substring on the line
            lbl_match = re.search(r"['\"]([^'\"]+)['\"]", target_line)
            if lbl_match:
                label = lbl_match.group(1)

            # Rich-text iframes all expose the same generic label; their real
            # field label is the nearest preceding StaticText row.
            if not label or "rich text area" in label.lower():
                previous_lines = lines[max(0, target_idx - 80):target_idx]
                for prev_line in reversed(previous_lines):
                    prev_match = re.search(r"StaticText ['\"]([^'\"]+)['\"]", prev_line)
                    if prev_match:
                        candidate = prev_match.group(1).strip()
                        if (
                            candidate
                            and re.search(r"[A-Za-z]", candidate)
                            and candidate not in {"Select...", "Clear value"}
                        ):
                            label = candidate
                            break

            # Input type inference
            if cls._PASSWORD_RE.search(target_line) or ("password" in label.lower()):
                input_type = "password"
            elif cls._EMAIL_RE.search(target_line) or ("email" in label.lower()):
                input_type = "email"
            elif cls._PHONE_RE.search(target_line) or any(token in label.lower() for token in ["phone", "mobile", "telephone"]):
                input_type = "phone"
            elif cls._NUMBER_RE.search(target_line):
                input_type = "number"
            elif cls._TEXTAREA_RE.search(target_line) or ("content" in label.lower()):
                input_type = "textarea"
            else:
                input_type = "text"

            required = bool(cls._REQUIRED_RE.search(target_line))

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
    Generates complete ISP testcases for a web form.

    The primary path is a single LLM call that returns ready-to-use testcases.
    If that fails, the generator falls back to deterministic heuristic cases,
    while still preserving empty-input coverage and a duplicate-existing case
    for uniqueness-sensitive fields.
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
            May expose ``max_test_cases``, ``max_partitions_per_field`` for
            backward compatibility, ``expected_outcomes_by_category`` and
            ``isp_model``.
        actor_config : DotDict
            Used as fallback to determine which LLM to call.
        """
        self.default_max_test_cases = (
            getattr(isp_config, "max_test_cases", None)
            or getattr(isp_config, "max_partitions_per_field", 5)
        )
        isp_model = getattr(isp_config, "isp_model", None) or actor_config.model
        expected_outcomes = getattr(isp_config, "expected_outcomes_by_category", {}) or {}
        self.expected_outcomes_by_category = {
            "original": "pass",
            "valid": "pass",
            "boundary": "fail",
            "invalid": "fail",
            "empty": "fail",
        }
        for category, expected in expected_outcomes.items():
            self.expected_outcomes_by_category[category] = (
                self._normalize_expected(expected) or "fail"
            )
        # ISP prompts are self-contained; force empty system prompt for consistency.
        self._call_model = build_call_model(isp_model, system_prompt="")

    @staticmethod
    def _heuristic_fallbacks(field_meta: FieldMetadata) -> List[ISPPartition]:
        label_lower = (field_meta.label or "").lower()
        input_type = (field_meta.input_type or "text").lower()

        if input_type in {"date", "datetime"} or re.search(r"\b(date|begin|start|from|end|until)\b", label_lower):
            return [
                ISPPartition("", "empty", "Empty date — required-field validation"),
                ISPPartition("5/15/26", "valid", "Alternate valid date in the observed format"),
                ISPPartition("13/40/26", "invalid", "Impossible month/day date"),
                ISPPartition("not-a-date", "invalid", "Non-date text in a date field"),
                ISPPartition("2/29/25", "invalid", "Invalid leap-day boundary"),
            ]

        if input_type == "email" or "email" in label_lower:
            return [
                ISPPartition("valid.user@example.com", "valid", "Well-formed alternate email address"),
                ISPPartition("", "empty", "Empty string — required-field validation"),
                ISPPartition("a@b.co", "boundary", "Minimal well-formed email boundary"),
                ISPPartition("user.example.com", "invalid", "Missing @ symbol"),
                ISPPartition("user@", "invalid", "Missing domain after @"),
            ]

        if input_type == "password" or "password" in label_lower:
            return [
                ISPPartition("P@ssw0rd2026!", "valid", "Strong alternate password"),
                ISPPartition("", "empty", "Empty string — required-field validation"),
                ISPPartition("short1", "invalid", "Too short for typical password rules"),
                ISPPartition("        ", "boundary", "Whitespace-only password"),
                ISPPartition("A" * 129, "boundary", "Very long password boundary"),
            ]

        if input_type == "phone" or any(token in label_lower for token in ["phone", "mobile", "telephone"]):
            return [
                ISPPartition("+14155550123", "valid", "Well-formed alternate phone number"),
                ISPPartition("", "empty", "Empty string — required-field validation"),
                ISPPartition("0912345678", "boundary", "Local-format phone number boundary"),
                ISPPartition("12345", "invalid", "Too short to be a valid phone number"),
                ISPPartition("phone-number", "invalid", "Alphabetic characters in phone number"),
            ]

        if input_type == "number":
            return [
                ISPPartition("0", "boundary", "Zero boundary value"),
                ISPPartition("1", "valid", "Simple positive number"),
                ISPPartition("-1", "boundary", "Negative boundary value"),
                ISPPartition("999999999", "boundary", "Large numeric boundary"),
                ISPPartition("abc", "invalid", "Alphabetic input in numeric field"),
            ]

        return [
            ISPPartition("", "empty", "Empty string — required-field validation"),
            ISPPartition("a", "boundary", "Single-character lower boundary"),
            ISPPartition("a" * 201, "boundary", "201-char string — exceeds typical limit"),
            ISPPartition("<script>alert(1)</script>", "invalid", "XSS injection attempt"),
            ISPPartition("'; DROP TABLE posts;--", "invalid", "SQL injection attempt"),
        ]

    # ── public API ────────────────────────────────────────────────────────────

    def generate_test_cases(
        self,
        field_metas: dict[str, FieldMetadata],
        gherkin_context: dict | None = None,
        max_cases: int | None = None,
    ) -> List[ISPTestCase]:
        """Generate complete ISP testcases in a single LLM call."""
        from AgentOccam.prompts.isp_prompt import build_isp_testcase_generation_prompt

        if not field_metas:
            return []

        case_limit = max(
            1,
            int(max_cases or self.default_max_test_cases or 1),
        )
        prompt = build_isp_testcase_generation_prompt(
            field_metas,
            case_limit,
            gherkin_context=gherkin_context or {},
        )

        parsed_cases: List[ISPTestCase] = []
        try:
            # LLM ISP testcase generation prompt
            logger.debug(f"[ISPGenerator] Testcase generation prompt:\n{prompt}")
            response = self._call_model(prompt=prompt)
            print(f"[ISPGenerator] LLM response received: {len(str(response or ''))} chars")
            parsed_cases = self._parse_test_case_llm_response(response, field_metas)
            if parsed_cases:
                print(f"[ISPGenerator] Parsed {len(parsed_cases)} testcase(s) from LLM response.")
            else:
                print("[ISPGenerator] LLM response could not be parsed; using heuristic fallback.")
                print(f"[ISPGenerator] LLM response preview: {self._response_preview(response)}")
        except Exception as exc:
            print(f"[ISPGenerator] Testcase LLM call failed: {exc}; using heuristic fallback.")

        if not parsed_cases:
            fallback_cases = self._build_heuristic_test_cases(
                field_metas,
                case_limit,
                gherkin_context=gherkin_context,
            )
            print(f"[ISPGenerator] Heuristic fallback generated {len(fallback_cases)} testcase(s).")
            parsed_cases = fallback_cases

        return parsed_cases[:case_limit]

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _response_preview(response: str, limit: int = 800) -> str:
        text = str(response or "").replace("\r", "\\r").replace("\n", "\\n")
        if len(text) > limit:
            text = text[:limit] + "...<truncated>"
        return text or "<empty>"

    @staticmethod
    def _extract_partial_json_array(text: str) -> list | None:
        """Best-effort recovery for arrays whose tail was truncated or malformed."""
        stripped = str(text or "").strip()
        start = stripped.find("[")
        if start == -1:
            return None

        decoder = json.JSONDecoder()
        idx = start + 1
        items: list = []
        while idx < len(stripped):
            while idx < len(stripped) and stripped[idx].isspace():
                idx += 1
            if idx >= len(stripped) or stripped[idx] == "]":
                break
            if stripped[idx] == ",":
                idx += 1
                continue

            try:
                item, idx = decoder.raw_decode(stripped, idx)
            except json.JSONDecodeError:
                break
            items.append(item)

        return items or None

    @classmethod
    def _extract_json_array(cls, response: str) -> list | None:
        candidates = []
        stripped = str(response).strip()
        if stripped:
            candidates.append(stripped)

        start = stripped.find("[")
        end = stripped.rfind("]")
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start:end + 1])

        seen: set[str] = set()
        last_error: json.JSONDecodeError | None = None
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(parsed, list):
                return parsed

        if last_error is not None:
            print(
                "[ISPGenerator] JSON parse error: "
                f"{last_error.msg} at line {last_error.lineno}, "
                f"column {last_error.colno}, char {last_error.pos}"
            )
            error_start = max(0, last_error.pos - 160)
            error_end = min(len(stripped), last_error.pos + 160)
            print(
                "[ISPGenerator] JSON error context: "
                f"{cls._response_preview(stripped[error_start:error_end], limit=360)}"
            )

        partial = cls._extract_partial_json_array(stripped)
        if partial:
            print(
                "[ISPGenerator] Recovered "
                f"{len(partial)} complete item(s) from partial JSON array."
            )
            return partial
        return None

    @staticmethod
    def _normalize_field_reference(label: str) -> str:
        text = re.sub(r"\s+", " ", str(label or "")).strip().strip("'\"")
        text = re.sub(r"^\s*(?:the|a|an)\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"\s+(?:field|textbox|input|box)\s*$",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return text.strip(" :")

    @classmethod
    def _find_best_field_name_match(
        cls,
        raw_label: str,
        field_names: list[str],
    ) -> str | None:
        target = cls._normalize_field_reference(raw_label).lower()
        if not target:
            return None

        exact_matches: list[tuple[int, str]] = []
        fuzzy_matches: list[tuple[int, int, str]] = []

        for index, field_name in enumerate(field_names):
            candidate = cls._normalize_field_reference(field_name).lower()
            if not candidate:
                continue
            if candidate == target:
                exact_matches.append((0, field_name))
                continue
            if candidate in target or target in candidate:
                fuzzy_matches.append((abs(len(candidate) - len(target)), index, field_name))

        if exact_matches:
            exact_matches.sort(key=lambda item: (item[0], item[1]))
            return exact_matches[0][1]
        if fuzzy_matches:
            fuzzy_matches.sort(key=lambda item: (item[0], item[1], item[2]))
            return fuzzy_matches[0][2]
        return None

    @staticmethod
    def _normalize_expected(expected: str) -> str | None:
        text = str(expected or "").strip().lower()
        if text in {"pass", "accepted", "accept", "success", "succeed", "valid"}:
            return "pass"
        if text in {"fail", "failed", "reject", "rejected", "error", "invalid"}:
            return "fail"
        return None

    @staticmethod
    def _baseline_inputs(field_metas: dict[str, FieldMetadata]) -> dict[str, str]:
        return {
            field_name: str(field_meta.original_value or "")
            for field_name, field_meta in field_metas.items()
        }

    @classmethod
    def _is_uniqueness_sensitive_field(
        cls,
        field_name: str,
        field_meta: FieldMetadata,
    ) -> bool:
        label = cls._normalize_field_reference(field_name or field_meta.label).lower()
        if not label:
            return False

        if (field_meta.input_type or "").lower() == "email":
            return True

        unique_patterns = [
            r"\bemail\b",
            r"\bphone\b",
            r"\bmobile\b",
            r"\btelephone\b",
            r"\busername\b",
            r"\buser name\b",
            r"\blogin\b",
            r"\baccount\b",
            r"\bemployee id\b",
            r"\bstaff id\b",
            r"\buser id\b",
            r"\bmember id\b",
        ]
        return any(re.search(pattern, label) for pattern in unique_patterns)

    @classmethod
    def _scenario_suggests_create(cls, gherkin_context: dict | None) -> bool:
        if not isinstance(gherkin_context, dict):
            return False

        text_parts: list[str] = []
        for key in ("feature", "scenario"):
            value = gherkin_context.get(key)
            if value:
                text_parts.append(str(value))
        when_steps = gherkin_context.get("when", [])
        if isinstance(when_steps, list):
            text_parts.extend(str(step) for step in when_steps)

        haystack = " ".join(text_parts).lower()
        return any(
            phrase in haystack
            for phrase in [
                "add a new",
                "create a new",
                "create ",
                "add ",
                "new employee",
                "new user",
                "register",
                "invite",
                "sign up",
            ]
        )

    def _fresh_valid_value(
        self,
        field_name: str,
        field_meta: FieldMetadata,
    ) -> str:
        original_value = str(field_meta.original_value or "")
        label = self._normalize_field_reference(field_name or field_meta.label).lower()
        input_type = (field_meta.input_type or "text").lower()

        for partition in self._heuristic_fallbacks(field_meta):
            candidate = str(partition.value)
            if partition.category == "valid" and candidate and candidate != original_value:
                return candidate

        if input_type == "email" or "email" in label:
            local_part, _, domain = original_value.partition("@")
            if domain:
                safe_local = re.sub(r"[^a-zA-Z0-9._+-]", "", local_part) or "user"
                return f"{safe_local}+isp@example.com" if domain == "example.com" else f"{safe_local}+isp@{domain}"
            return "user+isp@example.com"

        if input_type == "phone" or any(token in label for token in ["phone", "mobile", "telephone"]):
            digits = re.sub(r"\D", "", original_value)
            if len(digits) >= 7:
                bumped = digits[:-1] + str((int(digits[-1]) + 1) % 10)
                return bumped
            return "+14155550123"

        if input_type == "number":
            if re.fullmatch(r"\s*-?\d+\s*", original_value):
                return str(int(original_value) + 1)
            return "1"

        base_text = re.sub(r"\s+", "_", original_value.strip()) or re.sub(r"[^a-z0-9]+", "_", label) or "value"
        return f"{base_text}_alt"

    @classmethod
    def _find_confirmation_pairs(cls, labels: list[str]) -> list[tuple[str, str]]:
        markers = ("confirm", "confirmation", "re-enter", "reenter", "retype", "verify", "verification")
        normalized = {
            label: cls._normalize_field_reference(label).lower()
            for label in labels
        }
        pairs: list[tuple[str, str]] = []
        seen_pairs: set = set()

        for confirm_label in labels:
            confirm_norm = normalized[confirm_label]
            if not any(marker in confirm_norm for marker in markers):
                continue

            base_key = confirm_norm
            for marker in markers:
                base_key = base_key.replace(marker, " ")
            base_key = re.sub(r"\s+", " ", base_key).strip()
            if not base_key:
                continue

            best_label = None
            best_score = None
            for candidate in labels:
                if candidate == confirm_label:
                    continue
                candidate_norm = normalized[candidate]
                if not candidate_norm:
                    continue

                if candidate_norm == base_key:
                    score = 0
                elif base_key in candidate_norm or candidate_norm in base_key:
                    score = abs(len(candidate_norm) - len(base_key)) + 1
                else:
                    continue

                if best_score is None or score < best_score:
                    best_score = score
                    best_label = candidate

            if best_label is None:
                continue

            pair = (best_label, confirm_label)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            pairs.append(pair)

        return pairs

    @classmethod
    def _find_date_range_pairs(cls, labels: list[str]) -> list[tuple[str, str]]:
        def _tokens(label: str) -> set[str]:
            normalized = cls._normalize_field_reference(label).lower()
            return {
                token
                for token in re.split(r"[^a-z0-9]+", normalized)
                if token and token not in {"date", "time", "period", "field"}
            }

        begin_markers = {"begin", "start", "from"}
        end_markers = {"end", "until", "to"}
        token_map = {label: _tokens(label) for label in labels}
        pairs: list[tuple[str, str]] = []

        for begin_label, begin_tokens in token_map.items():
            if not begin_tokens.intersection(begin_markers):
                continue

            begin_base = begin_tokens - begin_markers
            best: tuple[int, str] | None = None
            for end_label, end_tokens in token_map.items():
                if end_label == begin_label or not end_tokens.intersection(end_markers):
                    continue
                overlap = len(begin_base.intersection(end_tokens - end_markers))
                if overlap and (best is None or overlap > best[0]):
                    best = (overlap, end_label)

            if best:
                pairs.append((begin_label, best[1]))

        return pairs

    def _sanitize_test_case_inputs(
        self,
        raw_inputs,
        field_metas: dict[str, FieldMetadata],
    ) -> dict[str, str]:
        baseline_inputs = self._baseline_inputs(field_metas)
        if not isinstance(raw_inputs, dict):
            return dict(baseline_inputs)

        sanitized = dict(baseline_inputs)
        field_names = list(field_metas.keys())
        for raw_label, raw_value in raw_inputs.items():
            matched_label = self._find_best_field_name_match(str(raw_label), field_names)
            if matched_label is None:
                continue

            if isinstance(raw_value, dict):
                raw_value = raw_value.get("value", "")
            sanitized[matched_label] = str(raw_value or "")

        return sanitized

    def _infer_expected_from_inputs(
        self,
        inputs: dict[str, str],
        field_metas: dict[str, FieldMetadata],
    ) -> str:
        priority = {"fail": 0, "pass": 1}
        outcome = "pass"
        labels = list(field_metas.keys())

        for source_label, confirm_label in self._find_confirmation_pairs(labels):
            source_value = str(inputs.get(source_label, ""))
            confirm_value = str(inputs.get(confirm_label, ""))
            if source_value != confirm_value:
                return "fail"

        for label, field_meta in field_metas.items():
            value = str(inputs.get(label, field_meta.original_value or ""))
            original_value = str(field_meta.original_value or "")
            if value == original_value:
                continue

            category = None
            if value == "":
                category = "empty"
            else:
                for partition in self._heuristic_fallbacks(field_meta) + self._STATIC_FALLBACKS:
                    if str(partition.value) == value:
                        category = partition.category
                        break

            if category is None:
                input_type = (field_meta.input_type or "text").lower()
                if input_type == "email" and ("@" not in value or "." not in value.split("@")[-1]):
                    category = "invalid"
                elif input_type == "number" and not re.fullmatch(r"\s*-?\d+\s*", value):
                    category = "invalid"
                else:
                    category = "valid"

            mapped = self._normalize_expected(
                self.expected_outcomes_by_category.get(category, "")
            ) or "fail"
            if priority[mapped] < priority[outcome]:
                outcome = mapped

        return outcome

    def _build_heuristic_test_cases(
        self,
        field_metas: dict[str, FieldMetadata],
        max_cases: int,
        gherkin_context: dict | None = None,
    ) -> List[ISPTestCase]:
        labels = list(field_metas.keys())
        baseline_inputs = self._baseline_inputs(field_metas)
        valid_inputs = dict(baseline_inputs)
        test_cases: List[ISPTestCase] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        confirmation_pairs = self._find_confirmation_pairs(labels)
        date_range_pairs = self._find_date_range_pairs(labels)
        unique_labels = [
            label
            for label, field_meta in field_metas.items()
            if self._is_uniqueness_sensitive_field(label, field_meta)
        ]
        duplicate_expected = "fail"

        for label in unique_labels:
            valid_inputs[label] = self._fresh_valid_value(label, field_metas[label])

        def _append_case(name: str, inputs: dict[str, str], expected: str | None = None):
            normalized_inputs = {
                label: str(inputs.get(label, valid_inputs.get(label, baseline_inputs.get(label, ""))))
                for label in labels
            }
            signature = tuple((label, normalized_inputs[label]) for label in labels)
            if signature in seen:
                return

            resolved_expected = self._normalize_expected(expected or "")
            if resolved_expected is None:
                resolved_expected = self._infer_expected_from_inputs(
                    normalized_inputs,
                    field_metas,
                )

            seen.add(signature)
            test_cases.append(ISPTestCase(
                name=name or f"case {len(test_cases) + 1}",
                expected=resolved_expected,
                inputs=normalized_inputs,
            ))

        _append_case("baseline valid", valid_inputs, expected="pass")

        if unique_labels and len(test_cases) < max_cases:
            duplicate_inputs = dict(valid_inputs)
            for label in unique_labels:
                duplicate_inputs[label] = baseline_inputs.get(label, "")
            _append_case("existing unique value duplicate", duplicate_inputs, expected=duplicate_expected)

        for source_label, confirm_label in confirmation_pairs:
            if len(test_cases) >= max_cases:
                return test_cases[:max_cases]

            source_value = valid_inputs.get(source_label, "")
            mismatch_value = None
            for partition in self._heuristic_fallbacks(field_metas[source_label]):
                candidate = str(partition.value)
                if candidate and candidate != source_value:
                    mismatch_value = candidate
                    break
            if mismatch_value is None:
                mismatch_value = f"{source_value}1" if source_value else "MismatchValue1!"

            mismatch_inputs = dict(valid_inputs)
            mismatch_inputs[confirm_label] = mismatch_value
            _append_case(f"{confirm_label} mismatch", mismatch_inputs, expected="fail")

        for begin_label, end_label in date_range_pairs:
            if len(test_cases) >= max_cases:
                return test_cases[:max_cases]

            invalid_range_inputs = dict(valid_inputs)
            begin_value = valid_inputs.get(begin_label, "")
            end_value = valid_inputs.get(end_label, "")
            if begin_value and end_value and begin_value != end_value:
                invalid_range_inputs[begin_label] = end_value
                invalid_range_inputs[end_label] = begin_value
            else:
                invalid_range_inputs[begin_label] = "5/20/26"
                invalid_range_inputs[end_label] = "5/10/26"
            _append_case(f"{begin_label} after {end_label}", invalid_range_inputs, expected="fail")

        field_partitions = {
            label: self._heuristic_fallbacks(field_metas[label]) + self._STATIC_FALLBACKS
            for label in labels
        }
        max_partition_count = max((len(parts) for parts in field_partitions.values()), default=0)

        for partition_idx in range(max_partition_count):
            if len(test_cases) >= max_cases:
                break
            for label in labels:
                if len(test_cases) >= max_cases:
                    break
                partitions = field_partitions[label]
                if partition_idx >= len(partitions):
                    continue
                partition = partitions[partition_idx]
                candidate = str(partition.value)
                if candidate == valid_inputs.get(label, ""):
                    continue

                variant_inputs = dict(valid_inputs)
                variant_inputs[label] = candidate
                for source_label, confirm_label in confirmation_pairs:
                    if label == source_label:
                        variant_inputs[confirm_label] = candidate
                _append_case(f"{label} {partition.category}", variant_inputs)

        return test_cases[:max_cases]

    def _parse_test_case_llm_response(
        self,
        response: str,
        field_metas: dict[str, FieldMetadata],
    ) -> List[ISPTestCase]:
        payload = self._extract_json_array(response)
        if not payload:
            print("[ISPGenerator] Parse diagnostic: no valid top-level JSON array found.")
            return []

        parsed_cases: List[ISPTestCase] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        field_order = list(field_metas.keys())
        skipped_non_object = 0
        skipped_duplicate = 0

        for item in payload:
            if not isinstance(item, dict):
                skipped_non_object += 1
                continue

            inputs = self._sanitize_test_case_inputs(item.get("inputs", {}), field_metas)
            signature = tuple((label, inputs.get(label, "")) for label in field_order)
            if signature in seen:
                skipped_duplicate += 1
                continue

            expected = self._normalize_expected(item.get("expected", ""))
            if expected is None:
                expected = self._infer_expected_from_inputs(inputs, field_metas)

            name = str(item.get("name", "")).strip() or f"case {len(parsed_cases) + 1}"
            seen.add(signature)
            parsed_cases.append(ISPTestCase(
                name=name,
                expected=expected,
                inputs=inputs,
            ))

        print(
            "[ISPGenerator] Parse diagnostic: "
            f"payload_items={len(payload)}, accepted={len(parsed_cases)}, "
            f"skipped_non_object={skipped_non_object}, skipped_duplicate={skipped_duplicate}"
        )
        return parsed_cases
