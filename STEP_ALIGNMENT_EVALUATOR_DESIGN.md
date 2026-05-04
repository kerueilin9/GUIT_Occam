# Step Alignment Evaluator Design

## 1. Background

Current evaluators in this repo mainly judge:

- final answer text
- final URL
- final page content
- final acceptance criteria via `gherkin_criteria`
- final end-state judgement via `llm_judge`

What is still missing is a process-level evaluator that can answer:

"Did the actor's executed steps actually stay aligned with the task case?"

This gap is especially important for ISP tasks, because a run can end on a page that looks reasonable while still violating the testcase, for example:

- typing a different value than the ISP testcase specified
- skipping one required fill step
- clicking save too early
- correcting an intentionally invalid ISP value
- taking unrelated actions before submission

We want an evaluator that checks step-by-step alignment against the task case, not only the final page.


## 2. Goal

Add a new evaluator, tentatively named `step_alignment`, that scores whether the executed browser actions match the intended task steps.

Primary goals:

- verify that the actor followed the `gherkin.when` sequence closely enough
- verify that ISP fill values were entered exactly when the task is an ISP child task
- detect off-task or prematurely submitted trajectories
- emit a machine-readable report for debugging, not just a single float

Secondary goal:

- combine with existing evaluators such as `llm_judge` or `gherkin_criteria`


## 3. Non-Goals

V1 should not try to:

- replace end-state evaluators entirely
- prove DOM-perfect correctness for every micro action
- understand every possible workflow in a purely deterministic way
- require invasive changes to the browser environment before delivering value


## 4. Existing Inputs We Can Reuse

### 4.1 Task config

We already have:

- `gherkin.feature`
- `gherkin.scenario`
- `gherkin.given`
- `gherkin.when`
- `gherkin.then`
- optional `isp_test_case`

For ISP child tasks, the generated task JSON already contains:

- value-injected `gherkin.when`
- `isp_test_case._expected`
- per-field values in `isp_test_case`

That means the evaluator can use the task JSON itself as the source of truth for expected actions.

### 4.2 Trajectory

`env.py` currently stores a WebArena trajectory as alternating:

- state entries
- action entries

The action object already includes useful fields such as:

- `action_type`
- `element_id`
- `text`
- `answer`
- `raw_prediction`

The state entry already contains:

- accessibility-tree observation text
- page info

This is enough for a first version of step alignment.


## 5. Key Idea

The evaluator should compare two sequences:

1. expected task steps
2. observed executed actions

The comparison should be sequential and tolerant of a small number of harmless extra actions, but strict on the parts that matter:

- exact value matching for ISP fill steps
- reasonable ordering
- no silent replacement of testcase values
- no early submit before required fields are handled


## 6. Proposed Evaluator Type

New `eval_type`:

```json
{
  "eval": {
    "eval_types": ["step_alignment"]
  }
}
```

Recommended combined usage for ISP child tasks:

```json
{
  "eval": {
    "eval_types": ["step_alignment", "llm_judge"]
  }
}
```

Why combine:

- `step_alignment` checks process fidelity
- `llm_judge` checks whether the observed system response is acceptable

This split is clean: one checks "did the agent follow the testcase?", the other checks "did the system behave correctly?".


## 7. High-Level Architecture

Create a new module:

- `evaluation_harness/step_alignment_evaluator.py`

Add one router branch in:

- `evaluation_harness/evaluators.py`

No mandatory change is needed in `env.py` for V1.

Optional future improvement:

- enrich trajectory artifacts with pre/post observation snapshots or actor reasoning


## 8. Expected-Step Model

The evaluator first converts `gherkin.when` into normalized expected steps.

Example:

```text
I navigate to the Staff page
I add a new employee
I fill in First Name with "John"
I fill in Last Name with "Doe"
I fill in Email Address with "john.doe.new@example.com"
I fill in Password with "password123"
I fill in Confirm Password with "password123"
I save the new employee
```

Normalized expected steps:

1. `navigate` target=`Staff page`
2. `open/create` target=`new employee`
3. `fill` field=`First Name` value=`John`
4. `fill` field=`Last Name` value=`Doe`
5. `fill` field=`Email Address` value=`john.doe.new@example.com`
6. `fill` field=`Password` value=`password123`
7. `fill` field=`Confirm Password` value=`password123`
8. `submit/save` target=`new employee`

