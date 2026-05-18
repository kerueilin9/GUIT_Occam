"""
Gherkin-based evaluator for AgentOccam
Evaluates agent performance against Gherkin acceptance criteria
"""
import json
import re
from typing import List

from playwright.sync_api import Page, CDPSession

from AgentOccam.logger import logger
from browser_env import Trajectory
from evaluation_harness.evaluator_prompts import (
    GHERKIN_CRITERION_SYSTEM_PROMPT,
    build_gherkin_criterion_prompt,
)
from evaluation_harness.helper_functions import generate_from_llm_chat_completion
from evaluation_harness.page_snapshot import (
    PageSnapshot,
    capture_page_screenshot,
    get_page_snapshot,
)


def _binary_score(score: float) -> float:
    """Convert any parsed score into the evaluator's binary 0/1 scale."""
    return 1.0 if score >= 0.5 else 0.0


def evaluate_gherkin_criteria(
    acceptance_criteria: List[str],
    page: Page,
    trajectory: Trajectory,
    client: CDPSession = None,
    comment: bool = True,
    return_details: bool = False,
) -> float | tuple[float, list[dict]]:
    """
    Evaluate if the agent met the Gherkin acceptance criteria
    
    Args:
        acceptance_criteria: List of "Then" statements from Gherkin scenario
        page: Playwright page object
        trajectory: Agent's interaction trajectory
        client: CDP session (optional)
        comment: Whether to include comments in the evaluation
    Returns:
        Score between 0.0 and 1.0
    """
    if not acceptance_criteria:
        return (1.0, []) if return_details else 1.0  # No criteria to check
    
    page_snapshot = get_page_snapshot(page, trajectory)
    screenshot_cache: dict[str, bytes | bool | None] = {
        "attempted": False,
        "bytes": None,
    }
    
    # Evaluate each criterion
    scores = []
    details = []
    
    for criterion in acceptance_criteria:
        result = evaluate_single_criterion(
            criterion=criterion,
            page_snapshot=page_snapshot,
            page=page,
            screenshot_cache=screenshot_cache,
            return_comment=comment
        )
        if comment:
            score, criterion_comment = result
            details.append({
                "criterion": criterion,
                "score": score,
                "comment": criterion_comment,
            })
        else:
            score = result
        scores.append(score)
    
    # Gherkin criteria are binary: every Then expectation must pass.
    final_score = 1.0 if scores and all(score == 1.0 for score in scores) else 0.0
    return (final_score, details) if return_details else final_score


def evaluate_single_criterion(
    criterion: str,
    page_snapshot: PageSnapshot,
    page: Page | None = None,
    screenshot_cache: dict[str, bytes | bool | None] | None = None,
    return_comment: bool = True,
) -> float | tuple[float, str]:
    """
    Evaluate a single Gherkin acceptance criterion
    
    Args:
        criterion: Single "Then" statement from a Gherkin scenario
        page_snapshot: Current page URL, title, accessibility tree, and body text
    
    Returns:
        Score between 0.0 and 1.0
    """
    score, comment = llm_evaluate_criterion_with_comment(
        criterion=criterion,
        page_snapshot=page_snapshot,
        page=page,
        screenshot_cache=screenshot_cache,
    )
    return (score, comment) if return_comment else score


def llm_evaluate_criterion_with_comment(
    criterion: str,
                                       page_snapshot: PageSnapshot,
    page: Page | None = None,
    screenshot_cache: dict[str, bytes | bool | None] | None = None,
) -> tuple[float, str]:
    """
    Use LLM to evaluate if criterion is met and return a short rationale.

    Returns:
        Score between 0.0 and 1.0
    """
    prompt = build_gherkin_criterion_prompt(criterion, page_snapshot)
    logger.debug(f"LLM evaluation prompt: {prompt}\n\n\n\n")
    try:
        response = generate_from_llm_chat_completion(
            messages=[
                {"role": "system", "content": GHERKIN_CRITERION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            model="auto",
            temperature=0,
            max_tokens=1000,
        )
        score, comment, needs_screenshot = _parse_criterion_response(response)
        if needs_screenshot and page is not None:
            screenshot_bytes = _get_cached_screenshot(page, screenshot_cache)
            if screenshot_bytes:
                try:
                    visual_prompt = build_gherkin_criterion_prompt(
                        criterion,
                        page_snapshot,
                        screenshot_attached=True,
                    )
                    visual_response = generate_from_llm_chat_completion(
                        messages=[
                            {"role": "system", "content": GHERKIN_CRITERION_SYSTEM_PROMPT},
                            {"role": "user", "content": visual_prompt}
                        ],
                        model="auto",
                        temperature=0,
                        max_tokens=1000,
                        image_bytes=screenshot_bytes,
                    )
                    score, comment, _ = _parse_criterion_response(visual_response)
                    comment = f"{comment} (Used screenshot because text evidence was insufficient.)"
                except Exception as exc:
                    comment = f"{comment} Screenshot-assisted evaluation failed: {exc}"
            else:
                comment = f"{comment} Screenshot was requested but could not be captured."

        print(f"score: {score}, comment: {comment[:200]}")
        logger.debug(f"LLM evaluation response: {response}\n\n\n\n")
        return score, comment

    except Exception as e:
        print(f"Error in LLM evaluation with comment: {e}")
        print(f"Criterion was: {criterion}")
        return 0.0, f"Error in LLM evaluation with comment: {e}"


def _parse_criterion_response(response: str) -> tuple[float, str, bool]:
    text = response.strip()
    match_json = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match_json:
        try:
            parsed = json.loads(match_json.group(0))
            score = _binary_score(float(parsed.get("score", 0.0)))
            comment = str(parsed.get("comment", "No explanation provided."))
            needs_screenshot = _coerce_bool(parsed.get("needs_screenshot", False))
            return score, comment, needs_screenshot
        except (json.JSONDecodeError, ValueError):
            pass

    match_score = re.search(r"(\d+\.?\d*)", text)
    score = _binary_score(float(match_score.group(1))) if match_score else 0.0
    if "\n" in text:
        comment = text.split("\n", 1)[1].strip()
    else:
        comment = "Scored based on criterion-page alignment."
    return score, comment, _mentions_screenshot_need(text)


def _coerce_bool(value) -> bool:
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


def _get_cached_screenshot(
    page: Page,
    screenshot_cache: dict[str, bytes | bool | None] | None,
) -> bytes | None:
    if screenshot_cache is None:
        return _try_capture_screenshot(page)

    if not screenshot_cache.get("attempted"):
        screenshot_cache["attempted"] = True
        screenshot_cache["bytes"] = _try_capture_screenshot(page)

    screenshot = screenshot_cache.get("bytes")
    return screenshot if isinstance(screenshot, bytes) else None


def _try_capture_screenshot(page: Page) -> bytes | None:
    try:
        return capture_page_screenshot(page)
    except Exception as exc:
        print(f"Warning: Failed to capture final screenshot for Gherkin evaluation: {exc}")
        return None
