"""Implements helper functions to assist evaluation cases where other evaluators are not suitable."""
import json
import os
from typing import Any
from urllib.parse import urlparse
from AgentOccam.logger import logger

import requests
from playwright.sync_api import CDPSession, Page

from browser_env.env_config import (
    ACCOUNTS,
    GITLAB,
    MAP,
    REDDIT,
    SHOPPING,
    SHOPPING_ADMIN,
    WIKIPEDIA,
)

# Import both OpenAI and Gemini utilities
try:
    from llms.providers.openai_utils import generate_from_openai_chat_completion
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import google.generativeai as genai
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    else:
        GEMINI_AVAILABLE = False
except ImportError:
    GEMINI_AVAILABLE = False


def generate_from_llm_chat_completion(
    messages: list[dict[str, Any]],
    model: str = "auto",
    temperature: float = 0,
    max_tokens: int = 768,
) -> str:
    # 1. Decide Provider and Model
    if model == "auto":
        if os.getenv("GEMINI_API_KEY") and GEMINI_AVAILABLE:
            model, use_gemini = "gemini-2.5-flash", True
        elif os.getenv("OPENAI_API_KEY") and OPENAI_AVAILABLE:
            model, use_gemini = "gpt-4-turbo", False
        else:
            raise ValueError("Missing API Keys for both Gemini and OpenAI.")
    else:
        use_gemini = "gemini" in model.lower()

    # --- Gemini path ---
    if use_gemini:
        if not GEMINI_AVAILABLE:
            raise ValueError("Gemini not available. Please install google-generativeai and set GEMINI_API_KEY.")

        # Split system prompt and chat history (multi-turn)
        sys_msg = next((m["content"] for m in messages if m.get("role") == "system"), None)
        history = [
            {
                "role": "user" if m.get("role") == "user" else "model",
                "parts": [m.get("content", "")],
            }
            for m in messages
            if m.get("role") != "system"
        ]

        # Last message as current user prompt, previous turns as history
        user_input = history.pop()["parts"][0] if history else ""
        logger.debug(f"Gemini Chat - System: {sys_msg}, User Input: {user_input}, History Length: {len(history)}")  
        genai_model = genai.GenerativeModel(
            model_name=model,
            system_instruction=sys_msg,
            safety_settings={
                cat: "BLOCK_NONE"
                for cat in [
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                ]
            },
        )

        chat = genai_model.start_chat(history=history)
        response = chat.send_message(
            user_input,
            generation_config=genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        logger.debug(f"Gemini Raw Response: {response}")
        try:
            return response.text
        except Exception:
            if hasattr(response, "candidates") and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, "finish_reason"):
                    print(
                        f"Warning: Gemini response finished with reason={candidate.finish_reason}"
                    )
                if hasattr(candidate, "content") and candidate.content:
                    parts = candidate.content.parts or []
                    partial = "".join(
                        p.text for p in parts if hasattr(p, "text") and p.text
                    )
                    if partial:
                        return partial
            raise

    # --- OpenAI path ---
    if not OPENAI_AVAILABLE:
        raise ValueError("OpenAI not available. Please set OPENAI_API_KEY.")

    return generate_from_openai_chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=1.0,
        context_length=0,
    )


def shopping_get_auth_token() -> str:
    response = requests.post(
        url=f"{SHOPPING}/rest/default/V1/integration/admin/token",
        headers={"content-type": "application/json"},
        data=json.dumps(
            {
                "username": ACCOUNTS["shopping_site_admin"]["username"],
                "password": ACCOUNTS["shopping_site_admin"]["password"],
            }
        ),
    )
    token: str = response.json()
    return token


def shopping_get_latest_order_url() -> str:
    """Get the latest order url from the shopping website."""

    header = {
        "Authorization": f"Bearer {shopping_get_auth_token()}",
        "Content-Type": "application/json",
    }

    params = {
        "searchCriteria[sortOrders][0][field]": "created_at",
        "searchCriteria[sortOrders][0][direction]": "DESC",
        "searchCriteria[pageSize]": "1",
    }

    response = requests.get(
        f"{SHOPPING}/rest/V1/orders", params=params, headers=header
    )
    assert response.status_code == 200
    response_obj = response.json()["items"][0]
    order_id = int(response_obj["increment_id"])
    order_url = f"{SHOPPING}/sales/order/view/order_id/{order_id}/"
    return order_url


def shopping_get_sku_latest_review_author(sku: str) -> str:
    """Get the latest review for shopping admin."""
    header = {
        "Authorization": f"Bearer {shopping_get_auth_token()}",
        "Content-Type": "application/json",
    }
    response = requests.get(
        f"{SHOPPING}/rest/V1/products/{sku}/reviews", headers=header
    )
    assert response.status_code == 200
    response_obj = response.json()
    if len(response_obj) == 0:
        return ""
    author: str = response_obj[-1]["nickname"]
    return author


