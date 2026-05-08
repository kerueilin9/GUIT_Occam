# adk_playwright_agent Project Context

This document summarizes `adk_playwright_agent` for a thesis-writing agent. It
focuses on the project's research role, workflow, artifacts, and boundaries.

## One-Sentence Summary

`adk_playwright_agent` is an ADK-based draft task generation agent that explores
a web application under test, collects route and page evidence with
`playwright-cli`, uses Vertex/Gemini to propose task-shaped draft test cases,
and hands those drafts to humans for refinement before another agent executes
end-to-end tests.

## Research Positioning

The project is not intended to be the final end-to-end testing agent. Its main
role is an upstream task-authoring assistant:

```text
Web SUT
-> adk_playwright_agent
-> route/page artifacts
-> draft task backlog
-> human review and completion
-> executable task set
-> downstream E2E testing agent
```

In a thesis, it can be described as a system for converting an initially
unknown web application into structured, reviewable testing tasks. It reduces
manual effort in discovering pages, summarizing UI functionality, and drafting
candidate test tasks, while still keeping humans responsible for final task
quality and test-data decisions.

## Core Goal

The project generates draft tasks for generic web systems under test (SUTs).
Those drafts are close to the downstream task JSON format, but they are not
claimed to be final executable tests. A human is expected to inspect them,
correct weak steps, add realistic input data, refine assertions, and remove
unsafe or low-value tasks before execution by another agent.

## Main Technologies

- Google ADK: hosts the root agent and exposes Python functions as tools.
- `playwright-cli`: drives browser sessions, navigation, snapshots, clicks,
  fills, JavaScript evaluation, and storage-state save/load.
- Vertex/Gemini via `google-genai`: summarizes observed pages and drafts test
  ideas from page artifacts.
- JSON/YAML artifacts: preserve route manifests, observations, summaries,
  drafts, and backlogs for auditability and handoff.

## System Design

The project has two main tracks.

### 1. Route Coverage Track

This track is deterministic and manifest-first:

```text
start_url
-> crawl_site_to_manifest / crawl_authenticated_site_to_manifest
-> route_manifest.*.json
-> generate_tasks_from_manifest
-> task_*.json
-> validate_task_directory
```

It performs bounded same-origin crawling, separately for guest and authenticated
states. It writes route manifests containing discovered routes, labels,
page types, navigation steps, assertions, login requirements, skipped routes,
and crawl errors. It can then generate route-level navigation tasks and validate
their required schema fields.

### 2. Draft Case Track

This track is evidence-first and LLM-assisted:

```text
route_manifest
-> build_action_discovery_worklist
-> observe_task_pages_from_worklist
-> page_observations/*.yml
-> summarize_pages_with_vertex
-> page_summaries/*.summary.json
-> draft_test_ideas_with_vertex
-> page_drafts/*.drafts.json
-> merge_page_drafts
-> draft_backlog.json
```

It first canonicalizes route coverage into a worklist, folding query-string
variants such as `/calendar/teamview?department=1` into `/calendar/teamview`.
Then it opens each canonical page and records browser evidence, especially the
raw `playwright-cli snapshot`, visible forms, tables, headings, baseline URL,
and route provenance. Vertex/Gemini summarizes each page and proposes draft
task ideas grounded in that evidence. The tool layer normalizes retained ideas
into task-shaped JSON and merges them into a deduplicated `draft_backlog.json`.

## Important Artifacts

- `route_manifest.guest.*.json`: routes reachable without login.
- `route_manifest.auth.*.json`: routes reachable after login.
- `generated_tasks/guest` and `generated_tasks/auth`: route navigation task
  JSON files generated from stable manifests.
- `action_worklist*.json`: canonical page list used for draft discovery.
- `page_observations/*.yml`: evidence captured from live pages.
- `page_summaries/*.summary.json`: human-readable page summaries.
- `page_drafts/*.drafts.json`: per-page task-shaped draft cases.
- `draft_backlog.json`: merged draft task backlog for human refinement.

## Draft Task Shape

The draft tasks resemble the downstream task format. Each task usually contains:

- `sites`
- `task_id`
- `require_login`
- `storage_state`
- `start_url`
- `gherkin.given`
- `gherkin.when`
- `gherkin.then`
- `eval.reference_answers.gherkin_acceptance_criteria`

The intended categories are `create`, `edit`, `delete`, `filter`, and `search`.
The system intentionally filters out or deprioritizes pure navigation/open
drafts because route navigation is already covered by the route track.

## Safety and Scope

The project avoids unsafe routes by default, including logout/signout,
delete/remove/reset, download/export/upload/import, and suspicious invalid query
routes such as `NaN` or `undefined`. Destructive tasks may appear as drafts if
visible and valuable, but they are treated as human-review items rather than
automatically executable workflows.

The project is SUT-neutral. Profiles in `profiles/sut_profiles.json` provide
short-prompt defaults for systems such as TimeOff, NodeBB, KeystoneJS, and
Spring PetClinic, but the crawler and draft pipeline are designed to remain
generic rather than hard-coded to one application.

## Current Boundaries

The stable output boundary is:

- route navigation tasks
- page summaries
- page-level draft cases
- deduplicated draft backlog

The project does not currently claim to produce final, fully executable
create/edit/delete E2E tests. Draft quality still depends on page observation
quality, prompt quality, and human refinement. Route discovery is more mature
and deterministic than action draft generation.

## Suggested Thesis Description

This project can be introduced as an intermediate task generation layer in an
agentic web testing pipeline. Instead of requiring a human to manually inspect
all pages and author every task from scratch, the agent explores the target
application, records structured evidence, and produces a reviewable draft task
backlog. Human reviewers then convert this backlog into reliable executable
tasks, which are passed to a separate E2E web agent for actual test execution.

## Key Files for Reference

- `agent.py`: ADK root agent and tool registration.
- `tools/crawler_tools.py`: guest/auth crawling and route manifest generation.
- `tools/generator_tools.py`: route task generation.
- `tools/action_task_tools.py`: worklist construction, page observation, and
  backlog normalization helpers.
- `tools/page_summary_tools.py`: Vertex-backed page summaries.
- `tools/draft_case_tools.py`: Vertex-backed draft task generation and merge.
- `tools/workflow_tools.py`: manifest-first route workflow wrapper.
- `adapters/playwright_cli.py`: subprocess adapter around `playwright-cli`.
- `adapters/vertex_genai.py`: Vertex/Gemini JSON generation adapter.
- `profiles/sut_profiles.json`: reusable SUT parameters for short prompts.
