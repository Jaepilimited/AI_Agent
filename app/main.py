"""SKIN1004 Enterprise AI - FastAPI application entry point.

Single server on port 3000: AI backend + custom frontend.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin_api import admin_router
from app.api.admin_group_api import group_router, ad_router
from app.api.auth_api import auth_api_router
from app.api.auth_middleware import get_optional_user
from app.api.auth_routes import auth_router
from app.api.conversation_api import conversation_router
from app.api.eval_api import eval_router
from app.api.face_search_routes import router as face_search_router
from app.api.harness_api import router as harness_router
from app.api.middleware import setup_middleware
from app.api.routes import router
from app.config import get_settings
from app.core.log_scrub import scrub_identity_processor
from app.db.mariadb import fetch_one, execute

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        scrub_identity_processor,
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger(__name__)

# Module-level scheduler reference (accessible via _get_scheduler())
_scheduler_instance = None

def _set_scheduler(sched) -> None:
    global _scheduler_instance
    _scheduler_instance = sched

def _get_scheduler():
    return _scheduler_instance

# Directories
_BASE_DIR = Path(__file__).parent
_FRONTEND_DIR = _BASE_DIR / "frontend"
_STATIC_DIR = _BASE_DIR / "static"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Expand default thread pool so asyncio.to_thread() can run more
        # sync DB/LLM calls concurrently (default is min(32, os.cpu_count()+4)).
        from concurrent.futures import ThreadPoolExecutor
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=100, thread_name_prefix="skin1004"))
        logger.info("thread_pool_configured", max_workers=100)

        # Ensure admin user exists in MariaDB
        _ensure_admin()
        _ensure_audit_table()
        from app.db.mariadb import (
            ensure_knowledge_wiki_table,
            ensure_wiki_extraction_log_table,
            ensure_wiki_entity_aliases_table,
            ensure_wiki_graph_edges_table,
            ensure_wiki_entity_pages_table,
            ensure_wiki_communities_table,
            ensure_anon_columns,
            ensure_eval_tables,
        )
        ensure_knowledge_wiki_table()
        ensure_wiki_extraction_log_table()
        ensure_wiki_entity_aliases_table()
        ensure_wiki_graph_edges_table()
        ensure_wiki_entity_pages_table()
        ensure_wiki_communities_table()
        ensure_anon_columns()
        ensure_eval_tables()
        logger.info("mariadb_initialized")

        logger.info(
            "application_started",
            host=settings.host,
            port=settings.port,
            project=settings.gcp_project_id,
        )
        # Pre-fetch Notion titles, BQ schema, and CS DB in parallel at startup
        asyncio.create_task(_warmup_notion_titles())
        asyncio.create_task(_warmup_bq_schema())
        asyncio.create_task(_warmup_cs_db())
        asyncio.create_task(_warmup_team_resources())
        asyncio.create_task(_warmup_qdrant_cache())
        asyncio.create_task(_warmup_llm_clients())
        # face-search 워밍업은 OOM 위험으로 비활성화 (SigLIP+InsightFace+OCR 동시 로드 시 메모리 폭주).
        # 첫 query에 lazy load (30초) → 이후는 빠름. 인스턴스 메모리 늘리면 재활성 가능.
        # asyncio.create_task(_warmup_face_search())
        # Safety: auto-detect table updates via __TABLES__ metadata polling
        asyncio.create_task(_start_maintenance_monitor())
        # APScheduler: daily 01:00 team resources sync + hourly wiki extraction
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(_sync_team_resources_job, "cron", hour=1, minute=0, id="team_sync_daily")
        _scheduler.add_job(_extract_wiki_hourly, "cron", minute=15, id="wiki_extract_hourly")
        _scheduler.add_job(_qdrant_pipeline_job, "cron", hour=5, minute=0, id="qdrant_pipeline_daily")
        # AD sync is handled exclusively by Windows Task Scheduler (SKIN1004-AD-Sync-Daily at 22:00).
        # Removed from APScheduler to prevent concurrent dual-trigger race condition.
        _scheduler.start()
        _set_scheduler(_scheduler)
        logger.info("scheduler_started", jobs=["team_sync_daily_01:00", "wiki_extract_hourly_:15", "qdrant_pipeline_05:00"])
        yield
        logger.info("application_shutdown")

    app = FastAPI(
        title="Craver Enterprise AI",
        description="Text-to-SQL + Agentic RAG Hybrid AI System",
        version="4.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Setup middleware (CORS, logging)
    setup_middleware(app)

    # --- API routes ---
    app.include_router(router)           # /v1/chat/completions, /dashboard, /health, etc.
    app.include_router(auth_router)      # /auth/google/*
    app.include_router(auth_api_router)  # /api/auth/*
    app.include_router(conversation_router)  # /api/conversations/*
    app.include_router(admin_router)         # /api/admin/*
    app.include_router(group_router)         # /api/admin/groups/*
    app.include_router(ad_router)            # /api/admin/ad/*
    app.include_router(eval_router)          # /api/admin/eval/*
    app.include_router(harness_router)       # /harness, /api/harness/*
    app.include_router(face_search_router)   # /face-search, /face-search/query, /face-search/thumb/*

    # --- Frontend routes ---

    # CRM 설정 페이지 리디렉트 (OAuth 콜백 후 track.skin1004.app/settings → CRM)
    @app.get("/settings")
    async def crm_settings_redirect(request: Request):
        qs = request.url.query
        target = "http://172.16.1.250:3100/settings"
        if qs:
            target += f"?{qs}"
        return RedirectResponse(url=target, status_code=302)

    _NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}

    @app.get("/login")
    async def login_page():
        from app.core.safety import get_maintenance_manager
        if get_maintenance_manager().active:
            return FileResponse(str(_FRONTEND_DIR / "maintenance.html"), media_type="text/html", headers=_NO_CACHE)
        return FileResponse(str(_FRONTEND_DIR / "login.html"), media_type="text/html", headers=_NO_CACHE)

    @app.get("/")
    async def index(request: Request):
        from app.core.safety import get_maintenance_manager
        if get_maintenance_manager().active:
            return FileResponse(str(_FRONTEND_DIR / "maintenance.html"), media_type="text/html", headers=_NO_CACHE)
        # Check if user is authenticated
        token = request.cookies.get("token")
        if not token:
            return RedirectResponse(url="/login", status_code=302)
        from fastapi.responses import HTMLResponse
        html = (_FRONTEND_DIR / "chat.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})

    # Serve static files (no-cache middleware for dev)
    from starlette.middleware import Middleware
    from starlette.responses import Response

    class NoCacheStaticFiles(StaticFiles):
        async def __call__(self, scope, receive, send):
            async def _send(msg):
                if msg.get("type") == "http.response.start":
                    headers = list(msg.get("headers", []))
                    headers.append([b"cache-control", b"no-store, no-cache, must-revalidate, max-age=0"])
                    msg["headers"] = headers
                await send(msg)
            await super().__call__(scope, receive, _send)

    app.mount("/frontend", NoCacheStaticFiles(directory=str(_FRONTEND_DIR)), name="frontend")
    app.mount("/static", NoCacheStaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


def _ensure_admin():
    """Ensure jeffrey@skin1004korea.com is admin with all models in MariaDB."""
    try:
        # Find AD user for jeffrey
        ad_user = fetch_one(
            "SELECT id, email FROM ad_users WHERE email = %s AND is_active = 1",
            ("jeffrey@skin1004korea.com",),
        )
        if not ad_user:
            logger.warning("admin_ad_user_not_found", email="jeffrey@skin1004korea.com")
            return

        # Check if user exists
        user = fetch_one(
            "SELECT id, role, allowed_models FROM users WHERE ad_user_id = %s",
            (ad_user["id"],),
        )
        if user:
            # Update to admin if needed
            if user["role"] != "admin" or "skin1004-Analysis" not in (user["allowed_models"] or ""):
                execute(
                    "UPDATE users SET role = 'admin', allowed_models = %s WHERE id = %s",
                    ("skin1004-Analysis", user["id"]),
                )
                logger.info("admin_ensured", email="jeffrey@skin1004korea.com")
        else:
            logger.info("admin_user_needs_signup", email="jeffrey@skin1004korea.com")
    except Exception as e:
        logger.warning("ensure_admin_failed", error=str(e))


def _ensure_audit_table():
    """Create audit_logs table if it doesn't exist (MariaDB or SQLite)."""
    try:
        execute(
            """CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                user_email VARCHAR(255),
                route VARCHAR(50),
                query TEXT,
                first_token_ms INT,
                total_ms INT,
                model VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
    except Exception:
        # SQLite syntax fallback (dev mode uses AUTOINCREMENT not AUTO_INCREMENT)
        try:
            execute(
                """CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_email TEXT,
                    route TEXT,
                    query TEXT,
                    first_token_ms INTEGER,
                    total_ms INTEGER,
                    model TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )"""
            )
        except Exception as e:
            logger.warning("audit_table_create_failed", error=str(e))


async def _warmup_notion_titles():
    """Pre-fetch Notion allowlist titles at startup so first query is fast."""
    try:
        from app.agents.notion_agent import NotionAgent
        agent = NotionAgent()
        if agent.token:
            await agent._warm_up()
            logger.info("notion_titles_warmup_done")
    except Exception as e:
        logger.warning("notion_titles_warmup_failed", error=str(e))


async def _warmup_bq_schema():
    """Pre-load BigQuery schemas (sales + all marketing tables) into per-table cache at startup."""
    try:
        import app.agents.sql_agent as sql_mod
        from app.core.bigquery import get_bigquery_client
        settings = get_settings()
        bq = get_bigquery_client()

        # 1) Primary sales table
        if not sql_mod._schema_cache_sales:
            schema = await asyncio.to_thread(
                bq.get_table_schema, settings.sales_table_full_path
            )
            schema_lines = [
                f"  - {col['name']} ({col['type']}): {col['description']}"
                for col in schema
            ]
            table_short = settings.sales_table_full_path.rsplit(".", 1)[-1]
            sql_mod._schema_cache_sales = f"\n\n### 실제 테이블 스키마 ({table_short})\n" + "\n".join(schema_lines)
            logger.info("bq_schema_warmup_sales_done", columns=len(schema))

        # 2) Pre-cache all marketing table schemas in parallel
        uncached = [
            (t[0], t[1]) for t in sql_mod.MARKETING_TABLES
            if t[0] not in sql_mod._schema_cache_tables
        ]

        async def _fetch_one(table_path, label):
            try:
                tbl_schema = await asyncio.to_thread(bq.get_table_schema, table_path)
                tbl_lines = [
                    f"  - {col['name']} ({col['type']}): {col['description']}"
                    for col in tbl_schema
                ]
                tbl_short = table_path.rsplit(".", 1)[-1]
                sql_mod._schema_cache_tables[table_path] = f"\n\n### {label} ({tbl_short})\n" + "\n".join(tbl_lines)
                return True
            except Exception as e:
                logger.warning("bq_schema_warmup_table_failed", table=table_path, error=str(e))
                return False

        results = await asyncio.gather(*[_fetch_one(tp, lb) for tp, lb in uncached])
        loaded = sum(1 for r in results if r) + sum(1 for t in sql_mod.MARKETING_TABLES if t[0] in sql_mod._schema_cache_tables and t[0] not in dict(uncached))
        logger.info("bq_schema_warmup_done", marketing_tables_cached=loaded, parallel=len(uncached))
    except Exception as e:
        logger.warning("bq_schema_warmup_failed", error=str(e))


async def _warmup_cs_db():
    """Pre-load CS Q&A data from Google Spreadsheet at startup."""
    from app.agents.cs_agent import warmup
    for attempt in range(3):
        try:
            count = await warmup()
            logger.info("cs_db_warmup_done", qa_count=count, attempt=attempt + 1)
            return
        except Exception as e:
            logger.warning("cs_db_warmup_failed", error=str(e), attempt=attempt + 1)
            if attempt < 2:
                await asyncio.sleep(5)


async def _warmup_team_resources():
    """Pre-load team resources from MariaDB at startup."""
    try:
        from app.agents.team_agent import warmup
        count = await warmup()
        logger.info("team_resources_warmup_done", count=count)
    except Exception as e:
        logger.warning("team_resources_warmup_failed", error=str(e))


async def _warmup_qdrant_cache():
    """Pre-load Qdrant team chunk counts at startup."""
    try:
        import asyncio
        from app.core.safety import get_safety_status
        await asyncio.to_thread(get_safety_status)
        logger.info("qdrant_cache_warmup_done")
    except Exception as e:
        logger.warning("qdrant_cache_warmup_failed", error=str(e))


async def _warmup_llm_clients():
    """Pre-establish TLS/HTTP connections to Gemini + Claude.

    Without this, the first real chat request after worker startup pays
    ~20-30s in SDK init + TLS handshake + connection pool setup, making
    the unlucky first user experience terrible.
    """
    async def _warm_gemini():
        try:
            from app.core.llm import get_flash_client
            client = get_flash_client()
            await asyncio.to_thread(
                client.generate, "hi", temperature=0.0, max_output_tokens=5
            )
            logger.info("gemini_warmup_done")
        except Exception as e:
            logger.warning("gemini_warmup_failed", error=str(e)[:200])

    async def _warm_claude_opus():
        # Opus is the primary chat model (resolve_model_type → MODEL_CLAUDE → Opus).
        try:
            from app.core.llm import get_llm_client, MODEL_CLAUDE
            client = get_llm_client(MODEL_CLAUDE)
            await asyncio.to_thread(
                client.generate, "hi", temperature=0.0, max_output_tokens=5
            )
            logger.info("claude_opus_warmup_done")
        except Exception as e:
            logger.warning("claude_opus_warmup_failed", error=str(e)[:200])

    await asyncio.gather(_warm_gemini(), _warm_claude_opus())


async def _sync_team_resources_job():
    """Daily 01:00 cron job: Notion → MariaDB sync."""
    try:
        import asyncio
        from scripts.sync_team_resources import sync
        count = await asyncio.to_thread(sync, dry_run=False)
        from app.agents.team_agent import warmup
        await warmup()
        logger.info("team_resources_daily_sync_done", count=count)
    except Exception as e:
        logger.error("team_resources_daily_sync_failed", error=str(e))


async def _qdrant_pipeline_job():
    """Daily 05:00: Notion → Qdrant 서버 직접 업로드 (전체 sync)."""
    try:
        from scripts.notion_qdrant_pipeline import run_pipeline
        stats = await asyncio.to_thread(run_pipeline)
        logger.info("qdrant_pipeline_done", **{k: v for k, v in stats.items() if isinstance(v, (int, bool))})
    except Exception as e:
        logger.error("qdrant_pipeline_failed", error=str(e))


async def _extract_wiki_hourly():
    """Hourly cron: mine new Q/A pairs from the last 75 minutes into knowledge_wiki.

    75 min window gives a 15-minute safety overlap with the previous run so no
    pair is missed if a batch runs long. The extractor already skips pairs
    that already have wiki rows.
    """
    try:
        from app.knowledge.wiki_extractor import extract_batch
        result = await extract_batch(since_minutes=75, limit=200, max_concurrent=4)
        logger.info("wiki_hourly_extract_done", **result)
    except Exception as e:
        logger.error("wiki_hourly_extract_failed", error=str(e))


async def _warmup_face_search():
    """SigLIP + InsightFace 모델 미리 로드. 첫 /face-search/query 30초 → ~1초."""
    try:
        from app.agents import face_clip_agent
        await asyncio.to_thread(face_clip_agent.warmup)
    except Exception as e:
        logger.warning("face_search_warmup_failed", error=str(e)[:200])


async def _start_maintenance_monitor():
    """Start the auto-detect maintenance loop (polls __TABLES__ every 60s)."""
    try:
        from app.core.safety import maintenance_auto_detect_loop
        await maintenance_auto_detect_loop(interval=60.0)
    except Exception as e:
        logger.warning("maintenance_monitor_failed", error=str(e))


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        # Allow large request bodies for base64 image uploads (~50MB)
        h11_max_incomplete_event_size=50 * 1024 * 1024,
    )
