"""
LLM Judge Evaluator for AgentOccam
====================================
Lets an LLM autonomously decide whether the agent achieved the task goal,
by reading the full Gherkin scenario (or intent text) together with the
current page state.

ISP Support
-----------
When the config contains an ``isp_test_case`` block, the input values are
injected into the Gherkin ``when`` steps (using the same logic as
``GherkinParser._inject_isp_values_into_when``) so that the LLM can see
the complete, value-filled scenario.

The ``isp_test_case._expected`` field ("pass" / "fail") is passed to the
LLM **as a reference hint only** – the LLM still makes its own judgement
based on the actual page state.

Output
------
Returns a float score in [0.0, 1.0].
Also prints to stdout:
    [LLMJudge] score: <score>
    [LLMJudge] reason: <reason>
"""

from __future__ import annotations

import json
import re
from typing import Any

from playwright.sync_api import Page

from AgentOccam.logger import logger
from evaluation_harness.evaluator_prompts import (
    LLM_JUDGE_SYSTEM_PROMPT,
    build_llm_judge_prompt,
)
from evaluation_harness.helper_functions import generate_from_llm_chat_completion
from evaluation_harness.page_snapshot import capture_page_screenshot, get_page_snapshot

try:
    from AgentOccam.gherkin_parser import GherkinParser
    _GHERKIN_PARSER_AVAILABLE = True
except Exception:
    _GHERKIN_PARSER_AVAILABLE = False


