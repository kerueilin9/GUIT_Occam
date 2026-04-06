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

from playwright.sync_api import CDPSession, Page

from evaluation_harness.helper_functions import generate_from_llm_chat_completion

try:
    from AgentOccam.gherkin_parser import GherkinParser
    _GHERKIN_PARSER_AVAILABLE = True
except Exception:
    _GHERKIN_PARSER_AVAILABLE = False


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
    page_snapshot = _get_page_snapshot(page, trajectory)

    prompt = _build_prompt(scenario_text, expected_hint, page_snapshot)

    try:
        response = generate_from_llm_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert QA engineer evaluating whether a web automation agent "
                        "successfully completed a task. Be objective and base your judgement solely "
                        "on the task description and the actual page state provided."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            model="auto",
            temperature=0,
            max_tokens=1200,
        )
        score, reason = _parse_response(response)
    except Exception as exc:
        print(f"[LLMJudge] ERROR during LLM call: {exc}")
        score, reason = 0.5, f"LLM evaluation failed: {exc}"

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
        # Manually inject ISP values
        isp_values = _extract_isp_values(isp_block)
        val_idx = 0
        _WITH_VALUE_RE = re.compile(r'\bwith\s+[\'"]', re.IGNORECASE)
        for step in when_steps:
            step = str(step)
            if "fill in" in step.lower() and val_idx < len(isp_values):
                if not _WITH_VALUE_RE.search(step):
                    step = f'{step} with "{isp_values[val_idx]}"'
                val_idx += 1
            lines.append(f"  When {step}")
    if isinstance(then, list):
        for s in then:
            lines.append(f"  Then {s}")

    return "\n".join(lines)


def _extract_isp_values(isp_block: dict) -> list[str]:
    """Extract ordered input values from isp_test_case, skipping meta keys."""
    values: list[str] = []
    for key, payload in isp_block.items():
        if key == "_expected":
            continue
        if isinstance(payload, dict):
            values.append(str(payload.get("value", "")))
        else:
            values.append(str(payload))
    return values


def _format_isp_values(isp_block: dict) -> str:
    """Format ISP block as readable key→value lines (for intent path)."""
    lines: list[str] = []
    for key, payload in isp_block.items():
        if key == "_expected":
            continue
        if isinstance(payload, dict):
            val = payload.get("value", "")
            cat = payload.get("category", "")
            desc = payload.get("description", "")
            lines.append(f"  - {key}: \"{val}\" (category: {cat}; {desc})")
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
# Page snapshot
# ---------------------------------------------------------------------------

def _extract_accessibility_tree_text(trajectory: list | None) -> str:
    """Extract the final accessibility-tree text from trajectory, if available."""
    if not isinstance(trajectory, list):
        return ""

    for entry in reversed(trajectory):
        if not isinstance(entry, dict) or "observation" not in entry:
            continue

        info = entry.get("info", {})
        if isinstance(info, dict):
            metadata = info.get("observation_metadata", {})
            if isinstance(metadata, dict):
                text_meta = metadata.get("text", {})
                if isinstance(text_meta, dict):
                    for key in ("accessibility_tree_text", "accessibility_tree"):
                        value = text_meta.get(key)
                        if isinstance(value, str) and value.strip():
                            return value

        observation = entry.get("observation", {})
        if isinstance(observation, dict):
            text_obs = observation.get("text")
            if isinstance(text_obs, (list, tuple)) and text_obs:
                candidate = text_obs[0]
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
            if isinstance(text_obs, str) and text_obs.strip():
                return text_obs

    return ""


def _get_page_snapshot(page: Page, trajectory: list | None = None) -> dict[str, str]:
    """Capture the current page state with accessibility-tree text as primary evidence."""
    snapshot: dict[str, str] = {
        "url": "",
        "title": "",
        "accessibility_tree_text": "",
        "body_text": "",
        "snapshot_source": "",
    }
    try:
        snapshot["url"] = page.url
    except Exception:
        pass
    try:
        snapshot["title"] = page.title()
    except Exception:
        pass

    a11y_text = _extract_accessibility_tree_text(trajectory)
    if a11y_text:
        snapshot["accessibility_tree_text"] = a11y_text[:12000]
        snapshot["snapshot_source"] = "accessibility_tree"

    try:
        snapshot["body_text"] = page.inner_text("body")[:3000]
    except Exception:
        pass

    if not snapshot["snapshot_source"]:
        snapshot["snapshot_source"] = "body_text"

    return snapshot


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(
    scenario_text: str,
    expected_hint: str | None,
    page_snapshot: dict[str, str],
) -> str:
    hint_section = f"\n\n{expected_hint}" if expected_hint else ""
    a11y_section = page_snapshot.get("accessibility_tree_text", "")
    if not a11y_section:
        a11y_section = "(Accessibility tree not available for this run.)"

    return f"""You are evaluating whether a web automation agent successfully completed the following task.

--- TASK SCENARIO ---
{scenario_text}{hint_section}

--- CURRENT PAGE STATE (after agent finished) ---
URL   : {page_snapshot['url']}
Title : {page_snapshot['title']}
Primary snapshot source: {page_snapshot.get('snapshot_source', 'unknown')}

Accessibility tree text (first 12000 chars):
{a11y_section}

Fallback body text (first 3000 chars):
{page_snapshot['body_text']}

--- YOUR TASK ---
Based on the scenario description and the actual page state above, judge whether the agent
completed the task correctly. Consider:
1. Did the agent perform the required actions (When steps)?
2. Do the acceptance criteria (Then steps) appear to be satisfied on the page?
3. For ISP scenarios: did the system respond appropriately (accept valid inputs / reject invalid ones)?
4. Use accessibility-tree evidence as primary ground truth. Use body text only as fallback context.

Respond with ONLY a JSON object in this exact format (no markdown, no extra text):
{{"score": <float 0.0-1.0>, "reason": "<one or two sentences explaining your judgement>"}}"""


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_response(response: str) -> tuple[float, str]:
    """Parse LLM response into (score, reason). Robust to minor formatting issues."""
    text = response.strip()

    # Try strict JSON parse
    match_json = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if match_json:
        try:
            parsed = json.loads(match_json.group(0))
            score = float(parsed.get("score", 0.5))
            reason = str(parsed.get("reason", "No reason provided."))
            return max(0.0, min(1.0, score)), reason
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: extract first float and use remaining text as reason
    score_match = re.search(r'(\d+\.?\d*)', text)
    score = float(score_match.group(1)) if score_match else 0.5
    score = max(0.0, min(1.0, score))
    reason = text if len(text) < 300 else text[:300] + "..."
    return score, reason
