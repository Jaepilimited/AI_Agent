"""SKIN1004 Enterprise AI - FastAPI application entry point."""

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.auth_routes import auth_router
from app.api.middleware import setup_middleware
from app.api.routes import router
from app.config import get_settings

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


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "application_started",
            host=settings.host,
            port=settings.port,
            project=settings.gcp_project_id,
        )
        # Pre-fetch Notion titles and BQ schema in parallel at startup
        asyncio.create_task(_warmup_notion_titles())
        asyncio.create_task(_warmup_bq_schema())
        yield
        logger.info("application_shutdown")

    app = FastAPI(
        title="SKIN1004 Enterprise AI",
        description="Text-to-SQL + Agentic RAG Hybrid AI System",
        version="3.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Setup middleware (CORS, logging)
    setup_middleware(app)

    # Include API routes (before static mount to avoid path conflicts)
    app.include_router(router)
    app.include_router(auth_router)

    # Serve static files (charts, etc.)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    return app


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
    """Pre-load BigQuery schema at startup so first SQL query skips schema fetch."""
    try:
        import app.agents.sql_agent as sql_mod
        if not sql_mod._schema_cache:
            from app.core.bigquery import get_bigquery_client
            settings = get_settings()
            bq = get_bigquery_client()
            schema = await asyncio.to_thread(
                bq.get_table_schema, settings.sales_table_full_path
            )
            schema_lines = [
                f"  - {col['name']} ({col['type']}): {col['description']}"
                for col in schema
            ]
            sql_mod._schema_cache = "\n\n### 실제 테이블 스키마\n" + "\n".join(schema_lines)
            logger.info("bq_schema_warmup_done", columns=len(schema))
    except Exception as e:
        logger.warning("bq_schema_warmup_failed", error=str(e))


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
