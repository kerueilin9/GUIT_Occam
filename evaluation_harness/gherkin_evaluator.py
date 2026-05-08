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
    ELEMENT_EXISTENCE_SYSTEM_PROMPT,
    GHERKIN_CRITERION_SYSTEM_PROMPT,
    build_element_existence_prompt,
    build_gherkin_criterion_prompt,
)
from evaluation_harness.helper_functions import (
    generate_from_llm_chat_completion,
    llm_fuzzy_match
)
from evaluation_harness.page_snapshot import (
    PageSnapshot,
    get_page_snapshot,
    get_primary_page_text,
)


def evaluate_gherkin_criteria(
    acceptance_criteria: List[str],
    page: Page,
    trajectory: Trajectory,
    client: CDPSession = None,
    comment: bool = True,
) -> float:
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
        return 1.0  # No criteria to check
    
    page_snapshot = get_page_snapshot(page, trajectory)
    
    # Evaluate each criterion
    scores = []
    
    for criterion in acceptance_criteria:
        score = evaluate_single_criterion(
            criterion=criterion,
            page_snapshot=page_snapshot,
            return_comment=comment
        )
        scores.append(score)
    
    # Return average score
    return sum(scores) / len(scores) if scores else 0.0


def evaluate_single_criterion(
    criterion: str,
    page_snapshot: PageSnapshot,
    return_comment: bool = True,
) -> float:
    """
    Evaluate a single Gherkin acceptance criterion
    
    Args:
        criterion: Single "Then" statement (e.g., "I should see Python content")
        page_snapshot: Current page URL, title, accessibility tree, and body text
    
    Returns:
        Score between 0.0 and 1.0
    """
    # Extract the expected outcome from criterion
    # Common patterns:
    # - "I should see X"
    # - "The page should contain X"
    # - "The title should contain X"
    # - "The URL should be X"
    
    criterion_lower = criterion.lower()
    url = page_snapshot.get("url", "")
    title = page_snapshot.get("title", "")
    primary_text = get_primary_page_text(page_snapshot)
    
    # URL checks
    if "url should" in criterion_lower:
        expected_url = extract_quoted_text(criterion) or extract_after_keyword(criterion, ["url should be", "url should contain"])
        if expected_url:
            if "should be" in criterion_lower:
                return 1.0 if expected_url.lower() in url.lower() else 0.0
            elif "should contain" in criterion_lower:
                return 1.0 if expected_url.lower() in url.lower() else 0.0
    
    # Title checks
    if "title should" in criterion_lower:
        expected_title = extract_quoted_text(criterion) or extract_after_keyword(criterion, ["title should contain", "title should be"])
        if expected_title:
            if "should contain" in criterion_lower:
                return 1.0 if expected_title.lower() in title.lower() else 0.5
            elif "should be" in criterion_lower:
                return 1.0 if expected_title.lower() == title.lower() else 0.0
    
    # Content checks
    if "should see" in criterion_lower or "should contain" in criterion_lower:
        expected_content = extract_quoted_text(criterion) or extract_after_keyword(criterion, ["should see", "should contain"])
        if expected_content:
            # Use the same accessibility-first page evidence as LLMJudgeEvaluator.
            return llm_fuzzy_match(
                primary_text[:12000],
                expected_content,  # reference
                f"Check if page contains: {expected_content}"  # question
            )
    
    # Element existence checks
    if "should have" in criterion_lower or "should exist" in criterion_lower:
        element_desc = extract_after_keyword(criterion, ["should have", "should exist"])
        if element_desc:
            return check_element_existence(element_desc, page_snapshot)
    
    # Default: use LLM to evaluate criterion
    return llm_evaluate_criterion_with_comment(
        criterion=criterion,
        page_snapshot=page_snapshot,
    )


def extract_quoted_text(text: str) -> str:
    """Extract text within quotes"""
    matches = re.findall(r'"([^"]*)"', text)
    if matches:
        return matches[0]
    matches = re.findall(r"'([^']*)'", text)
    if matches:
        return matches[0]
    return ""


def extract_after_keyword(text: str, keywords: List[str]) -> str:
    """Extract text after specific keywords"""
    text_lower = text.lower()
    for keyword in keywords:
        if keyword in text_lower:
            idx = text_lower.index(keyword)
            result = text[idx + len(keyword):].strip()
            # Remove quotes if present
            result = result.strip('"').strip("'")
            return result
    return ""


def check_element_existence(
    element_desc: str,
    page_snapshot: PageSnapshot,
) -> float:
    """
    Check if an element exists on the page using LLM
    
    Args:
        element_desc: Description of element (e.g., "a search button", "login form")
        page_snapshot: Current page URL, title, accessibility tree, and body text
    
    Returns:
        Score between 0.0 and 1.0
    """
    prompt = build_element_existence_prompt(element_desc, page_snapshot)

    try:
        response = generate_from_llm_chat_completion(
            messages=[
                {"role": "system", "content": ELEMENT_EXISTENCE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            model="auto",
            temperature=0,
            max_tokens=10
        )
        
        if "yes" in response.lower():
            return 1.0
        elif "no" in response.lower():
            return 0.0
        else:
            print(f"Warning: Unexpected LLM response for element check: {response[:100]}")
            return 0.5
    except Exception as e:
        print(f"Error in element existence check: {e}")
        return 0.5


def llm_evaluate_criterion_with_comment(
    criterion: str,
    page_snapshot: PageSnapshot,
) -> float:
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
            score = float(parsed.get("score", 0.5))
            comment = str(parsed.get("comment", "No explanation provided."))
            print(f"score: {score}, comment: {comment[:200]}")
            # logger for debugging LLM evaluation responses
            logger.debug(f"LLM evaluation response: {response[:200]}\n\n\n\n")
            return max(0.0, min(1.0, score))

        # Fallback parsing if model didn't return strict JSON
        match_score = re.search(r"(\d+\.?\d*)", text)
        score = float(match_score.group(1)) if match_score else 0.5
        if "\n" in text:
            comment = text.split("\n", 1)[1].strip()
        else:
            comment = "Scored based on criterion-page alignment."
            
        print(f"score: {score}, comment: {comment[:200]}")
        return max(0.0, min(1.0, score))

    except Exception as e:
        print(f"Error in LLM evaluation with comment: {e}")
        print(f"Criterion was: {criterion}")
        return 0.5
