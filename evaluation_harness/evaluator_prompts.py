"""Centralized prompt builders for evaluation harness LLM calls."""

from __future__ import annotations

from evaluation_harness.page_snapshot import PageSnapshot


ISP_EVALUATOR_SYSTEM_PROMPT = (
    "You are an expert QA engineer evaluating ISP-generated web form tests. "
    "Judge only observable form-fill and form-submit facts from the supplied "
    "page states."
)

GHERKIN_CRITERION_SYSTEM_PROMPT = (
    "You are an expert at evaluating web automation test results against "
    "Gherkin acceptance criteria."
)

def build_isp_evaluation_prompt(
    scenario_text: str,
    page_snapshot: PageSnapshot,
    screenshot_attached: bool = False,
    pre_submit_snapshot: PageSnapshot | None = None,
    isp_expected_score: float | None = None,
) -> str:
    a11y_section = _accessibility_section(page_snapshot)
    visual_section = _visual_section(screenshot_attached)
    pre_submit_section = _pre_submit_section(pre_submit_snapshot)
    isp_metric_section = _isp_metric_section(isp_expected_score)

    return f"""You are evaluating an ISP-generated form submission test.

--- TEST SCENARIO ---
{scenario_text}
{isp_metric_section}
{pre_submit_section}

--- CURRENT PAGE STATE (after agent finished) ---
URL   : {page_snapshot['url']}
Title : {page_snapshot['title']}
Primary snapshot source: {page_snapshot.get('snapshot_source', 'unknown')}

Page state text (accessibility tree first 20000 chars, or body text fallback):
{a11y_section}
{visual_section}

--- YOUR TASK ---
Judge exactly two observable facts:

1. actual_fill_success
- Return 1.0 only if the pre-submit page state shows that the relevant form fields actually retained the intended ISP test values before submission.
- Return 0.0 if any intended value was cleared, replaced, normalized to a non-equivalent value, reverted to a default/current value, or cannot be confirmed from the pre-submit page state.
- Do not assume a value was filled successfully merely because the scenario or action trace says it was typed.
- When returning 0.0, identify the problematic field(s) by label/name when possible, and include both the intended ISP value and the observed pre-submit value or state.

2. submit_success_score
- Return 1.0 if the final page shows that the form submission succeeded, such as a success message, created/saved/updated record, redirect to a success/list/detail page, or persisted data.
- Return 0.0 if the final page shows validation errors, rejection, unchanged form, no created/saved/updated data, or an unresolved failed state.
- Do not compare submit_success_score with isp_expected_score. Downstream code will compute that comparison.

Reason requirements:
- Clearly explain the evidence for both actual_fill_success and submit_success_score.
- If any field was not correctly filled, name the field and state the mismatch in the form "field: expected X, observed Y" when the evidence is available.
- Mention values that were auto-normalized, reverted to defaults/current dates, cleared, missing, or unverifiable.
- Keep the reason concise but specific; use two to four sentences if needed.

If page-state text alone is insufficient because the result depends on visible layout, graphics, color, canvas/image content, or other visual evidence, set "needs_screenshot" to true. Otherwise set it to false.

Respond with ONLY a JSON object that Python json.loads can parse. Do not use markdown. Keep "reason" on one line:
{{"actual_fill_success": <0.0 or 1.0>, "submit_success_score": <0.0 or 1.0>, "reason": "<specific evidence-based explanation, including field mismatches when present>", "needs_screenshot": <true or false>}}"""


def build_gherkin_criterion_prompt(
    criterion: str,
    page_snapshot: PageSnapshot,
    screenshot_attached: bool = False,
    trajectory_evidence: str | None = None,
) -> str:
    a11y_section = _accessibility_section(page_snapshot)
    visual_section = _visual_section(screenshot_attached)
    trajectory_section = _trajectory_section(trajectory_evidence)

    return f"""Evaluate the acceptance criterion and return JSON only.

Acceptance Criterion: {criterion}

Current Web Page:
- URL: {page_snapshot['url']}
- Title: {page_snapshot['title']}
- Primary snapshot source: {page_snapshot.get('snapshot_source', 'unknown')}

Page state text (accessibility tree first 20000 chars, or body text fallback):
{a11y_section}
{visual_section}
{trajectory_section}

Scoring standard:
- 1.0: The criterion is satisfied by the page state.
- 0.0: The criterion is not satisfied by the page state.
- Only use one of these two scores: 1.0 or 0.0. Do not return partial credit.
- If page-state text alone is insufficient because the criterion depends on visible layout, graphics, color, canvas/image content, or other visual evidence, set "needs_screenshot" to true. Otherwise set it to false.
- For disappearance/removal criteria, do not infer success from final absence alone. Require supporting evidence that the target item existed earlier or was created during the run, and that the final page no longer contains that same item.

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


def _pre_submit_section(pre_submit_snapshot: PageSnapshot | None) -> str:
    if not pre_submit_snapshot:
        return """

--- PRE-SUBMIT PAGE STATE (before the final submit/save action) ---
Not available. Do not use the current/final page as pre-submit evidence. If the
intended field values cannot be confirmed from a pre-submit state, return
actual_fill_success as 0.0 and explain that the pre-submit field values are
unverifiable.
"""

    return f"""

--- PRE-SUBMIT PAGE STATE (before the final submit/save action) ---
URL   : {pre_submit_snapshot.get('url', '')}
Title : {pre_submit_snapshot.get('title', '')}
Primary snapshot source: {pre_submit_snapshot.get('snapshot_source', 'unknown')}

Pre-submit page state text:
{_accessibility_section(pre_submit_snapshot)}
"""


def _isp_metric_section(isp_expected_score: float | None) -> str:
    if isp_expected_score is None:
        return ""

    return f"""

--- ISP EXPECTATION ---
isp_expected_score: {isp_expected_score}
This value is generated by the ISP test case: pass is 1.0 and fail is 0.0.
You should not change this value. It is provided so downstream code can compare it with submit_success_score.
"""


def _trajectory_section(trajectory_evidence: str | None) -> str:
    if not trajectory_evidence:
        return ""

    return f"\n\n{trajectory_evidence}"
