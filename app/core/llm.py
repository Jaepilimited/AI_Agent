"""Dual LLM client: Gemini 3 Pro + Claude (Opus 4.6 / Sonnet 4.5).

Provides a unified interface for both models.
Open WebUI model selection determines which LLM is used.

Gemini side:  3 Pro (conversation) / 3 Flash (internal tasks)
Claude side:  Opus 4.6 (complex reasoning) / Sonnet 4.5 (light tasks)
"""

import re
from typing import Any, Dict, List, Optional, Protocol

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


# --- Common Interface ---


class LLMClient(Protocol):
    """Common interface for LLM clients."""

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_output_tokens: int = 8192,
    ) -> str: ...

    def generate_with_history(
        self,
        messages: List[Dict[str, str]],
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_output_tokens: int = 8192,
    ) -> str: ...

    def generate_json(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str: ...


# --- Gemini 2.5 Pro Client ---


class GeminiClient:
    """Wrapper for Gemini 3 Pro API calls via google-genai SDK."""

    def __init__(self) -> None:
        from google import genai
        self.settings = get_settings()
        self.client = genai.Client(api_key=self.settings.gemini_api_key)
        self.model = self.settings.gemini_model
        logger.info("gemini_client_initialized", model=self.model)

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_output_tokens: int = 8192,
    ) -> str:
        """Generate a response from Gemini."""
        from google.genai import types

        logger.info("gemini_generating", prompt_length=len(prompt))

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        if system_instruction:
            config.system_instruction = system_instruction

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            text = response.text or ""
            logger.info("gemini_response_generated", response_length=len(text))
            return text
        except Exception as e:
            logger.error("gemini_generation_failed", error=str(e))
            raise

    def generate_with_history(
        self,
        messages: List[Dict[str, str]],
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_output_tokens: int = 8192,
    ) -> str:
        """Generate a response with conversation history."""
        from google.genai import types

        contents = []
        for msg in messages:
            role = msg["role"]
            if role == "assistant":
                role = "model"
            if role == "system":
                continue
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=msg["content"])],
                )
            )

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        if system_instruction:
            config.system_instruction = system_instruction

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
            return response.text or ""
        except Exception as e:
            logger.error("gemini_history_failed", error=str(e))
            raise

    def generate_json(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str:
        """Generate a JSON response from Gemini (native JSON mode)."""
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=4096,
            response_mime_type="application/json",
        )
        if system_instruction:
            config.system_instruction = system_instruction

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            return response.text or "{}"
        except Exception as e:
            logger.error("gemini_json_failed", error=str(e))
            raise

    def generate_with_search(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: int = 8192,
    ) -> str:
        """Generate a response with Google Search grounding."""
        from google.genai import types

        logger.info("gemini_generating_with_search", prompt_length=len(prompt))

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )
        if system_instruction:
            config.system_instruction = system_instruction

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            text = response.text or ""
            logger.info("gemini_search_response_generated", response_length=len(text))
            return text
        except Exception as e:
            logger.error("gemini_search_failed", error=str(e))
            raise

    def generate_with_history_and_search(
        self,
        messages: List[Dict[str, str]],
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: int = 8192,
    ) -> str:
        """Generate a response with conversation history and Google Search grounding."""
        from google.genai import types

        contents = []
        for msg in messages:
            role = msg["role"]
            if role == "assistant":
                role = "model"
            if role == "system":
                continue
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=msg["content"])],
                )
            )

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )
        if system_instruction:
            config.system_instruction = system_instruction

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
            text = response.text or ""
            logger.info("gemini_history_search_response_generated", response_length=len(text))
            return text
        except Exception as e:
            logger.error("gemini_history_search_failed", error=str(e))
            raise

    def test_connection(self) -> bool:
        """Test Gemini API connection."""
        try:
            response = self.generate("Say 'OK' if you can hear me.", temperature=0.0)
            logger.info("gemini_connection_test_passed", response=response[:50])
            return bool(response)
        except Exception as e:
            logger.error("gemini_connection_test_failed", error=str(e))
            return False


# --- Claude Client (Opus / Sonnet) ---