Suggested internal structure:

```python
{
  "index": 3,
  "kind": "fill",
  "raw_text": "I fill in Email Address with \"john.doe.new@example.com\"",
  "field_label": "Email Address",
  "expected_value": "john.doe.new@example.com",
  "required": True,
}
```


## 9. Observed-Action Model

The evaluator then converts trajectory actions into normalized observed actions.

Suggested internal structure:

```python
{
  "trajectory_index": 9,
  "action_index": 5,
  "kind": "type",
  "element_id": "1165",
  "typed_value": "john.doe.new@example.com",
  "raw_prediction": "type [1165] [john.doe.new@example.com] [1]",
  "target_label": "Email Address",
  "before_obs_text": "...",
  "after_obs_text": "...",
}
```

Important point:

`target_label` is not stored directly, but can usually be recovered from the previous state's accessibility-tree text by locating the line for the action's `element_id`.


## 10. Matching Strategy

### 10.1 Core principle

Do monotonic left-to-right matching:

- expected step order must not go backwards
- observed actions may contain a small number of extra actions
- once an expected step is matched, move to the next expected step

### 10.2 Fill-step matching

For `fill` steps, use deterministic matching.

A fill step is considered aligned only if:

- observed action kind is `type`
- target field label matches the expected field label
- typed value exactly matches the expected value

Label matching can use:

- normalized exact match
- singular/plural and punctuation-insensitive match
- containment match as fallback

Value matching for ISP should be exact string equality after minimal normalization.

This is the most important rule in the whole evaluator.

### 10.3 Navigate/open/save matching

For actions like:

- navigate
- click add/create/new
- save
- submit

use a hybrid approach:

- first try deterministic keyword matching using action type, target label, button text, and URL/page change
- if still ambiguous, optionally call a small LLM fallback on only that local step pair

This keeps fill validation strict while allowing navigation semantics to remain practical.


## 11. Proposed Scoring

The evaluator should return a float in `[0.0, 1.0]`, but also write a detailed report.

Suggested scoring components:

- `step_coverage_score`
  fraction of expected steps that were matched
- `fill_value_score`
  fraction of fill steps whose values exactly matched
- `order_score`
  whether the matched steps occurred in the expected order
- `divergence_penalty`
  penalty for too many unrelated actions before completion

Suggested weighted formula:

```text
overall =
  0.35 * step_coverage_score +
  0.40 * fill_value_score +
  0.15 * order_score +
  0.10 * submit_timing_score

overall *= divergence_penalty
```

For ISP tasks, `fill_value_score` should carry the highest weight.

### Hard-fail conditions for ISP

For ISP child tasks, any of the following may immediately force a very low score:

- a required fill step used the wrong value
- the actor changed an intentionally invalid testcase value
- submit happened before all required testcase fill steps were attempted

Recommended hard-fail output:

- score in the `0.0` to `0.2` range, depending on how much still aligned


## 12. Report Format

Besides returning a float, the evaluator should write a JSON report to something like:

- `output/step_alignment/<task_id>_step_alignment.json`

Suggested structure:

```json
{
  "task_id": "timeoff_task_staff01_isp_isp_01",
  "overall_score": 0.92,
  "summary": {
    "expected_steps": 8,
    "matched_steps": 8,
    "fill_steps": 5,
    "exact_fill_matches": 5,
    "off_task_actions": 0
  },
  "steps": [
    {
      "expected_index": 1,
      "expected_kind": "fill",
      "expected_field": "Email Address",
      "expected_value": "john.doe.new@example.com",
      "status": "matched",
      "matched_action_index": 5,
      "observed_value": "john.doe.new@example.com",
      "reason": "Exact field and value match"
    }
  ],
  "unmatched_expected_steps": [],
  "extra_actions": []
}
```

This report is critical, because the value of this evaluator is not only the final score but the ability to debug why the run diverged.


## 13. LLM Usage Strategy

### V1 recommendation

Use a hybrid strategy:

- deterministic for fill/value checks
- deterministic-first plus optional LLM fallback for semantic click/navigation steps

Why not fully LLM:

