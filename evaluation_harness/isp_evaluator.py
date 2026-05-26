"""ISP evaluator for AgentOccam form-submission tests."""

from __future__ import annotations

import json
import re
from typing import Any

from playwright.sync_api import Page

from AgentOccam.logger import logger
from evaluation_harness.evaluator_prompts import (
    ISP_EVALUATOR_SYSTEM_PROMPT,
    build_isp_evaluation_prompt,
)
from evaluation_harness.helper_functions import generate_from_llm_chat_completion
from evaluation_harness.page_snapshot import (
    PageSnapshot,
    capture_page_screenshot,
    extract_accessibility_tree_text,
    get_page_snapshot,
)

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

def isp_evaluate(
    config: dict[str, Any],
    page: Page,
    trajectory: list | None = None,
    *,
    return_metrics: bool = False,
) -> tuple[float, str] | tuple[float, str, dict[str, float]]:
    """
    Evaluate an ISP-generated form submission test.

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
        score  – effective ISP score in [0.0, 1.0]
        reason – short natural-language explanation from the LLM
    """
    scenario_text = _build_scenario_text(config)
    page_snapshot = get_page_snapshot(page, trajectory)
    isp_expected_score = _isp_expected_score(config)
    if isp_expected_score is None:
        raise ValueError("ISPEvaluator requires isp_test_case._expected to be 'pass' or 'fail'.")

    pre_submit_snapshot = (
        _get_pre_submit_snapshot(trajectory)
    )
    metrics: dict[str, float] = {}

    prompt = build_isp_evaluation_prompt(
        scenario_text,
        page_snapshot,
        pre_submit_snapshot=pre_submit_snapshot,
        isp_expected_score=isp_expected_score,
    )
    logger.debug(f"[ISPEvaluator] Text-only prompt:\n{prompt}")

    try:
        response = generate_from_llm_chat_completion(
            messages=[
                {"role": "system", "content": ISP_EVALUATOR_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            model="auto",
            temperature=0,
            max_tokens=1200,
        )
        logger.debug(f"[ISPEvaluator] Text-only response:\n{response}")
        reason, needs_screenshot, llm_metrics = _parse_response(response)
        metrics = _build_isp_metrics(isp_expected_score, llm_metrics)
        score = metrics["effective_isp_score"]

        if needs_screenshot:
            logger.debug(
                "[ISPEvaluator] Text-only judgement requested screenshot:\n"
                f"Score: {score}\n"
                f"Reason: {reason}"
            )
            screenshot_bytes = _try_capture_screenshot(page)
            if screenshot_bytes:
                try:
                    visual_prompt = build_isp_evaluation_prompt(
                        scenario_text,
                        page_snapshot,
                        screenshot_attached=True,
                        pre_submit_snapshot=pre_submit_snapshot,
                        isp_expected_score=isp_expected_score,
                    )
                    logger.debug(
                        "[ISPEvaluator] Screenshot-assisted prompt:\n"
                        f"Screenshot bytes: {len(screenshot_bytes)}\n"
                        f"{visual_prompt}"
                    )
                    visual_response = generate_from_llm_chat_completion(
                        messages=[
                            {"role": "system", "content": ISP_EVALUATOR_SYSTEM_PROMPT},
                            {"role": "user", "content": visual_prompt},
                        ],
                        model="auto",
                        temperature=0,
                        max_tokens=1200,
                        image_bytes=screenshot_bytes,
                    )
                    logger.debug(
                        "[ISPEvaluator] Screenshot-assisted response:\n"
                        f"{visual_response}"
                    )
                    reason, _, llm_metrics = _parse_response(visual_response)
                    metrics = _build_isp_metrics(isp_expected_score, llm_metrics)
                    score = metrics["effective_isp_score"]
                    reason = f"{reason} (Used screenshot because text evidence was insufficient.)"
                except Exception as exc:
                    reason = f"{reason} Screenshot-assisted evaluation failed: {exc}"
            else:
                reason = f"{reason} Screenshot was requested but could not be captured."
    except Exception as exc:
        print(f"[ISPEvaluator] ERROR during LLM call: {exc}")
        score, reason = 0.0, f"ISP evaluation failed: {exc}"
        metrics = _build_isp_metrics(isp_expected_score, {})

    print(f"[ISPEvaluator] score: {score}")
    print(f"[ISPEvaluator] reason: {reason}")
    if metrics:
        print(f"[ISPEvaluator] metrics: {metrics}")
    if return_metrics:
        return score, reason, metrics
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
# Response parser
# ---------------------------------------------------------------------------

def _parse_response(response: str) -> tuple[str, bool, dict[str, float]]:
    """Parse LLM response into reason, screenshot flag, and ISP metrics."""
    text = response.strip()

    # Try strict JSON parse
    match_json = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if match_json:
        try:
            parsed = json.loads(match_json.group(0))
            reason = str(parsed.get("reason", "No reason provided."))
            needs_screenshot = _coerce_bool(parsed.get("needs_screenshot", False))
            metrics = {
                key: _parse_binary_value(parsed[key])
                for key in ("actual_fill_success", "submit_success_score")
                if key in parsed
            }
            return reason, needs_screenshot, metrics
        except (json.JSONDecodeError, ValueError):
            pass

    reason = _extract_json_string_field(text, "reason") or (
        text if len(text) < 300 else text[:300] + "..."
    )
    needs_screenshot = _coerce_bool(
        _extract_json_bool_field(text, "needs_screenshot")
    )
    metrics: dict[str, float] = {}
    for key in ("actual_fill_success", "submit_success_score"):
        value = _extract_json_number_field(text, key)
        if value is not None:
            metrics[key] = _parse_binary_value(value)
    return reason, needs_screenshot or _mentions_screenshot_need(reason), metrics


def _extract_json_number_field(text: str, key: str) -> float | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*([01](?:\.0)?)', text)
    return float(match.group(1)) if match else None


def _extract_json_bool_field(text: str, key: str) -> bool | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(true|false)', text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def _extract_json_string_field(text: str, key: str) -> str:
    pattern = rf'"{re.escape(key)}"\s*:\s*"(?P<value>.*?)"\s*(?:,\s*"[A-Za-z_]+":|\s*\}})'
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return ""
    value = match.group("value")
    value = value.replace('\\"', '"').replace("\\n", "\n")
    return re.sub(r"\s+", " ", value).strip()


def _build_isp_metrics(
    isp_expected_score: float,
    llm_metrics: dict[str, float],
) -> dict[str, float]:
    actual_fill_success = _binary_score(
        float(llm_metrics.get("actual_fill_success", 0.0))
    )
    submit_success_score = _binary_score(
        float(llm_metrics.get("submit_success_score", 0.0))
    )
    expected_match_score = 1.0 if submit_success_score == isp_expected_score else 0.0
    effective_isp_score = (
        1.0 if actual_fill_success == 1.0 and expected_match_score == 1.0 else 0.0
    )
    return {
        "actual_fill_success": actual_fill_success,
        "isp_expected_score": isp_expected_score,
        "submit_success_score": submit_success_score,
        "expected_match_score": expected_match_score,
        "effective_isp_score": effective_isp_score,
    }


def _parse_binary_value(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "pass", "success", "successful"}:
            return 1.0
        if lowered in {"false", "no", "fail", "failure", "failed"}:
            return 0.0
    return _binary_score(float(value))


def _isp_expected_score(config: dict[str, Any]) -> float | None:
    isp_block = config.get("isp_test_case", {})
    if not isinstance(isp_block, dict):
        return None
    expected = str(isp_block.get("_expected", "")).strip().lower()
    if expected == "pass":
        return 1.0
    if expected == "fail":
        return 0.0
    return None


def _get_pre_submit_snapshot(trajectory: list | None) -> PageSnapshot | None:
    state_entry = _find_pre_submit_state(trajectory)
    if not state_entry:
        logger.debug("[ISPEvaluator] Pre-submit snapshot unavailable.")
        return None

    accessibility_tree_text = extract_accessibility_tree_text([state_entry])
    if not accessibility_tree_text:
        logger.debug("[ISPEvaluator] Pre-submit state had no accessibility text.")
        return None

    logger.debug(
        "[ISPEvaluator] Pre-submit snapshot selected:\n"
        f"URL: {_state_url(state_entry)}\n"
        f"{accessibility_tree_text[:1000]}"
    )

    return {
        "url": _state_url(state_entry),
        "title": "",
        "accessibility_tree_text": accessibility_tree_text[:20000],
        "body_text": "",
        "snapshot_source": "pre_submit_accessibility_tree",
    }


def _find_pre_submit_state(trajectory: list | None) -> dict[str, Any] | None:
    if not isinstance(trajectory, list):
        return None

    states = [entry for entry in trajectory if _is_state_entry(entry)]
    _log_state_candidates(states)
    if len(states) < 2:
        logger.debug("[ISPEvaluator] No reliable pre-submit state found in trajectory.")
        return None

    selected_index = len(states) - 2
    selected = states[selected_index]
    logger.debug(
        "[ISPEvaluator] Selected pre-submit state as the page before final: "
        f"state_index={selected_index + 1}/{len(states)}, "
        f"url={_state_url(selected)}"
    )
    _log_state_preview("[ISPEvaluator] Selected pre-submit preview", selected)
    return selected


def _log_state_candidates(states: list[dict[str, Any]]) -> None:
    if not states:
        logger.debug("[ISPEvaluator] Trajectory contains no state entries.")
        return

    start = max(0, len(states) - 5)
    for idx, state in enumerate(states[start:], start=start + 1):
        text = extract_accessibility_tree_text([state]).replace("\n", " ")[:300]
        logger.debug(
            "[ISPEvaluator] State candidate "
            f"{idx}/{len(states)} url={_state_url(state)} preview={text}"
        )


def _log_state_preview(label: str, state: dict[str, Any]) -> None:
    text = extract_accessibility_tree_text([state])
    logger.debug(f"{label}:\nURL: {_state_url(state)}\n{text[:1200]}")


def _state_url(state_entry: dict[str, Any]) -> str:
    info = state_entry.get("info", {})
    if not isinstance(info, dict):
        return ""
    for key in ("url", "page_url", "current_url"):
        value = info.get(key)
        if isinstance(value, str):
            return value
    return ""


def _is_state_entry(entry: Any) -> bool:
    return isinstance(entry, dict) and "observation" in entry


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
            "[ISPEvaluator] Captured final screenshot for evaluation: "
            f"{len(screenshot_bytes)} bytes"
        )
        return screenshot_bytes
    except Exception as exc:
        print(f"[ISPEvaluator] WARNING: Failed to capture final screenshot: {exc}")
        logger.debug(f"[ISPEvaluator] Failed to capture final screenshot: {exc}")
        return None