def _binary_score(score: float) -> float:
    """Convert any parsed score into the evaluator's binary 0/1 scale."""
    return 1.0 if score >= 0.5 else 0.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def llm_judge_evaluate(
    config: dict[str, Any],
    page: Page,
    trajectory: list | None = None,
) -> tuple[float, str]:
    """
    Evaluate whether the agent completed the task correctly.

    Parameters
    ----------
    config:
        Parsed task-config JSON dict.
    page:
        Live Playwright page at the end of the trajectory.
    trajectory:
        Agent trajectory used to extract the final accessibility-tree observation.

    Returns
    -------
    (score, reason)
        score  – float in [0.0, 1.0]
        reason – short natural-language explanation from the LLM
    """
    scenario_text = _build_scenario_text(config)
    expected_hint = _build_expected_hint(config)
    page_snapshot = get_page_snapshot(page, trajectory)

    prompt = build_llm_judge_prompt(scenario_text, expected_hint, page_snapshot)
    logger.debug(f"[LLMJudge] Text-only prompt:\n{prompt}")

    try:
        response = generate_from_llm_chat_completion(
            messages=[
                {"role": "system", "content": LLM_JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            model="auto",
            temperature=0,
            max_tokens=1200,
        )
        logger.debug(f"[LLMJudge] Text-only response:\n{response}")
        score, reason, needs_screenshot = _parse_response(response)

        if needs_screenshot:
            logger.debug(
                "[LLMJudge] Text-only judgement requested screenshot:\n"
                f"Score: {score}\n"
                f"Reason: {reason}"
            )
            screenshot_bytes = _try_capture_screenshot(page)
            if screenshot_bytes:
                try:
                    visual_prompt = build_llm_judge_prompt(
                        scenario_text,
                        expected_hint,
                        page_snapshot,
                        screenshot_attached=True,
                    )
                    logger.debug(
                        "[LLMJudge] Screenshot-assisted prompt:\n"
                        f"Screenshot bytes: {len(screenshot_bytes)}\n"
                        f"{visual_prompt}"
                    )
                    visual_response = generate_from_llm_chat_completion(
                        messages=[
                            {"role": "system", "content": LLM_JUDGE_SYSTEM_PROMPT},
                            {"role": "user", "content": visual_prompt},
                        ],
                        model="auto",
                        temperature=0,
                        max_tokens=1200,
                        image_bytes=screenshot_bytes,
                    )
                    logger.debug(
                        "[LLMJudge] Screenshot-assisted response:\n"
                        f"{visual_response}"
                    )
                    score, reason, _ = _parse_response(visual_response)
                    reason = f"{reason} (Used screenshot because text evidence was insufficient.)"
                except Exception as exc:
                    reason = f"{reason} Screenshot-assisted evaluation failed: {exc}"
            else:
                reason = f"{reason} Screenshot was requested but could not be captured."
    except Exception as exc:
        print(f"[LLMJudge] ERROR during LLM call: {exc}")
        score, reason = 0.0, f"LLM evaluation failed: {exc}"

    print(f"[LLMJudge] score: {score}")
    print(f"[LLMJudge] reason: {reason}")
    return score, reason


# ---------------------------------------------------------------------------
# Scenario text builder
# ---------------------------------------------------------------------------

def _build_scenario_text(config: dict[str, Any]) -> str:
    """
    Build a human-readable scenario description from config.

    Priority: intent > gherkin (with ISP injection if available).
    """
    intent: str | None = config.get("intent", None)

    # --- intent path ---
    if intent:
        isp_block = config.get("isp_test_case", {})
        if isp_block:
            # Append ISP values summary to intent so LLM knows the actual inputs
            value_lines = _format_isp_values(isp_block)
            if value_lines:
                intent = intent.rstrip(".") + ".\n\nISP test inputs:\n" + value_lines
        return intent

    # --- gherkin path ---
    gherkin = config.get("gherkin")
    if not gherkin:
        return "(No scenario description available)"

    isp_block = config.get("isp_test_case", {})

    if _GHERKIN_PARSER_AVAILABLE:
        try:
            # parse_from_dict handles isp_test_case injection internally
            scenario = GherkinParser.parse_from_dict(config)
            parts: list[str] = []
            if scenario.feature:
                parts.append(f"Feature: {scenario.feature}")
            if scenario.scenario:
                parts.append(f"Scenario: {scenario.scenario}")
            if scenario.given:
                parts.append("Given:\n" + "\n".join(f"  - {s}" for s in scenario.given))
            if scenario.when:
                parts.append("When:\n" + "\n".join(f"  - {s}" for s in scenario.when))
            if scenario.then:
                parts.append("Then (acceptance criteria):\n" + "\n".join(f"  - {s}" for s in scenario.then))
            return "\n".join(parts)
        except Exception:
            pass  # fall through to manual assembly

    # Fallback: manual assembly without parser
    return _manual_gherkin_text(gherkin, isp_block)


def _manual_gherkin_text(gherkin: dict | str, isp_block: dict) -> str:
    """Assemble gherkin text without the parser (fallback)."""
    if isinstance(gherkin, str):
        return gherkin

    lines: list[str] = []
    if gherkin.get("feature"):
        lines.append(f"Feature: {gherkin['feature']}")
    if gherkin.get("scenario"):
        lines.append(f"Scenario: {gherkin['scenario']}")

    given = gherkin.get("given", [])
    when_steps = gherkin.get("when", [])
    then = gherkin.get("then", [])

    if isinstance(given, list):
        for s in given:
            lines.append(f"  Given {s}")
    if isinstance(when_steps, list):
        injected_steps = [str(step) for step in when_steps]
        if isp_block:
            if _GHERKIN_PARSER_AVAILABLE:
                try:
                    injected_steps = GherkinParser._inject_isp_values_into_when(
                        injected_steps,
                        isp_block,
                    )
                except Exception:
                    injected_steps = _inject_isp_values_into_when_fallback(
                        injected_steps,
                        isp_block,
                    )
            else:
                injected_steps = _inject_isp_values_into_when_fallback(
                    injected_steps,
                    isp_block,
                )
        for step in injected_steps:
            lines.append(f"  When {step}")
    if isinstance(then, list):
        for s in then:
            lines.append(f"  Then {s}")

    return "\n".join(lines)


def _extract_isp_values(isp_block: dict) -> list[str]:
    """Extract ordered input values from isp_test_case, skipping meta keys."""
    values: list[str] = []
    for key, payload in isp_block.items():
        if str(key).startswith("_"):
            continue
        if isinstance(payload, dict):
            values.append(str(payload.get("value", "")))
        else:
            values.append(str(payload))
    return values


def _inject_isp_values_into_when_fallback(
    when_steps: list[str],
    isp_block: dict,
) -> list[str]:
    """Best-effort ISP injection when GherkinParser is unavailable."""
    values = _extract_isp_values(isp_block)
    if not values:
        return when_steps

    injected: list[str] = []
    value_idx = 0
    with_value_re = re.compile(r'\bwith\s+[\'"]', re.IGNORECASE)

    for raw_step in when_steps:
        step = str(raw_step)
        if "fill in" in step.lower() and value_idx < len(values):
            if not with_value_re.search(step):
                step = f'{step} with "{values[value_idx]}"'
            value_idx += 1
        injected.append(step)

    return injected


def _format_isp_values(isp_block: dict) -> str:
    """Format ISP block as readable key→value lines (for intent path)."""
    lines: list[str] = []
    for key, payload in isp_block.items():
        if str(key).startswith("_"):
            continue
        if isinstance(payload, dict):
            val = payload.get("value", "")
            cat = payload.get("category", "")
            desc = payload.get("description", "")
            if cat or desc:
                detail_parts = []
                if cat:
                    detail_parts.append(f"category: {cat}")
                if desc:
                    detail_parts.append(desc)
                lines.append(f'  - {key}: "{val}" ({"; ".join(detail_parts)})')
            else:
                lines.append(f'  - {key}: "{val}"')
        else:
            lines.append(f"  - {key}: {payload}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Expected-hint builder
# ---------------------------------------------------------------------------

def _build_expected_hint(config: dict[str, Any]) -> str | None:
    """
    Extract the _expected hint from isp_test_case if present.
    Returns a hint string, or None if not applicable.
    """
    isp_block = config.get("isp_test_case", {})
    expected = isp_block.get("_expected")
    if expected:
        return (
            f"NOTE (for reference only): The test designer expected this scenario to "
            f"'{expected}' — meaning the system should "
            + ("accept the input and complete the action." if expected == "pass"
               else "reject the input or show a validation error.")
        )
    return None


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_response(response: str) -> tuple[float, str, bool]:
    """Parse LLM response into (score, reason, needs_screenshot)."""
    text = response.strip()

    # Try strict JSON parse
    match_json = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if match_json:
        try:
            parsed = json.loads(match_json.group(0))
            score = _binary_score(float(parsed.get("score", 0.0)))
            reason = str(parsed.get("reason", "No reason provided."))
            needs_screenshot = _coerce_bool(parsed.get("needs_screenshot", False))
            return score, reason, needs_screenshot
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: extract first float and use remaining text as reason
    score_match = re.search(r'(\d+\.?\d*)', text)
    score = _binary_score(float(score_match.group(1))) if score_match else 0.0
    reason = text if len(text) < 300 else text[:300] + "..."
    return score, reason, _mentions_screenshot_need(reason)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _mentions_screenshot_need(text: str) -> bool:
    lower_text = text.lower()
    return (
        "need screenshot" in lower_text
        or "needs screenshot" in lower_text
        or ("cannot determine from" in lower_text and "text" in lower_text)
        or ("insufficient" in lower_text and "visual" in lower_text)
    )


def _try_capture_screenshot(page: Page) -> bytes | None:
    try:
        screenshot_bytes = capture_page_screenshot(page)
        logger.debug(
            "[LLMJudge] Captured final screenshot for evaluation: "
            f"{len(screenshot_bytes)} bytes"
        )
        return screenshot_bytes
    except Exception as exc:
        print(f"[LLMJudge] WARNING: Failed to capture final screenshot: {exc}")
        logger.debug(f"[LLMJudge] Failed to capture final screenshot: {exc}")
        return None
