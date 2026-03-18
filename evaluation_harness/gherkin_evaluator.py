"""
Gherkin-based evaluator for AgentOccam
Evaluates agent performance against Gherkin acceptance criteria
"""
from typing import List, Dict, Any
from playwright.sync_api import Page, CDPSession
from AgentOccam.logger import logger
from browser_env import Trajectory
from evaluation_harness.helper_functions import (
    generate_from_llm_chat_completion,
    llm_fuzzy_match
)
import json


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
    
    # Get current page state
    current_url = page.url
    page_title = page.title()
    
    # Get page content (visible text)
    try:
        page_content = page.inner_text("body")
    except:
        page_content = ""
    
    # Evaluate each criterion
    scores = []
    
    for criterion in acceptance_criteria:
        score = evaluate_single_criterion(
            criterion=criterion,
            url=current_url,
            title=page_title,
            content=page_content,
            page=page,
            return_comment=comment
        )
        scores.append(score)
    
    # Return average score
    return sum(scores) / len(scores) if scores else 0.0


def evaluate_single_criterion(
    criterion: str,
    url: str,
    title: str,
    content: str,
    page: Page,
    return_comment: bool = True,
) -> float | tuple[float, str]:
    """
    Evaluate a single Gherkin acceptance criterion
    
    Args:
        criterion: Single "Then" statement (e.g., "I should see Python content")
        url: Current page URL
        title: Current page title
        content: Current page visible text content
        page: Playwright page object
    
    Returns:
        - if return_comment=False: score between 0.0 and 1.0
        - if return_comment=True: (score, short comment)
    """
    # Extract the expected outcome from criterion
    # Common patterns:
    # - "I should see X"
    # - "The page should contain X"
    # - "The title should contain X"
    # - "The URL should be X"
    
    criterion_lower = criterion.lower()
    
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
            # Use fuzzy matching for content
            return llm_fuzzy_match(
                content[:2000],  # pred - Limit content length
                expected_content,  # reference
                f"Check if page contains: {expected_content}"  # question
            )
    
    # Element existence checks
    if "should have" in criterion_lower or "should exist" in criterion_lower:
        element_desc = extract_after_keyword(criterion, ["should have", "should exist"])
        if element_desc:
            # Use LLM to check if element exists
            return check_element_existence(element_desc, content, page)
    
    # Default: use LLM to evaluate criterion
    if return_comment:
        return llm_evaluate_criterion_with_comment(criterion, url, title, content)
    return llm_evaluate_criterion(criterion, url, title, content)


def extract_quoted_text(text: str) -> str:
    """Extract text within quotes"""
    import re
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


def check_element_existence(element_desc: str, content: str, page: Page) -> float:
    """
    Check if an element exists on the page using LLM
    
    Args:
        element_desc: Description of element (e.g., "a search button", "login form")
        content: Page content
        page: Playwright page
    
    Returns:
        Score between 0.0 and 1.0
    """
    prompt = f"""Given the following page content, does it contain {element_desc}?

Page content:
{content[:1000]}

Answer with just "YES" or "NO"."""

    try:
        response = generate_from_llm_chat_completion(
            messages=[
                {"role": "system", "content": "You are a helpful assistant that analyzes web page content."},
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


def llm_evaluate_criterion(criterion: str, url: str, title: str, content: str) -> float:
    """
    Use LLM to evaluate if criterion is met
    
    Args:
        criterion: Gherkin acceptance criterion
        url: Current URL
        title: Page title
        content: Page content
    
    Returns:
        Score between 0.0 and 1.0
    """
    prompt = f"""Evaluate if the following acceptance criterion is met based on the web page information.

Acceptance Criterion: {criterion}

Current Web Page:
- URL: {url}
- Title: {title}
- Content (first 1000 chars): {content[:1000]}

Does the web page satisfy this criterion? Rate from 0.0 to 1.0 where:
- 1.0 = Fully satisfied
- 0.5 = Partially satisfied
- 0.0 = Not satisfied

Respond with ONLY a number between 0.0 and 1.0."""

    try:
        response = generate_from_llm_chat_completion(
            messages=[
                {"role": "system", "content": "You are an expert at evaluating web automation test results against acceptance criteria."},
                {"role": "user", "content": prompt}
            ],
            model="auto",
            temperature=0,
            max_tokens=10
        )
        
        # Extract numeric score
        import re
        match = re.search(r'(\d+\.?\d*)', response)
        if match:
            score = float(match.group(1))
            return max(0.0, min(1.0, score))  # Clamp to [0, 1]
        else:
            print(f"Warning: Could not extract score from LLM response: {response[:100]}")
            return 0.5
    except Exception as e:
        print(f"Error in LLM evaluation: {e}")
        print(f"Criterion was: {criterion}")
        return 0.5

def llm_evaluate_criterion_with_comment(
    criterion: str,
    url: str,
    title: str,
    content: str,
) -> float:
    """
    Use LLM to evaluate if criterion is met and return a short rationale.

    Returns:
        (score, comment)
        - score: 0.0 ~ 1.0
        - comment: one short sentence explaining the score
    """
    prompt = f"""Evaluate the acceptance criterion and return JSON only.

Acceptance Criterion: {criterion}

Current Web Page:
- URL: {url}
- Title: {title}
- Content (first 1000 chars): {content[:1000]}

Return strict JSON with keys:
{{"score": <0.0-1.0>, "comment": "<short reason in one sentence>"}}"""

    try:
        response = generate_from_llm_chat_completion(
            messages=[
                {"role": "system", "content": "You are an expert at evaluating web automation test results against acceptance criteria."},
                {"role": "user", "content": prompt}
            ],
            model="auto",
            temperature=0,
            max_tokens=1000,
        )

        import re
        import json
        
        # Try JSON parse first
        text = response.strip()
        match_json = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match_json:
            parsed = json.loads(match_json.group(0))
            score = float(parsed.get("score", 0.5))
            comment = str(parsed.get("comment", "No explanation provided."))
            print(f"score: {score}, comment: {comment[:200]}")
            # logger for debugging LLM evaluation responses
            logger.debug(f"LLM evaluation response: {response[:200]}/n/n/n/n")
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
        return 0.5, "Evaluation fallback used due to LLM response/parsing error."
