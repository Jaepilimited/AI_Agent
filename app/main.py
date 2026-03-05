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
from app.api.auth_api import auth_api_router
from app.api.auth_middleware import get_optional_user
from app.api.auth_routes import auth_router
from app.api.conversation_api import conversation_router
from app.api.middleware import setup_middleware
from app.api.routes import router
from app.config import get_settings
from app.db.database import init_db

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger(__name__)

# Directories
_BASE_DIR = Path(__file__).parent
_FRONTEND_DIR = _BASE_DIR / "frontend"
_STATIC_DIR = _BASE_DIR / "static"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Initialize SQLite DB
        init_db()
        _ensure_admin()
        logger.info("sqlite_db_initialized", path=settings.sqlite_db_path)

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
        # Safety: auto-detect table updates via __TABLES__ metadata polling
        asyncio.create_task(_start_maintenance_monitor())
        yield
        logger.info("application_shutdown")

    app = FastAPI(
        title="SKIN1004 Enterprise AI",
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

    # --- Frontend routes ---
    @app.get("/login")
    async def login_page():
        return FileResponse(str(_FRONTEND_DIR / "login.html"), media_type="text/html")

    @app.get("/")
    async def index(request: Request):
        # Check if user is authenticated
        token = request.cookies.get("token")
        if not token:
            return RedirectResponse(url="/login", status_code=302)
        return FileResponse(str(_FRONTEND_DIR / "chat.html"), media_type="text/html")

    # Serve static files
    app.mount("/frontend", StaticFiles(directory=str(_FRONTEND_DIR)), name="frontend")
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


def _ensure_admin():
    """Ensure jeffrey@skin1004korea.com is admin with all models."""
    try:
        from app.db.database import get_session_factory
        from app.db.models import User
        Session = get_session_factory()
        db = Session()
        try:
            user = db.query(User).filter(User.email == "jeffrey@skin1004korea.com").first()
            if user:
                changed = False
                if user.role != "admin":
                    user.role = "admin"
                    changed = True
                if not user.allowed_models or "skin1004-Analysis" not in (user.allowed_models or ""):
                    user.allowed_models = "skin1004-Analysis"
                    changed = True
                if changed:
                    db.commit()
                    logger.info("admin_ensured", email=user.email)
        finally:
            db.close()
    except Exception as e:
        logger.warning("ensure_admin_failed", error=str(e))


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

        # 2) Pre-cache all marketing table schemas individually
        loaded = 0
        for table_entry in sql_mod.MARKETING_TABLES:
            table_path, label = table_entry[0], table_entry[1]
            if table_path in sql_mod._schema_cache_tables:
                loaded += 1
                continue
            try:
                tbl_schema = await asyncio.to_thread(
                    bq.get_table_schema, table_path
                )
                tbl_lines = [
                    f"  - {col['name']} ({col['type']}): {col['description']}"
                    for col in tbl_schema
                ]
                tbl_short = table_path.rsplit(".", 1)[-1]
                sql_mod._schema_cache_tables[table_path] = f"\n\n### {label} ({tbl_short})\n" + "\n".join(tbl_lines)
                loaded += 1
            except Exception as e:
                logger.warning("bq_schema_warmup_table_failed", table=table_path, error=str(e))

        logger.info("bq_schema_warmup_done", marketing_tables_cached=loaded)
    except Exception as e:
        logger.warning("bq_schema_warmup_failed", error=str(e))


async def _warmup_cs_db():
    """Pre-load CS Q&A data from Google Spreadsheet at startup."""
    try:
        from app.agents.cs_agent import warmup
        count = await warmup()
        logger.info("cs_db_warmup_done", qa_count=count)
    except Exception as e:
        logger.warning("cs_db_warmup_failed", error=str(e))


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
