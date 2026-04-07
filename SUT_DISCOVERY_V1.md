# SUT Discovery and Task Generation V1

## Goal

Add a new pipeline that:

1. Explores a system under test (SUT) with a safe, low-cost policy.
2. Records visited screens as structured artifacts, not just screenshots.
3. Builds a screen graph from the discovered states and transitions.
4. Extracts reusable task seeds from the graph.
5. Synthesizes draft task configs that can later be validated by AgentOccam.

This pipeline should stay separate from the current task execution loop in
`eval_webarena.py`. The current loop assumes an existing task config, while the
new flow is responsible for producing task configs.

## Non-goals for V1

- Full autonomous CRUD coverage.
- Reliable form submission across arbitrary applications.
- Hard evaluators for every generated task.
- Replacing the current AgentOccam task runner.

V1 should focus on navigation-oriented task discovery and draft generation.

## Proposed Pipeline

### Phase 1: Explore

Use a dedicated explorer policy that aims to increase screen coverage rather
than solve a user task.

Recommended V1 constraints:

- Allow: `click`, `scroll`, `go_back`, safe `goto`.
- Prefer elements that look like links, menus, tabs, pagination, and search.
- Avoid destructive labels such as `delete`, `remove`, `logout`, `sign out`,
  `submit`, `save`, `confirm`.
- Do not submit forms in V1.

### Phase 2: Record

Each visited screen should produce a `ScreenRecord` with:

- `screen_id`
- `url`
- `title`
- `observation_text`
- `interactive_elements`
- `fingerprint`
- `screenshot_path`
- `metadata`

Each executed action should produce a `TransitionRecord` with:

- `from_screen_id`
- `to_screen_id`
- `action`
- `success`
- `error_message`

### Phase 3: Graph

Store discovery results as a screen graph:

- Node: one canonical screen/state
- Edge: one successful or failed transition

Nodes should be deduplicated by a screen fingerprint so repeated visits do not
inflate the graph.

### Phase 4: Extract Task Seeds

V1 task seeds should come from graph paths:

- root -> target screen
- no destructive actions
- target is meaningfully different from the root
- target has stable evidence such as title, URL path, heading, or landmark text

Examples:

- "Navigate from home page to Staff page"
- "Navigate from dashboard to employee details"
- "Open the leave request list"

### Phase 5: Synthesize Tasks

Generate draft task configs from seeds.

V1 should emit Gherkin-like draft tasks with `llm_judge` evaluation because:

- acceptance criteria are not always easy to hard-code,
- discovered targets may still be partially dynamic,
- `llm_judge` already exists in this repo.

### Phase 6: Validate

Later, generated tasks can be replayed through the normal AgentOccam flow:

- Run task once or multiple times.
- Keep tasks that are reproducible.
- Drop tasks with unstable targets or ambiguous end states.

## Recommended Module Layout

New package:

- `AgentOccam/discovery/models.py`
- `AgentOccam/discovery/recorder.py`
- `AgentOccam/discovery/screen_graph.py`
- `AgentOccam/discovery/explorer.py`
- `AgentOccam/discovery/task_synthesizer.py`
- `AgentOccam/discovery/pipeline.py`

## Output Layout

Suggested run output:

```text
output/discovery/<run_id>/
  manifest.json
  graph.json
  screens/
    <screen_id>.png
  states/
    <screen_id>.json
  tasks/
    <task_id>.json
```

## Integration Points with Existing Repo

- Browser runtime: `browser_env/envs.py`
- Existing task format: `config_files/*/*.json`
- Existing Gherkin support: `AgentOccam/gherkin_parser.py`
- Existing evaluator: `evaluation_harness/llm_judge_evaluator.py`
- Existing ISP extension: `AgentOccam/isp_generator.py`

## V1 Data Flow

```text
ExplorerPolicy
  -> ScreenRecorder
  -> ScreenGraph
  -> TaskSeed extraction
  -> TaskSynthesizer
  -> generated task configs
```

## Suggested Implementation Order

1. Record and dedupe screens.
2. Build graph and export artifacts.
3. Extract navigation seeds.
4. Generate deterministic draft tasks.
5. Optionally add LLM rewriting for task phrasing.
6. Add replay-based validation.

## Why This Shape Fits the Existing Codebase

- It does not overload `AgentOccam.py` further.
- It keeps discovery output reusable for debugging and manual review.
- It aligns well with the repo's existing JSON task configs.
- It lets you layer ISP on top of discovered form flows later, instead of
  coupling both concerns from day one.