class ClaudeClient:
    """Wrapper for Claude API calls via Anthropic SDK.

    Supports both Opus (complex reasoning) and Sonnet (lighter tasks).
    The model is set at init time via the `model` attribute.
    """

    def __init__(self, model: Optional[str] = None) -> None:
        from anthropic import Anthropic
        self.settings = get_settings()
        self.client = Anthropic(api_key=self.settings.anthropic_api_key)
        self.model = model or self.settings.anthropic_opus_model
        logger.info("claude_client_initialized", model=self.model)

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_output_tokens: int = 8192,
    ) -> str:
        """Generate a response from Claude."""
        logger.info("claude_generating", model=self.model, prompt_length=len(prompt))

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_instruction:
            kwargs["system"] = system_instruction

        try:
            response = self.client.messages.create(**kwargs)
            text = response.content[0].text if response.content else ""
            logger.info("claude_response_generated", response_length=len(text))
            return text
        except Exception as e:
            logger.error("claude_generation_failed", error=str(e))
            raise

    def generate_with_history(
        self,
        messages: List[Dict[str, str]],
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        max_output_tokens: int = 8192,
    ) -> str:
        """Generate a response with conversation history."""
        api_messages = []
        for msg in messages:
            role = msg["role"]
            if role == "model":
                role = "assistant"
            if role == "system":
                continue
            api_messages.append({"role": role, "content": msg["content"]})

        # Ensure starts with user
        if not api_messages or api_messages[0]["role"] != "user":
            api_messages.insert(0, {"role": "user", "content": "안녕하세요."})

        # Merge consecutive same-role messages
        merged = []
        for msg in api_messages:
            if merged and merged[-1]["role"] == msg["role"]:
                merged[-1]["content"] += "\n" + msg["content"]
            else:
                merged.append(dict(msg))
        api_messages = merged

        # Ensure ends with user
        if api_messages and api_messages[-1]["role"] != "user":
            api_messages.append({"role": "user", "content": "계속해주세요."})

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "messages": api_messages,
        }
        if system_instruction:
            kwargs["system"] = system_instruction

        try:
            response = self.client.messages.create(**kwargs)
            return response.content[0].text if response.content else ""
        except Exception as e:
            logger.error("claude_history_failed", error=str(e))
            raise

    def generate_json(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str:
        """Generate a JSON response from Claude."""
        json_system = (system_instruction or "") + "\n\nIMPORTANT: 반드시 유효한 JSON만 출력하세요. 다른 텍스트는 포함하지 마세요."

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": temperature,
            "system": json_system.strip(),
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            response = self.client.messages.create(**kwargs)
            text = response.content[0].text if response.content else "{}"
            if "```" in text:
                match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
                if match:
                    text = match.group(1).strip()
            return text
        except Exception as e:
            logger.error("claude_json_failed", error=str(e))
            raise

    def test_connection(self) -> bool:
        """Test Claude API connection."""
        try:
            response = self.generate("Say 'OK' if you can hear me.", temperature=0.0)
            logger.info("claude_connection_test_passed", response=response[:50])
            return bool(response)
        except Exception as e:
            logger.error("claude_connection_test_failed", error=str(e))
            return False


# --- Factory & Singletons ---

_gemini_client: Optional[GeminiClient] = None
_gemini_flash_client: Optional[GeminiClient] = None
_claude_opus_client: Optional[ClaudeClient] = None
_claude_sonnet_client: Optional[ClaudeClient] = None

# Model type constants
MODEL_GEMINI = "gemini"
MODEL_CLAUDE = "claude"


def get_llm_client(model_type: str = MODEL_GEMINI) -> Any:
    """Get or create the appropriate LLM client.

    Args:
        model_type: "gemini" for Gemini 3 Pro, "claude" for Claude Opus.

    Returns:
        LLM client instance (GeminiClient or ClaudeClient).
    """
    global _gemini_client, _claude_opus_client

    if model_type == MODEL_CLAUDE:
        if _claude_opus_client is None:
            settings = get_settings()
            _claude_opus_client = ClaudeClient(model=settings.anthropic_opus_model)
        return _claude_opus_client
    else:
        if _gemini_client is None:
            _gemini_client = GeminiClient()
        return _gemini_client


def get_flash_client() -> GeminiClient:
    """Get Gemini Flash client for fast, lightweight tasks (routing, grading).

    Uses gemini-2.5-flash which is much faster than Pro for simple tasks.
    """
    global _gemini_flash_client
    if _gemini_flash_client is None:
        _gemini_flash_client = GeminiClient()
        settings = get_settings()
        _gemini_flash_client.model = settings.gemini_flash_model
        logger.info("gemini_flash_client_initialized", model=_gemini_flash_client.model)
    return _gemini_flash_client


def get_claude_sonnet_client() -> ClaudeClient:
    """Get Claude Sonnet client for lighter Claude tasks.

    Uses Sonnet for tasks that need Claude but don't require Opus-level reasoning.
    """
    global _claude_sonnet_client
    if _claude_sonnet_client is None:
        settings = get_settings()
        _claude_sonnet_client = ClaudeClient(model=settings.anthropic_sonnet_model)
        logger.info("claude_sonnet_client_initialized", model=_claude_sonnet_client.model)
    return _claude_sonnet_client


def resolve_model_type(model_id: str) -> str:
    """Resolve Open WebUI model ID to internal model type.

    Args:
        model_id: Model ID from request (e.g., "skin1004-Search", "skin1004-Analysis").

    Returns:
        MODEL_GEMINI or MODEL_CLAUDE.
    """
    model_id_lower = model_id.lower()
    if "analysis" in model_id_lower or "claude" in model_id_lower or "sonnet" in model_id_lower or "opus" in model_id_lower:
        return MODEL_CLAUDE
    return MODEL_GEMINI


# Backward compatibility
def get_gemini_client() -> GeminiClient:
    """Get Gemini client (backward compatibility)."""
    return get_llm_client(MODEL_GEMINI)
