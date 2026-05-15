"""OpenAI-compatible API endpoints for Open WebUI integration."""

import asyncio
import time
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.agents.orchestrator import OrchestratorAgent
from app.config import get_settings
from app.db.mariadb import fetch_one, fetch_all
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

# Notion sync state (in-process, reset on restart)
_notion_sync_state: dict = {
    "running": False,
    "last_run": None,   # ISO datetime string
    "last_stats": None, # {new, updated, deleted, unchanged, skipped_404, errors}
    "error": None,
}


def _estimate_tokens(text: str) -> int:
    """Estimate token count for mixed Korean/English text.

    Korean characters use ~2-3 tokens per character in most LLMs.
    English words use ~1.3 tokens on average.
    """
    if not text:
        return 0
    korean = sum(1 for c in text if '\uac00' <= c <= '\ud7a3')
    ascii_chars = sum(1 for c in text if c.isascii())
    return int(korean * 2.5 + ascii_chars * 0.3)


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


@router.post("/api/save-roadmap")
async def save_roadmap(request: Request):
    """Save edited roadmap HTML to static file."""
    body = await request.body()
    with open("app/static/roadmap.html", "wb") as f:
        f.write(body)
    return {"status": "ok"}


@router.post("/v1/chat/completions")
async def chat_completions(http_request: Request, request: ChatCompletionRequest):
    """OpenAI-compatible chat completions endpoint.

    Supports both streaming and non-streaming responses.
    """
    # Extract user_email from JWT auth (NO default fallback — prevents accessing other users' GWS)
    user_email = (
        getattr(http_request.state, "user_email", "")
        or request.user
        or ""
    )

    # Server-side brand_filter enforcement: prefer JWT-cached value (Wave 1)
    brand_filter = request.brand_filter
    if not brand_filter:
        jwt_role = getattr(http_request.state, "jwt_role", "")
        jwt_bf = getattr(http_request.state, "jwt_brand_filter", "")
        if jwt_bf and jwt_role != "admin":
            brand_filter = jwt_bf
            logger.debug("brand_filter_from_jwt", brand_filter=brand_filter)
        elif not jwt_bf:
            # Fallback: DB lookup for tokens issued before Wave 1 JWT update
            user_id = getattr(http_request.state, "user_id", None)
            if user_id:
                try:
                    row = await asyncio.to_thread(
                        fetch_one,
                        "SELECT u.role, g.brand_filter FROM users u "
                        "LEFT JOIN user_groups ug ON u.ad_user_id = ug.ad_user_id "
                        "LEFT JOIN access_groups g ON ug.group_id = g.id AND g.brand_filter IS NOT NULL "
                        "WHERE u.id = %s LIMIT 1",
                        (user_id,),
                    )
                    if row and row["role"] != "admin" and row.get("brand_filter"):
                        brand_filter = row["brand_filter"]
                        logger.info("brand_filter_enforced_fallback", user_id=user_id, brand_filter=brand_filter)
                except Exception as e:
                    logger.warning("brand_filter_lookup_failed", user_id=user_id, error=str(e))

    logger.info(
        "chat_completion_request",
        model=request.model,
        message_count=len(request.messages),
        stream=request.stream,
        user_email=user_email or None,
        brand_filter=brand_filter or None,
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
    # No message limit — Gemini 2.5 Flash supports 1M token context
    # Strip images from older messages to keep payload small
    raw_messages = list(request.messages)

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

    enabled_sources = request.enabled_sources
    enabled_team_resources = request.enabled_team_resources

    if request.stream:
        return StreamingResponse(
            _stream_response(query, messages_for_context, model_type, request, user_email, images=images, brand_filter=brand_filter, enabled_sources=enabled_sources, enabled_team_resources=enabled_team_resources),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    # Non-streaming response (v3.0: Orchestrator)
    try:
        result = await _get_orchestrator().route_and_execute(
            query, messages_for_context, model_type, user_email=user_email, images=images, brand_filter=brand_filter, enabled_sources=enabled_sources, enabled_team_resources=enabled_team_resources
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
            prompt_tokens=_estimate_tokens(query),
            completion_tokens=_estimate_tokens(answer),
            total_tokens=_estimate_tokens(query) + _estimate_tokens(answer),
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
    brand_filter: str = None,
    enabled_sources: list = None,
    enabled_team_resources: dict = None,
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

    # Wave 4: Performance timing
    _t_start = time.monotonic()
    _t_first_token = None
    _detected_route = "direct"

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

    # Stream via orchestrator async generator
    streamed_live = False
    try:
        async for msg_type, content in _get_orchestrator().route_and_stream(
            query, messages, model_type, user_email=user_email, images=images or [],
            brand_filter=brand_filter, enabled_sources=enabled_sources,
            enabled_team_resources=enabled_team_resources,
        ):
            if msg_type == "source":
                _detected_route = content
                sc = ChatCompletionStreamResponse(
                    id=response_id, created=created, model=request.model,
                    choices=[ChatCompletionStreamChoice(
                        delta=ChatCompletionStreamDelta(content=f"<!-- source:{content} -->"),
                    )],
                )
                yield f"data: {sc.model_dump_json()}\n\n"

            elif msg_type == "chunk":
                # Real-time streamed token
                if not streamed_live:
                    _t_first_token = time.monotonic()
                streamed_live = True
                sc = ChatCompletionStreamResponse(
                    id=response_id, created=created, model=request.model,
                    choices=[ChatCompletionStreamChoice(
                        delta=ChatCompletionStreamDelta(content=content),
                    )],
                )
                yield f"data: {sc.model_dump_json()}\n\n"

            elif msg_type == "done":
                if streamed_live:
                    # Already streamed via chunks — skip duplicate
                    pass
                else:
                    # Non-streaming route (BQ/CS/Notion): send full answer in chunks
                    answer = content
                    chunk_size = 500
                    for i in range(0, len(answer), chunk_size):
                        piece = answer[i:i + chunk_size]
                        sc = ChatCompletionStreamResponse(
                            id=response_id, created=created, model=request.model,
                            choices=[ChatCompletionStreamChoice(
                                delta=ChatCompletionStreamDelta(content=piece),
                            )],
                        )
                        yield f"data: {sc.model_dump_json()}\n\n"
    except Exception as e:
        err_chunk = ChatCompletionStreamResponse(
            id=response_id, created=created, model=request.model,
            choices=[ChatCompletionStreamChoice(
                delta=ChatCompletionStreamDelta(content=f"오류가 발생했습니다: {str(e)}"),
            )],
        )
        yield f"data: {err_chunk.model_dump_json()}\n\n"

    # Wave 4: Compute timing metrics
    _t_end = time.monotonic()
    _total_ms = int((_t_end - _t_start) * 1000)
    _first_token_ms = int((_t_first_token - _t_start) * 1000) if _t_first_token else _total_ms

    # Send timing metadata as SSE comment (invisible to OpenAI-compatible clients)
    yield f"data: {{\"metrics\":{{\"first_token_ms\":{_first_token_ms},\"total_ms\":{_total_ms},\"route\":\"{_detected_route}\"}}}}\n\n"

    logger.info(
        "stream_completed",
        route=_detected_route,
        first_token_ms=_first_token_ms,
        total_ms=_total_ms,
        user_email=user_email or None,
        query=query[:80],
    )

    # Wave 4: Audit log (fire-and-forget)
    try:
        from app.db.mariadb import execute as db_execute
        asyncio.get_event_loop().run_in_executor(
            None,
            lambda: db_execute(
                "INSERT INTO audit_logs (user_email, route, query, first_token_ms, total_ms, model) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (user_email or "", _detected_route, query[:500], _first_token_ms, _total_ms, request.model),
            ),
        )
    except Exception:
        pass  # Never block response for audit logging

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


@router.get("/api/announcement")
async def get_announcement():
    """Return current site-wide announcement (empty string if none)."""
    from app.core.safety import get_announcement_manager
    am = get_announcement_manager()
    return {"message": am.message, "active": am.active}


@router.post("/api/announcement")
async def set_announcement(message: str = "", clear: bool = False):
    """Set or clear the site-wide announcement banner (admin only)."""
    from app.core.safety import get_announcement_manager
    am = get_announcement_manager()
    if clear:
        am.clear()
    else:
        am.set(message)
    return {"ok": True, "message": am.message, "active": am.active}


@router.get("/api/scheduler/status")
async def scheduler_status():
    """Return APScheduler job list and next run times."""
    from app.main import _get_scheduler
    sched = _get_scheduler()
    if sched is None:
        return {"running": False, "jobs": []}
    jobs = []
    for job in sched.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })
    return {"running": sched.running, "jobs": jobs}


@router.post("/api/scheduler/run")
async def scheduler_run_now(job_id: str):
    """Immediately trigger a scheduled job by ID (admin/dev only)."""
    import asyncio
    from app.main import _get_scheduler
    sched = _get_scheduler()
    if sched is None:
        return {"ok": False, "error": "scheduler not running"}
    job = sched.get_job(job_id)
    if job is None:
        return {"ok": False, "error": f"job '{job_id}' not found"}
    job.modify(next_run_time=__import__("datetime").datetime.now(job.next_run_time.tzinfo if job.next_run_time else None))
    return {"ok": True, "triggered": job_id}


@router.get("/api/datasources")
async def list_datasources():
    """Return available @@ data sources for frontend autocomplete."""
    from app.agents.orchestrator import OrchestratorAgent
    registry = OrchestratorAgent.get_db_registry()
    return [{"key": e["key"], "aliases": e["aliases"], "label": e["label"], "desc": e["desc"], "group": e.get("group", ""), "icon": e.get("icon", "")} for e in registry]


@router.get("/api/notion-sync/status")
async def notion_sync_status():
    """Return current Notion → Qdrant sync state."""
    return _notion_sync_state


async def _run_notion_sync_bg():
    """Background task: run full Notion → Qdrant pipeline."""
    global _notion_sync_state
    import datetime
    _notion_sync_state["running"] = True
    _notion_sync_state["error"] = None
    try:
        from scripts.notion_qdrant_pipeline import run_pipeline
        stats = await asyncio.to_thread(run_pipeline)
        _notion_sync_state["last_stats"] = stats
        _notion_sync_state["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        # Invalidate safety.py qdrant cache so next poll reflects new data
        try:
            import app.core.safety as _sf
            _sf._qdrant_cache = {}
            _sf._qdrant_cache_time = 0
        except Exception:
            pass
    except Exception as e:
        _notion_sync_state["error"] = str(e)[:120]
        logger.error("notion_sync_failed", error=str(e))
    finally:
        _notion_sync_state["running"] = False


@router.post("/api/notion-sync")
async def trigger_notion_sync(request: Request):
    """Trigger Notion → Qdrant sync pipeline (admin only)."""
    from app.api.auth_middleware import get_current_user
    user = await get_current_user(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    if _notion_sync_state["running"]:
        return {"ok": False, "error": "이미 동기화 중입니다"}
    asyncio.create_task(_run_notion_sync_bg())
    return {"ok": True, "message": "동기화를 시작합니다"}


@router.get("/health")
async def health_check():
    """Health check endpoint (liveness)."""
    return {"status": "ok", "service": "Craver AI Agent"}


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


