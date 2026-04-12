"""
Google ADK (Agent Development Kit) LLM Provider for AgentOccam

This module provides integration with Google's ADK framework,
allowing AgentOccam to leverage ADK's LlmAgent capabilities.

Phase 1 Implementation: Basic LLM Provider (No Tools/Orchestration)
- Uses ADK's LlmAgent as a simple text generation backend
- Compatible with AgentOccam's existing actor-critic architecture
- Does not utilize ADK's tool system or multi-agent features yet

Model Naming Convention:
- Use "adk-" prefix in YAML config (e.g., "adk-gemini-2.0-flash")
- The prefix ensures correct provider detection in AgentOccam
- Actual model ID (without prefix) is passed to ADK API
"""

import os
import time
import asyncio
from typing import Optional

from AgentOccam.llms.retry_utils import compute_retry_delay_seconds

try:
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    ADK_AVAILABLE = True
except ImportError:
    ADK_AVAILABLE = False
    print("Warning: google-adk not installed. ADK provider will not work.")
    print("Install with: pip install google-adk")

# Environment setup
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", os.environ.get("GEMINI_API_KEY", ""))


def _is_vertex_mode() -> bool:
    """Return True when ADK should use Vertex AI instead of API-key mode."""
    vertex_flag = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower()
    return vertex_flag in {"1", "true", "yes", "on"}


def _validate_adk_runtime_env(model_id: str) -> None:
    """Validate minimal env requirements for ADK runtime mode."""
    if _is_vertex_mode():
        missing_variables = []
        if not os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip():
            missing_variables.append("GOOGLE_CLOUD_PROJECT")
        if not os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip():
            missing_variables.append("GOOGLE_CLOUD_LOCATION")

        credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if credentials_path and not os.path.exists(credentials_path):
            raise EnvironmentError(
                "GOOGLE_APPLICATION_CREDENTIALS is set but file does not exist: "
                f"{credentials_path}"
            )

        if missing_variables:
            raise EnvironmentError(
                "Vertex AI mode is enabled (GOOGLE_GENAI_USE_VERTEXAI=true), "
                "but required variables are missing: "
                f"{', '.join(missing_variables)}"
            )
    else:
        if not GOOGLE_API_KEY:
            raise EnvironmentError(
                "ADK API-key mode requires GOOGLE_API_KEY or GEMINI_API_KEY. "
                "Alternatively, enable Vertex AI mode with "
                "GOOGLE_GENAI_USE_VERTEXAI=true and set GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION."
            )

# Session management constants
APP_NAME = "AgentOccam"
DEFAULT_USER_ID = "agent_occam_user"


async def call_adk_async(prompt: str, model_id: str = "adk-gemini-2.0-flash", system_prompt: Optional[str] = None) -> str:
    """
    Async: Call ADK LlmAgent with a simple text prompt.
    
    This function creates a minimal ADK agent for single-turn text generation,
    using ADK's async API to avoid deprecation warnings.
    
    Args:
        prompt: User prompt/query text
        model_id: Model identifier with "adk-" prefix (e.g., "adk-gemini-2.0-flash")
                 The prefix will be stripped before passing to ADK API
        system_prompt: System instruction to guide agent behavior
        
    Returns:
        Generated text response from the ADK agent
        
    Raises:
        ValueError: After 10 failed attempts
        ImportError: If google-adk is not installed
    """
    if not ADK_AVAILABLE:
        raise ImportError(
            "google-adk package is not installed. "
            "Please install it with: pip install google-adk"
        )

    _validate_adk_runtime_env(model_id)
    
    # Strip "adk-" prefix if present (AgentOccam naming convention)
    actual_model_id = model_id.replace("adk-", "") if model_id.startswith("adk-") else model_id
    
    # Build instruction from system prompt
    instruction = system_prompt if system_prompt else "You are a helpful assistant."
    
    num_attempts = 0
    while True:
        if num_attempts >= 10:
            raise ValueError("ADK request failed after 10 attempts.")
        
        try:
            # Create a minimal ADK agent
            agent = LlmAgent(
                model=actual_model_id,
                name="agentoccam_actor",
                instruction=instruction,
                description="AgentOccam LLM provider using Google ADK"
            )
            
            # Create session service and session (using async method)
            session_service = InMemorySessionService()
            session_id = f"session_{int(time.time()*1000)}"
            session = await session_service.create_session(
                app_name=APP_NAME,
                user_id=DEFAULT_USER_ID,
                session_id=session_id
            )
            
            # Create runner and execute
            runner = Runner(
                agent=agent,
                app_name=APP_NAME,
                session_service=session_service
            )
            
            # Prepare user message
            content = types.Content(
                role="user",
                parts=[types.Part(text=prompt)]
            )
            
            # Run agent and collect response
            response_text = ""
            events = runner.run(
                user_id=DEFAULT_USER_ID,
                session_id=session_id,
                new_message=content
            )
            
            for event in events:
                if event.is_final_response() and event.content:
                    if event.content.parts and event.content.parts[0].text:
                        response_text = event.content.parts[0].text.strip()
                        break
            
            if not response_text:
                raise ValueError("No response generated from ADK agent")
                
            return response_text
            
        except Exception as e:
            print(f"ERROR: Can't invoke ADK agent with model '{actual_model_id}' (from '{model_id}'). Reason: {e}")
            num_attempts += 1
            if num_attempts >= 10:
                raise ValueError("ADK request failed after 10 attempts.") from e
            wait_seconds = compute_retry_delay_seconds(
                e,
                num_attempts - 1,
                default_seconds=10.0,
                quota_seconds=30.0,
            )
            print(f"Sleeping for {wait_seconds:.1f}s before retrying ADK...")
            await asyncio.sleep(wait_seconds)


