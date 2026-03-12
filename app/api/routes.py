"""OpenAI-compatible API endpoints for Open WebUI integration."""

import asyncio
import time
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

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
    extract_images,
    extract_text,
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


@router.get("/dashboard")
async def dashboard():
    """Serve the Dashboard Hub page."""
    return FileResponse("app/static/dashboard.html", media_type="text/html")


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

    last_content = user_messages[-1].content
    query = extract_text(last_content)
    images = extract_images(last_content)

    if images:
        logger.info("multimodal_request", image_count=len(images), text_length=len(query))

    # Resolve which LLM to use based on model selection
    model_type = resolve_model_type(request.model)

    # Build conversation history for context continuity
    # Truncate to last 50 messages (25 turns) for deeper conversation memory
    # Strip images from older messages to keep payload small
    MAX_CONTEXT_MESSAGES = 50
    raw_messages = list(request.messages)
    if len(raw_messages) > MAX_CONTEXT_MESSAGES + 1:
        # Always keep the last message (current query) + recent history
        raw_messages = raw_messages[-(MAX_CONTEXT_MESSAGES + 1):]

    messages_for_context = []
    for idx, m in enumerate(raw_messages):
        content = m.content
        if isinstance(content, list):
            # Keep images only on the last user message
            is_last_user = (m.role == "user" and m == user_messages[-1])
            if is_last_user:
                # Convert Pydantic models to plain dicts
                parts = []
                for part in content:
                    if hasattr(part, "model_dump"):
                        parts.append(part.model_dump())
                    elif isinstance(part, dict):
                        parts.append(part)
                    else:
                        parts.append({"type": "text", "text": str(part)})
                messages_for_context.append({"role": m.role, "content": parts})
            else:
                # Older messages: strip images, keep text only
                messages_for_context.append({"role": m.role, "content": extract_text(content)})
        else:
            messages_for_context.append({"role": m.role, "content": content})

    if request.stream:
        return StreamingResponse(
            _stream_response(query, messages_for_context, model_type, request, user_email, images=images),
            media_type="text/event-stream",
        )

    # Non-streaming response (v3.0: Orchestrator)
    try:
        result = await _get_orchestrator().route_and_execute(
            query, messages_for_context, model_type, user_email=user_email, images=images
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
    images: list = None,
) -> AsyncGenerator[str, None]:
    """Stream response chunks in SSE format.

    Args:
        query: User query.
        messages: Full conversation history.
        model_type: "gemini" or "claude".
        request: Original request.
        user_email: User's email for GWS auth.
        images: Extracted images (list of {"data": bytes, "mime_type": str}).

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
    result = None
    try:
        result = await _get_orchestrator().route_and_execute(
            query, messages, model_type, user_email=user_email, images=images or []
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

    # Send source label before answer content
    source = result.get("source", "direct") if result else "direct"
    source_chunk = ChatCompletionStreamResponse(
        id=response_id,
        created=created,
        model=request.model,
        choices=[
            ChatCompletionStreamChoice(
                delta=ChatCompletionStreamDelta(content=f"<!-- source:{source} -->"),
            )
        ],
    )
    yield f"data: {source_chunk.model_dump_json()}\n\n"

    # Stream the answer in chunks
    chunk_size = 80  # characters per chunk (bigger = fewer SSE frames = faster)
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
            ModelInfo(id="skin1004-Analysis", owned_by="skin1004"),
        ]
    )


# ---------------------------------------------------------------------------
# Safety & Maintenance endpoints
# ---------------------------------------------------------------------------

@router.post("/admin/maintenance")
async def toggle_maintenance(action: str = "on", reason: str = ""):
    """Toggle maintenance mode manually.

    Args:
        action: "on" or "off".
        reason: Reason text (only used when action=on).
    """
    from app.core.safety import get_maintenance_manager
    mm = get_maintenance_manager()

    if action == "on":
        mm.activate(reason or "\uc218\ub3d9 \uc810\uac80\ubaa8\ub4dc")
        return {"ok": True, "maintenance": mm.status}
    elif action == "off":
        mm.deactivate()
        return {"ok": True, "maintenance": mm.status}
    else:
        raise HTTPException(status_code=400, detail="action must be 'on' or 'off'")


@router.get("/admin/maintenance/status")
async def maintenance_status():
    """Return current maintenance state (polled by frontend banner)."""
    from app.core.safety import get_maintenance_manager
    mm = get_maintenance_manager()
    return mm.status


@router.get("/safety/status")
async def safety_status():
    """Full safety dashboard: maintenance + services + circuit breakers."""
    from app.core.safety import get_safety_status
    return get_safety_status()


@router.get("/health")
async def health_check():
    """Health check endpoint (liveness)."""
    return {"status": "ok", "service": "SKIN1004 AI Agent"}


@router.get("/health/ready")
async def readiness_check():
    """Readiness check — reports warmup status of all subsystems."""
    import app.agents.sql_agent as sql_mod

    # Notion warmup
    try:
        from app.agents.notion_agent import _page_titles
        notion_ready = len(_page_titles) > 0
        notion_count = len(_page_titles)
    except Exception:
        notion_ready = False
        notion_count = 0

    # BigQuery schema cache
    bq_ready = bool(sql_mod._schema_cache)

    # CS Q&A database
    try:
        from app.agents.cs_agent import _qa_cache, _cache_loaded
        cs_ready = _cache_loaded
        cs_count = len(_qa_cache)
    except Exception:
        cs_ready = False
        cs_count = 0

    all_ready = notion_ready and bq_ready and cs_ready
    return {
        "status": "ready" if all_ready else "warming_up",
        "notion_warmup": notion_ready,
        "notion_pages": notion_count,
        "bq_schema": bq_ready,
        "cs_loaded": cs_ready,
        "cs_db_count": cs_count,
    }


