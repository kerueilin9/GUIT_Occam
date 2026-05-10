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
) -> str:
    hint_section = f"\n\n{expected_hint}" if expected_hint else ""
    a11y_section = _accessibility_section(page_snapshot)

    return f"""You are evaluating whether a web automation agent successfully completed the following task.

--- TASK SCENARIO ---
{scenario_text}{hint_section}

--- CURRENT PAGE STATE (after agent finished) ---
URL   : {page_snapshot['url']}
Title : {page_snapshot['title']}
Primary snapshot source: {page_snapshot.get('snapshot_source', 'unknown')}

Accessibility tree text (first 20000 chars):
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
5. For ISP scenarios with invalid inputs, note that some forms may clear or reset previously filled fields after validation fails. Treat this as a possible system response, not automatically as evidence that the agent skipped those inputs.

Scoring standard:
- 1.0: The result appears to match the expected outcome. The required task behavior was completed successfully, or for ISP scenarios the system response matches the expected pass/fail behavior.
- 0.0: The result appears not to match the expected outcome. The task was not completed correctly, or for ISP scenarios the system response contradicts the expected pass/fail behavior.
- Only use one of these two scores: 1.0 or 0.0.

Respond with ONLY a JSON object in this exact format (no markdown, no extra text):
{{"score": <0.0 or 1.0>, "reason": "<one or two sentences explaining your judgement>"}}"""


def build_gherkin_criterion_prompt(
    criterion: str,
    page_snapshot: PageSnapshot,
) -> str:
    a11y_section = _accessibility_section(page_snapshot)

    return f"""Evaluate the acceptance criterion and return JSON only.

Acceptance Criterion: {criterion}

Current Web Page:
- URL: {page_snapshot['url']}
- Title: {page_snapshot['title']}
- Primary snapshot source: {page_snapshot.get('snapshot_source', 'unknown')}

Accessibility tree text (first 20000 chars):
{a11y_section}

Fallback body text (first 3000 chars):
{page_snapshot['body_text']}

Scoring standard:
- 1.0: The criterion is satisfied by the page state.
- 0.0: The criterion is not satisfied by the page state.
- Only use one of these two scores: 1.0 or 0.0. Do not return partial credit.

Return strict JSON with keys:
{{"score": <0.0 or 1.0>, "comment": "<short reason in one sentence>"}}"""


def _accessibility_section(page_snapshot: PageSnapshot) -> str:
    return (
        page_snapshot.get("accessibility_tree_text", "")
        or "(Accessibility tree not available for this run.)"
    )
