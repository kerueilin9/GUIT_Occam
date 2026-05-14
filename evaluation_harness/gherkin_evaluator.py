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
    
    # Evaluate each criterion
    scores = []
    details = []
    
    for criterion in acceptance_criteria:
        result = evaluate_single_criterion(
            criterion=criterion,
            page_snapshot=page_snapshot,
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
    )
    return (score, comment) if return_comment else score


def llm_evaluate_criterion_with_comment(
    criterion: str,
    page_snapshot: PageSnapshot,
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
        # Try JSON parse first
        text = response.strip()
        match_json = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match_json:
            parsed = json.loads(match_json.group(0))
            score = _binary_score(float(parsed.get("score", 0.0)))
            comment = str(parsed.get("comment", "No explanation provided."))
            print(f"score: {score}, comment: {comment[:200]}")
            # logger for debugging LLM evaluation responses
            logger.debug(f"LLM evaluation response: {response[:200]}\n\n\n\n")
            return score, comment

        # Fallback parsing if model didn't return strict JSON
        match_score = re.search(r"(\d+\.?\d*)", text)
        score = _binary_score(float(match_score.group(1))) if match_score else 0.0
        if "\n" in text:
            comment = text.split("\n", 1)[1].strip()
        else:
            comment = "Scored based on criterion-page alignment."
            
        print(f"score: {score}, comment: {comment[:200]}")
        return score, comment

    except Exception as e:
        print(f"Error in LLM evaluation with comment: {e}")
        print(f"Criterion was: {criterion}")
        return 0.0, f"Error in LLM evaluation with comment: {e}"