def shopping_get_sku_latest_review_rating(sku: str) -> str:
    """Get the latest review for shopping admin."""
    header = {
        "Authorization": f"Bearer {shopping_get_auth_token()}",
        "Content-Type": "application/json",
    }
    response = requests.get(
        f"{SHOPPING}/rest/V1/products/{sku}/reviews", headers=header
    )
    assert response.status_code == 200
    response_obj = response.json()
    if len(response_obj) == 0:
        return ""
    assert response_obj[0]["ratings"][0]["rating_name"] == "Rating"
    rating: str = str(response_obj[-1]["ratings"][0]["percent"])
    return rating


def reddit_get_post_url(url: str) -> str:
    """Get the post url"""
    # Url is http://domain/f/subreddit/post_id/...
    # get domain, subreddit, post_id
    domain = urlparse(url).netloc
    tok_url = urlparse(url).path.split("/")
    # not a valid post/comment url, return the url as is
    if len(tok_url) < 4:
        return url
    if tok_url[1] != "f":
        return url
    subreddit = urlparse(url).path.split("/")[2]
    post_id = urlparse(url).path.split("/")[3]
    scheme = urlparse(url).scheme
    post_url = f"{scheme}://{domain}/f/{subreddit}/{post_id}/"
    return post_url


def gitlab_get_project_memeber_role(page: Page, account_name: str) -> str:
    # get the account index
    try:
        account_idx = page.evaluate(
            f"""(() => {{
                const elements = document.querySelectorAll("td[data-label='Account'] span.gl-avatar-labeled-sublabel");
                let index = -1;  // Default value if not found

                for(let i = 0; i < elements.length; i++) {{
                    if(elements[i].outerText === '@{account_name}') {{
                        index = i;
                        break;
                    }}
                }}

                return index;
            }})()"""
        )

        # get the role
        role: str = page.evaluate(
            f"""(() => {{
                return document.querySelectorAll("td.col-max-role span")[{account_idx}].outerText;
            }})()"""
        )
    except Exception:
        role = ""

    return role


def llm_fuzzy_match(pred: str, reference: str, question: str) -> float:
    """Check whether the prediction matches the reference with LLM (Gemini or GPT-4)"""
    messages: list[dict[str, Any]] = []
    # construct the question to ask
    message = "Help a teacher to grade the answer of a student given a question. Keep in mind that the student has performed the action to get the answer. They are allowed to use different phrasing or wording to answer the question. The goal is to evaluate whether the key points in the reference answer are included in the student's answer. We allow answers with additional information that doesn't contradict the reference answer and review them as fully (not partially) correct.\n"
    message += f"question: {question}\n"
    message += f"reference answer: {reference}\n"
    message += "all the string 'N/A' that you see is a special sequence that means 'not achievable'\n"
    message += f"student answer: {pred}\n"
    message += "Conclude the judgement by correct/incorrect/partially correct and explain why."
    messages = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": message},
    ]
    
    try:
        response = generate_from_llm_chat_completion(
            messages=messages,
            model="auto",  # Auto-detect Gemini or OpenAI
            temperature=0,
            max_tokens=768,
        ).lower()
        print(response)
        if "partially correct" in response or "incorrect" in response:
            return 0.0
        else:
            assert "correct" in response
            return 1.0
    except Exception as e:
        print(f"Warning: LLM evaluation failed: {e}")
        print("Falling back to exact match...")
        # Fallback to simple string matching
        return 1.0 if pred.lower().strip() in reference.lower().strip() or reference.lower().strip() in pred.lower().strip() else 0.0

def llm_ua_match(pred: str, reference: str, question: str) -> float:
    """Check whether the prediction matches the reference with LLM (Gemini or GPT-4)"""
    messages: list[dict[str, Any]] = []
    # construct the question to ask
    message = ""
    message += f"task: {question}\n"
    message += f"actual unachievable reason: {reference}\n"
    message += f"reported unachievable reason: {pred}\n"
    message += (
        "The task described above is inherently unachievable due to the reason specified under 'actual unachievable reason'. "
        "An individual previously attempted this task and was unable to complete it. They provided a reason for their failure, "
        "which is listed under 'reported unachievable reason'. Your role is to review both the actual and reported reasons. "
        "Determine if the reported reason aligns with the actual reason, even if implicitly. "
        "If the stated reason is in line with the actual reason, respond with 'same'. Otherwise, respond with 'different'."
    )
    messages = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": message},
    ]

    try:
        response = generate_from_llm_chat_completion(
            messages=messages,
            model="auto",  # Auto-detect Gemini or OpenAI
            temperature=0,
            max_tokens=768,
        ).lower()
        if "different" in response:
            return 0.0
        else:
            assert "same" in response
            return 1.0
    except Exception as e:
        print(f"Warning: LLM evaluation failed: {e}")
        print("Falling back to simple matching...")
        # Fallback to simple matching
        return 1.0 if pred.lower().strip() in reference.lower().strip() or reference.lower().strip() in pred.lower().strip() else 0.0


class PseudoPage:
    def __init__(self, original_page: Page, url: str):
        self.url = url
        self.original_page = original_page

    def __getattr__(self, attr: str) -> Any:
        # Delegate attribute access to the original page object
        if attr not in ["url"]:
            return getattr(self.original_page, attr)
        else:
            return getattr(self, attr)
