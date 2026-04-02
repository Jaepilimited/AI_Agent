"""Pydantic request/response models for OpenAI-compatible API."""

import base64
import re
import time
import uuid
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# --- Multimodal Content Parts (OpenAI Vision API format) ---

class ImageUrl(BaseModel):
    """Image URL (data URI or HTTP URL)."""
    url: str

class ContentPartText(BaseModel):
    """Text content part."""
    type: Literal["text"] = "text"
    text: str

class ContentPartImage(BaseModel):
    """Image content part (data URI)."""
    type: Literal["image_url"] = "image_url"
    image_url: ImageUrl

ContentPart = Union[ContentPartText, ContentPartImage]


def extract_text(content: Union[str, List[Any]]) -> str:
    """Extract plain text from a message content field.

    Works for both plain string and multimodal list content.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text", ""))
            elif isinstance(part, ContentPartText):
                parts.append(part.text)
        return " ".join(parts).strip()
    return str(content)


def extract_images(content: Union[str, List[Any]]) -> List[Dict[str, Any]]:
    """Extract images from multimodal content.

    Returns list of {"data": bytes, "mime_type": str} dicts.
    """
    if isinstance(content, str) or not isinstance(content, list):
        return []

    images = []
    for part in content:
        url = None
        if isinstance(part, dict) and part.get("type") == "image_url":
            url = part.get("image_url", {}).get("url", "")
        elif isinstance(part, ContentPartImage):
            url = part.image_url.url

        if url and url.startswith("data:"):
            # Parse data URI: data:<mime>;base64,<data>
            match = re.match(r"data:(image/\w+);base64,(.+)", url, re.DOTALL)
            if match:
                mime_type = match.group(1)
                raw_data = base64.b64decode(match.group(2))
                images.append({"data": raw_data, "mime_type": mime_type})
    return images


# --- OpenAI-compatible Request Models ---

class ChatMessage(BaseModel):
    """A single chat message (supports text or multimodal content)."""

    role: Literal["system", "user", "assistant"]
    content: Union[str, List[ContentPart]]


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request."""

    model: str = "skin1004-ai"
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.1
    max_tokens: Optional[int] = 4096
    stream: Optional[bool] = False
    user: Optional[str] = None
    brand_filter: Optional[str] = None  # e.g. "SK,CL,CBT" or "UM"
    enabled_sources: Optional[List[str]] = None  # e.g. ["매출", "CS Q&A"]
    enabled_team_resources: Optional[Dict[str, List[str]]] = None  # {"JBT": ["name1", ...], "BCM": [...] }


# --- OpenAI-compatible Response Models ---

class ChatCompletionChoice(BaseModel):
    """A single completion choice."""

    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class UsageInfo(BaseModel):
    """Token usage information."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "skin1004-ai"
    choices: List[ChatCompletionChoice]
    usage: UsageInfo = Field(default_factory=UsageInfo)


# --- Streaming Response Models ---

class ChatCompletionStreamDelta(BaseModel):
    """Delta content for streaming."""

    role: Optional[str] = None
    content: Optional[str] = None


class ChatCompletionStreamChoice(BaseModel):
    """A single streaming choice."""

    index: int = 0
    delta: ChatCompletionStreamDelta
    finish_reason: Optional[str] = None


class ChatCompletionStreamResponse(BaseModel):
    """OpenAI-compatible streaming response chunk."""

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "skin1004-ai"
    choices: List[ChatCompletionStreamChoice]


# --- Model List Response ---

class ModelInfo(BaseModel):
    """Model information."""

    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "skin1004"


class ModelListResponse(BaseModel):
    """OpenAI-compatible model list response."""

    object: str = "list"
    data: List[ModelInfo]


# --- Internal Models ---

class QueryAnalysis(BaseModel):
    """Result of query intent analysis."""

    route_type: Literal["text_to_sql", "rag", "direct_llm", "multi_agent"]
    reasoning: str
    confidence: float = 1.0


class SQLResult(BaseModel):
    """Result of SQL execution."""

    sql: str
    rows: List[Dict[str, Any]]
    row_count: int
    error: Optional[str] = None


class RAGDocument(BaseModel):
    """A retrieved RAG document."""

    id: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
    source_type: str = ""