- expensive
- harder to debug
- worse at exact ISP value fidelity

Why not fully deterministic:

- generic action semantics like "I add a new employee" are often too fuzzy

### LLM fallback scope

Only call the LLM when deterministic matching cannot confidently classify a step such as:

- "I add a new employee"
- "I navigate to the Staff page"
- "I save the new employee"

The LLM prompt should be local and compact:

- one expected step
- one observed action
- short before/after accessibility-tree excerpts

This keeps cost and variance low.


## 14. Config Schema

Suggested extension:

```json
{
  "eval": {
    "eval_types": ["step_alignment", "llm_judge"],
    "step_alignment": {
      "mode": "hybrid",
      "strict_fill_values": true,
      "allow_extra_actions": 2,
      "llm_fallback": true,
      "report_dir": "output/step_alignment"
    }
  }
}
```

Recommended defaults:

- `mode = "hybrid"`
- `strict_fill_values = true`
- `allow_extra_actions = 1` for ISP tasks
- `llm_fallback = false` for first rollout if stability is preferred


## 15. Integration Points

### 15.1 Router

In `evaluation_harness/evaluators.py`:

- add `StepAlignmentEvaluator`
- add router case for `"step_alignment"`

### 15.2 New evaluator module

In `evaluation_harness/step_alignment_evaluator.py`:

- parse config
- extract expected steps
- extract observed actions from trajectory
- perform matching
- compute score
- save JSON report

### 15.3 Reusable helpers

Likely helper functions:

- `normalize_label(text)`
- `extract_expected_steps(config)`
- `extract_observed_actions(trajectory)`
- `resolve_element_label_from_obs(element_id, obs_text)`
- `match_fill_step(expected, observed)`
- `match_click_like_step(expected, observed)`
- `write_alignment_report(report, config_file, task_id)`


## 16. Recommended V1 Scope

Start narrow and useful.

V1 should officially support:

- Gherkin tasks with ordered `when` steps
- ISP child tasks with injected fill values
- action kinds: `click`, `type`, `goto`, `stop`

V1 may treat these as neutral or low-priority:

- scroll
- hover
- back
- tab switches

This is enough to cover the current ISP form workflow well.


## 17. Risks and Mitigations

### Risk 1: Field label recovery is imperfect

Problem:

- the action only stores `element_id`
- the label has to be reconstructed from accessibility-tree text

Mitigation:

- look up the matching line in the previous observation
- use neighboring static text lines when direct label text is missing
- fall back to `raw_prediction`
- if still unresolved, mark step as `uncertain` rather than wrong

### Risk 2: Repeated typing can look like misalignment

Problem:

- agent may type, clear, and type again

Mitigation:

- for a matched field, prefer the last relevant `type` action before submit
- keep all attempts in the report

### Risk 3: Generic semantic steps are ambiguous

Problem:

- "add a new employee" may be a click on a button, a modal open, or a route transition

Mitigation:

- use deterministic-first matching
- allow small LLM fallback only for ambiguous non-fill steps


## 18. Rollout Plan

### Phase 1

- implement deterministic ISP-focused step alignment
- support fill/value exact matching
- support basic click/save detection
- emit JSON report

### Phase 2

- add optional LLM fallback for ambiguous semantic steps
- tune penalties and thresholds using real trajectories

### Phase 3

- consider richer trajectory logging if needed
- optionally record actor plan/reason alongside executed actions


## 19. Recommendation

The cleanest direction is:

1. add a new `step_alignment` evaluator
2. make it deterministic-first
3. make ISP fill-value matching exact and strict
4. keep `llm_judge` as the end-state evaluator
5. save a per-step JSON report for debugging

In other words:

- `step_alignment` answers whether the actor followed the testcase
- `llm_judge` answers whether the SUT responded correctly

That separation matches the current architecture well and should be much easier to debug than trying to force one evaluator to do both jobs.


## 20. Suggested First Implementation Target

If we implement this next, the first target should be the current ISP child tasks under `config_files/timeoff/isp_tasks`.

Why:

- they already have injected `gherkin.when`
- they already have exact `isp_test_case` values
- success and failure cases are both present
- they are the clearest place where "step fidelity" matters

This will let us validate the design on a high-signal workflow before generalizing it to all task types.
