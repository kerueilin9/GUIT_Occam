"""Centralized prompt builders for evaluation harness LLM calls."""

from __future__ import annotations

from evaluation_harness.page_snapshot import PageSnapshot


LLM_JUDGE_SYSTEM_PROMPT = (
    "You are an expert QA engineer evaluating whether a web automation agent "
    "successfully completed a task. Be objective and base your judgement solely "
    "on the task description and the actual page state provided."
)

GHERKIN_CRITERION_SYSTEM_PROMPT = (
    "You are an expert at evaluating web automation test results against "
    "Gherkin acceptance criteria."
)

def build_llm_judge_prompt(
    scenario_text: str,
    expected_hint: str | None,
    page_snapshot: PageSnapshot,
    screenshot_attached: bool = False,
) -> str:
    hint_section = f"\n\n{expected_hint}" if expected_hint else ""
    a11y_section = _accessibility_section(page_snapshot)
    visual_section = _visual_section(screenshot_attached)

    return f"""You are evaluating whether a web automation agent successfully completed the following task.

--- TASK SCENARIO ---
{scenario_text}{hint_section}

--- CURRENT PAGE STATE (after agent finished) ---
URL   : {page_snapshot['url']}
Title : {page_snapshot['title']}
Primary snapshot source: {page_snapshot.get('snapshot_source', 'unknown')}

Page state text (accessibility tree first 20000 chars, or body text fallback):
{a11y_section}
{visual_section}

--- YOUR TASK ---
Based on the scenario description and the actual page state above, judge whether the agent
completed the task correctly. Consider:
1. Did the agent perform the required actions (When steps)?
2. Do the acceptance criteria (Then steps) appear to be satisfied on the page?
3. For ISP scenarios: did the system respond appropriately (accept valid inputs / reject invalid ones)?
4. Use the provided page-state evidence as ground truth. It uses the accessibility tree when available and body text only when the accessibility tree is unavailable.
5. For ISP scenarios with invalid inputs, note that some forms may clear or reset previously filled fields after validation fails. Treat this as a possible system response, not automatically as evidence that the agent skipped those inputs.
6. If the page-state text alone is insufficient because the result depends on visible layout, graphics, color, canvas/image content, or other visual evidence, set "needs_screenshot" to true. Otherwise set it to false.

Scoring standard:
- 1.0: The result appears to match the expected outcome. The required task behavior was completed successfully, or for ISP scenarios the system response matches the expected pass/fail behavior.
- 0.0: The result appears not to match the expected outcome. The task was not completed correctly, or for ISP scenarios the system response contradicts the expected pass/fail behavior.
- Only use one of these two scores: 1.0 or 0.0.

Respond with ONLY a JSON object in this exact format (no markdown, no extra text):
{{"score": <0.0 or 1.0>, "reason": "<one or two sentences explaining your judgement>", "needs_screenshot": <true or false>}}"""


def build_gherkin_criterion_prompt(
    criterion: str,
    page_snapshot: PageSnapshot,
    screenshot_attached: bool = False,
) -> str:
    a11y_section = _accessibility_section(page_snapshot)
    visual_section = _visual_section(screenshot_attached)

    return f"""Evaluate the acceptance criterion and return JSON only.

Acceptance Criterion: {criterion}

Current Web Page:
- URL: {page_snapshot['url']}
- Title: {page_snapshot['title']}
- Primary snapshot source: {page_snapshot.get('snapshot_source', 'unknown')}

Page state text (accessibility tree first 20000 chars, or body text fallback):
{a11y_section}
{visual_section}

Scoring standard:
- 1.0: The criterion is satisfied by the page state.
- 0.0: The criterion is not satisfied by the page state.
- Only use one of these two scores: 1.0 or 0.0. Do not return partial credit.
- If page-state text alone is insufficient because the criterion depends on visible layout, graphics, color, canvas/image content, or other visual evidence, set "needs_screenshot" to true. Otherwise set it to false.

Return strict JSON with keys:
{{"score": <0.0 or 1.0>, "comment": "<short reason in one sentence>", "needs_screenshot": <true or false>}}"""


def _accessibility_section(page_snapshot: PageSnapshot) -> str:
    accessibility_tree_text = page_snapshot.get("accessibility_tree_text", "")
    if accessibility_tree_text:
        return accessibility_tree_text

    body_text = page_snapshot.get("body_text", "")
    if body_text:
        return f"(Accessibility tree not available; using fallback body text.)\n{body_text}"

    return "(Accessibility tree and body text are not available for this run.)"


def _visual_section(screenshot_attached: bool) -> str:
    if not screenshot_attached:
        return ""

    return (
        "\n\nVisual evidence:\n"
        "A final-page screenshot is attached. Use it only to resolve information "
        "that cannot be determined from the page-state text, then set "
        '"needs_screenshot" to false.'
    )
