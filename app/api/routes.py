"""OpenAI-compatible API endpoints for Open WebUI integration."""

import asyncio
import time
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agents.orchestrator import OrchestratorAgent
from app.config import get_settings
from app.core.llm import resolve_model_type
from app.models.schemas import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamChoice,
    ChatCompletionStreamDelta,
    ChatCompletionStreamResponse,
    ChatMessage,
    ModelInfo,
    ModelListResponse,
    UsageInfo,
)

logger = structlog.get_logger(__name__)

# v3.0 Orchestrator singleton (lazy init)
_orchestrator = None


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = OrchestratorAgent()
    return _orchestrator

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(http_request: Request, request: ChatCompletionRequest):
    """OpenAI-compatible chat completions endpoint.

    Supports both streaming and non-streaming responses.
    """
    # Extract user_email: header > request body > default fallback
    user_email = (
        getattr(http_request.state, "user_email", "")
        or request.user
        or get_settings().gws_default_email
    )

    logger.info(
        "chat_completion_request",
        model=request.model,
        message_count=len(request.messages),
        stream=request.stream,
        user_email=user_email or None,
    )

    # Extract the last user message as the query
    user_messages = [m for m in request.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="메시지에 사용자 질문이 없습니다.")

    query = user_messages[-1].content

    # Resolve which LLM to use based on model selection
    model_type = resolve_model_type(request.model)

    # Build conversation history for context continuity
    messages_for_context = [
        {"role": m.role, "content": m.content} for m in request.messages
    ]

    if request.stream:
        return StreamingResponse(
            _stream_response(query, messages_for_context, model_type, request, user_email),
            media_type="text/event-stream",
        )

    # Non-streaming response (v3.0: Orchestrator)
    try:
        result = await _get_orchestrator().route_and_execute(
            query, messages_for_context, model_type, user_email=user_email
        )
        answer = result.get("answer", "")
    except Exception as e:
        logger.error("agent_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"에이전트 실행 실패: {str(e)}")

    response = ChatCompletionResponse(
        model=request.model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role="assistant", content=answer),
                finish_reason="stop",
            )
        ],
        usage=UsageInfo(
            prompt_tokens=len(query),
            completion_tokens=len(answer),
            total_tokens=len(query) + len(answer),
        ),
    )

    return response


async def _stream_response(
    query: str,
    messages: list,
    model_type: str,
    request: ChatCompletionRequest,
    user_email: str = "",
) -> AsyncGenerator[str, None]:
    """Stream response chunks in SSE format.

    Args:
        query: User query.
        messages: Full conversation history.
        model_type: "gemini" or "claude".
        request: Original request.
        user_email: User's email for GWS auth.

    Yields:
        SSE-formatted response chunks.
    """
    response_id = f"chatcmpl-{int(time.time())}"
    created = int(time.time())

    # Send initial chunk with role
    initial_chunk = ChatCompletionStreamResponse(
        id=response_id,
        created=created,
        model=request.model,
        choices=[
            ChatCompletionStreamChoice(
                delta=ChatCompletionStreamDelta(role="assistant"),
            )
        ],
    )
    yield f"data: {initial_chunk.model_dump_json()}\n\n"

    # Generate the full answer (v3.0: Orchestrator)
    try:
        result = await _get_orchestrator().route_and_execute(
            query, messages, model_type, user_email=user_email
        )
        answer = result.get("answer", "")
    except Exception as e:
        error_msg = f"오류가 발생했습니다: {str(e)}"
        error_chunk = ChatCompletionStreamResponse(
            id=response_id,
            created=created,
            model=request.model,
            choices=[
                ChatCompletionStreamChoice(
                    delta=ChatCompletionStreamDelta(content=error_msg),
                )
            ],
        )
        yield f"data: {error_chunk.model_dump_json()}\n\n"
        answer = ""

    # Stream the answer in chunks
    chunk_size = 20  # characters per chunk
    for i in range(0, len(answer), chunk_size):
        text_chunk = answer[i : i + chunk_size]
        stream_chunk = ChatCompletionStreamResponse(
            id=response_id,
            created=created,
            model=request.model,
            choices=[
                ChatCompletionStreamChoice(
                    delta=ChatCompletionStreamDelta(content=text_chunk),
                )
            ],
        )
        yield f"data: {stream_chunk.model_dump_json()}\n\n"
        await asyncio.sleep(0.01)  # Small delay for streaming effect

    # Send final chunk
    final_chunk = ChatCompletionStreamResponse(
        id=response_id,
        created=created,
        model=request.model,
        choices=[
            ChatCompletionStreamChoice(
                delta=ChatCompletionStreamDelta(),
                finish_reason="stop",
            )
        ],
    )
    yield f"data: {final_chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


@router.get("/v1/models")
async def list_models():
    """List available models (OpenAI-compatible)."""
    return ModelListResponse(
        data=[
            ModelInfo(id="skin1004-Search", owned_by="skin1004"),
            ModelInfo(id="skin1004-Analysis", owned_by="skin1004"),
        ]
    )


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "SKIN1004 AI Agent"}


