"""Shared model/provider registry utilities for AgentOccam."""

from __future__ import annotations

import inspect
from functools import partial

from AgentOccam.llms.adk import (
    arrange_message_for_adk,
    call_adk,
    call_adk_with_messages,
)
from AgentOccam.llms.claude import (
    arrange_message_for_claude,
    call_claude,
    call_claude_with_messages,
)
from AgentOccam.llms.cohere import (
    arrange_message_for_cohere,
    call_cohere,
    call_cohere_with_messages,
)
from AgentOccam.llms.gemini import (
    arrange_message_for_gemini,
    call_gemini,
    call_gemini_with_messages,
)
from AgentOccam.llms.gpt import (
    arrange_message_for_gpt,
    call_gpt,
    call_gpt_with_messages,
)
from AgentOccam.llms.llama import (
    arrange_message_for_llama,
    call_llama,
    call_llama_with_messages,
)
from AgentOccam.llms.mistral import (
    arrange_message_for_mistral,
    call_mistral,
    call_mistral_with_messages,
)
from AgentOccam.llms.titan import (
    arrange_message_for_titan,
    call_titan,
    call_titan_with_messages,
)

MODEL_FAMILIES = ["claude", "mistral", "cohere", "llama", "titan", "gpt", "adk", "gemini"]

CALL_MODEL_MAP = {
    "claude": call_claude,
    "mistral": call_mistral,
    "cohere": call_cohere,
    "llama": call_llama,
    "titan": call_titan,
    "gpt": call_gpt,
    "gemini": call_gemini,
    "adk": call_adk,
}

CALL_MODEL_WITH_MESSAGES_FUNCTION_MAP = {
    "claude": call_claude_with_messages,
    "mistral": call_mistral_with_messages,
    "cohere": call_cohere_with_messages,
    "llama": call_llama_with_messages,
    "titan": call_titan_with_messages,
    "gpt": call_gpt_with_messages,
    "gemini": call_gemini_with_messages,
    "adk": call_adk_with_messages,
}

ARRANGE_MESSAGE_FOR_MODEL_MAP = {
    "claude": arrange_message_for_claude,
    "mistral": arrange_message_for_mistral,
    "cohere": arrange_message_for_cohere,
    "llama": arrange_message_for_llama,
    "titan": arrange_message_for_titan,
    "gpt": arrange_message_for_gpt,
    "gemini": arrange_message_for_gemini,
    "adk": arrange_message_for_adk,
}


def detect_model_family(model_id: str) -> str:
    family = next((model_family for model_family in MODEL_FAMILIES if model_family in model_id), None)
    if family is None:
        raise ValueError(f"Cannot determine model family for '{model_id}'")
    return family


def build_call_model(model_id: str, system_prompt: str = ""):
    """Return a provider callable normalized as call_model(prompt=...)."""
    family = detect_model_family(model_id)
    fn = CALL_MODEL_MAP[family]

    if "system_prompt" in inspect.signature(fn).parameters:
        return partial(fn, model_id=model_id, system_prompt=system_prompt)
    return partial(fn, model_id=model_id)