def call_adk(prompt: str, model_id: str = "adk-gemini-2.0-flash", system_prompt: Optional[str] = None) -> str:
    """
    Synchronous wrapper for call_adk_async.
    
    This function provides a synchronous interface for compatibility with AgentOccam,
    which expects synchronous LLM provider functions.
    
    Args:
        prompt: User prompt/query text
        model_id: Model identifier with "adk-" prefix
        system_prompt: System instruction to guide agent behavior
        
    Returns:
        Generated text response from the ADK agent
    """
    try:
        # Try to get existing event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is already running, create a new one in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, call_adk_async(prompt, model_id, system_prompt))
                return future.result()
        else:
            # If no loop is running, use asyncio.run
            return loop.run_until_complete(call_adk_async(prompt, model_id, system_prompt))
    except RuntimeError:
        # If there's no event loop, create a new one
        return asyncio.run(call_adk_async(prompt, model_id, system_prompt))


def arrange_message_for_adk(item_list):
    """
    Convert AgentOccam's message format to ADK-compatible format.
    
    AgentOccam uses list of (type, content) tuples. For Phase 1,
    we only support text messages and concatenate them.
    
    Args:
        item_list: List of (type, content) tuples where type is "text" or "image"
        
    Returns:
        Concatenated text prompt
        
    Raises:
        NotImplementedError: If image messages are encountered (not supported in Phase 1)
    """
    for item in item_list:
        if item[0] == "image":
            raise NotImplementedError(
                "Image support not implemented in Phase 1 ADK provider. "
                "Images will be supported in future phases."
            )
    
    # Concatenate all text messages
    prompt = "".join([item[1] for item in item_list if item[0] == "text"])
    return prompt


def call_adk_with_messages(messages: str, model_id: str = "adk-gemini-2.0-flash", system_prompt: Optional[str] = None) -> str:
    """
    Call ADK with pre-formatted messages (wrapper for call_adk).
    
    This function exists for compatibility with AgentOccam's provider interface,
    which expects both call_<model>() and call_<model>_with_messages() functions.
    
    Args:
        messages: Pre-formatted prompt string
        model_id: Model identifier with "adk-" prefix
        system_prompt: System instruction
        
    Returns:
        Generated text response
    """
    return call_adk(prompt=messages, model_id=model_id, system_prompt=system_prompt)


# Testing code
if __name__ == "__main__":
    try:
        _validate_adk_runtime_env("adk-gemini-2.0-flash")
        print("Testing ADK Provider...")
        print("=" * 50)

        test_prompt = """You are a helpful web navigation agent.

Current webpage shows:
- Link [123] "Home"
- Link [456] "Products"
- Link [789] "Contact"

Task: Navigate to the Products page.

What action should you take?"""

        response = call_adk(
            prompt=test_prompt,
            model_id="adk-gemini-2.0-flash",
            system_prompt="You are a web automation assistant. Provide concise, direct answers."
        )
        print("\nADK Response:")
        print(response)
        print("=" * 50)
        print("✓ ADK provider test successful!")
    except Exception as e:
        print(f"\n✗ ADK provider test failed: {e}")
