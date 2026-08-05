"""SKIN1004 Enterprise AI - FastAPI application entry point.

Single server on port 3000: AI backend + custom frontend.
"""

# torch must be imported before google.cloud.bigquery to prevent
# Windows DLL initialization conflict (c10.dll vs gRPC DLLs)
try:
    import torch  # noqa: F401
except Exception:
    pass

import asyncio
import os
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

        # Windows proactor event loop logs a noisy ConnectionResetError traceback
        # whenever a client disconnects mid-response (benign). Suppress only that case.
        _default_handler = loop.get_exception_handler()

        def _quiet_proactor_reset(loop, context):
            exc = context.get("exception")
            if isinstance(exc, ConnectionResetError):
                return
            if _default_handler:
                _default_handler(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(_quiet_proactor_reset)

        # Ensure admin user exists in MariaDB
        await asyncio.to_thread(_ensure_admin)
        await asyncio.to_thread(_ensure_audit_table)
        from app.db.mariadb import (
            ensure_fi_permission_column,
            ensure_knowledge_wiki_table,
            ensure_wiki_extraction_log_table,
            ensure_wiki_entity_aliases_table,
            ensure_wiki_graph_edges_table,
            ensure_wiki_entity_pages_table,
            ensure_wiki_communities_table,
            ensure_anon_columns,
            ensure_eval_tables,
            ensure_agent_skills_table,
            ensure_quality_snapshots_table,
            ensure_knowledge_gaps_table,
        )
        # Sequential — some of these create tables others may reference (FK-adjacent),
        # so we preserve original ordering rather than gathering concurrently.
        for _ensure_fn in (
            ensure_fi_permission_column,
            ensure_knowledge_wiki_table,
            ensure_wiki_extraction_log_table,
            ensure_wiki_entity_aliases_table,
            ensure_wiki_graph_edges_table,
            ensure_wiki_entity_pages_table,
            ensure_wiki_communities_table,
            ensure_anon_columns,
            ensure_eval_tables,
            ensure_agent_skills_table,
            ensure_quality_snapshots_table,
            ensure_knowledge_gaps_table,
        ):
            await asyncio.to_thread(_ensure_fn)
        from app.core.self_check import ensure_self_check_tables
        await asyncio.to_thread(ensure_self_check_tables)
        from app.core.ingredients import ensure_ingredient_tables
        await asyncio.to_thread(ensure_ingredient_tables)
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
        #
        # 이관 리다이렉트가 켜진 인스턴스는 사용자를 신규 서버로 넘기기만 하는 껍데기이므로
        # 배치를 돌리지 않는다. 그러지 않으면 구/신 서버가 같은 잡을 이중 실행하고,
        # 특히 05:00 Qdrant 파이프라인은 **동일한 Qdrant Cloud 컬렉션에 양쪽이 동시 업로드**한다.
        if settings.migrated_redirect_url:
            logger.warning("scheduler_skipped_migrated_instance",
                           reason="redirect-only shim; batch jobs run on the new server")
        else:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            _scheduler = AsyncIOScheduler()
            _scheduler.add_job(_sync_team_resources_job, "cron", hour=1, minute=0, id="team_sync_daily")
            _scheduler.add_job(_extract_wiki_hourly, "cron", minute=15, id="wiki_extract_hourly")
            _scheduler.add_job(_qdrant_pipeline_job, "cron", hour=5, minute=0, id="qdrant_pipeline_daily")
            _scheduler.add_job(_quality_snapshot_job, "cron", hour=0, minute=5, id="quality_snapshot_daily")
            _scheduler.add_job(_weekly_growth_report_job, "cron", day_of_week="mon", hour=0, minute=10, id="weekly_growth_report")
            # 자가 점검 — AD sync(22:00) 와 새벽 배치가 모두 끝난 뒤에 돌려야
            # "어제 배치가 돌았는가"를 제대로 판정한다. 07:30 이면 여유 있다.
            # 지식맵 빌드 — APP 크론에서 이관 (APP 은 Gemini 가 막혀 돌 수 없었다).
            # deploy/crontab.app-server 에 다시 등록하면 이중 실행이 된다.
            _scheduler.add_job(_knowledge_map_job, "cron", hour=3, minute=0, id="knowledge_map_daily")
            _scheduler.add_job(_ingredient_sync_job, "cron", hour=4, minute=0, id="ingredient_sync_daily")
            _scheduler.add_job(_self_check_job, "cron", hour=7, minute=30, id="self_check_daily")
            # AD sync is handled exclusively by the APP server crontab (22:00).
            # Removed from APScheduler to prevent concurrent dual-trigger race condition.
            _scheduler.start()
            _set_scheduler(_scheduler)
        logger.info("scheduler_started", jobs=["team_sync_daily_01:00", "wiki_extract_hourly_:15", "qdrant_pipeline_05:00", "quality_snapshot_00:05", "weekly_growth_mon_00:10", "knowledge_map_03:00", "ingredient_sync_04:00", "self_check_07:30"])
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

    # --- 서버 이관 리다이렉트 (2026-07-30) ---
    # MIGRATED_REDIRECT_URL 이 설정돼 있으면 모든 사용자 요청을 신규 서버로 넘긴다.
    # 이관 후 기존 주소(172.16.1.250:3000)로 들어오는 접속 때문에 대화가 구 DB에만
    # 쌓여 데이터가 갈라지는 것을 막기 위한 장치다.
    #   끄는 방법: .env 에서 MIGRATED_REDIRECT_URL 을 지우고 pm2 restart
    # 제외 경로:
    #   /health          — watchdog 가 죽은 것으로 오판하지 않도록
    #   /auth/google, /settings — CRM OAuth 콜백 프록시(신규 서버로 옮기지 않은 기능)
    # ⚠️ os.getenv 로 읽으면 안 된다 — .env 는 pydantic-settings 가 직접 읽고
    #    os.environ 에는 넣지 않으므로 항상 빈 값이 된다(2026-07-30 실제 겪음).
    _redirect_base = settings.migrated_redirect_url.rstrip("/")
    if _redirect_base:
        _keep_prefixes = ("/health", "/auth/google", "/settings", "/api/auth/google")

        @app.middleware("http")
        async def _migrated_redirect(request: Request, call_next):
            path = request.url.path
            if path.startswith(_keep_prefixes):
                return await call_next(request)
            target = f"{_redirect_base}{path}"
            if request.url.query:
                target += f"?{request.url.query}"
            # 307: 메서드와 본문을 보존한다 (POST 도 안전하게 넘어감)
            return RedirectResponse(url=target, status_code=307)

        logger.warning("migrated_redirect_enabled", target=_redirect_base)

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
        return FileResponse(str(_FRONTEND_DIR / "login.html"), media_type="text/html", headers=_NO_CACHE)

    _CHAT_HTML_CACHE = (_FRONTEND_DIR / "chat.html").read_text(encoding="utf-8")

    @app.get("/")
    async def index(request: Request):
        # Check if user is authenticated
        token = request.cookies.get("token")
        if not token:
            return RedirectResponse(url="/login", status_code=302)
        from fastapi.responses import HTMLResponse
        return HTMLResponse(_CHAT_HTML_CACHE, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})

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
    # Add context_len column (idempotent — silently ignored if already exists)
    try:
        execute("ALTER TABLE audit_logs ADD COLUMN context_len INT DEFAULT NULL")
    except Exception:
        pass


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
        # Claude is the primary chat model (all chat requests use MODEL_CLAUDE).
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
    from app.core.self_check import track_job
    try:
        import asyncio
        with track_job("team_sync_daily") as jr:
            from scripts.sync_team_resources import sync
            count = await asyncio.to_thread(sync, dry_run=False)
            from app.agents.team_agent import warmup
            await warmup()
            jr.set_note(f"{count}건 동기화")
        logger.info("team_resources_daily_sync_done", count=count)
    except Exception as e:
        logger.error("team_resources_daily_sync_failed", error=str(e))


async def _qdrant_pipeline_job():
    """Daily 05:00: Notion → Qdrant 서버 직접 업로드 (전체 sync)."""
    from app.core.self_check import track_job
    try:
        with track_job("qdrant_pipeline_daily") as jr:
            from scripts.notion_qdrant_pipeline import run_pipeline
            stats = await asyncio.to_thread(run_pipeline)
            jr.set_note(str({k: v for k, v in stats.items() if isinstance(v, (int, bool))})[:400])
        logger.info("qdrant_pipeline_done", **{k: v for k, v in stats.items() if isinstance(v, (int, bool))})
    except Exception as e:
        logger.error("qdrant_pipeline_failed", error=str(e))


async def _extract_wiki_hourly():
    """Hourly cron: mine new Q/A pairs from the last 75 minutes into knowledge_wiki.

    75 min window gives a 15-minute safety overlap with the previous run so no
    pair is missed if a batch runs long. The extractor already skips pairs
    that already have wiki rows.
    """
    from app.core.self_check import track_job
    try:
        with track_job("wiki_extract_hourly") as jr:
            from app.knowledge.wiki_extractor import extract_batch
            result = await extract_batch(since_minutes=75, limit=200, max_concurrent=4)
            # 처리할 게 없어도 "돌았다"는 기록은 남는다 — 한산함과 고장을 구분하기 위함
            jr.set_note(str(result)[:400])
        logger.info("wiki_hourly_extract_done", **result)
    except Exception as e:
        logger.error("wiki_hourly_extract_failed", error=str(e))


async def _quality_snapshot_job():
    """Daily 00:05: compute quality snapshot for yesterday."""
    from app.core.self_check import track_job
    try:
        with track_job("quality_snapshot_daily") as jr:
            from app.core.quality_monitor import compute_daily_snapshot
            result = await asyncio.to_thread(compute_daily_snapshot)
            jr.set_note(f"{result.get('date')} · flags {len(result.get('flags', []))}")
        logger.info("quality_snapshot_done", date=result.get("date"), flags=len(result.get("flags", [])))
    except Exception as e:
        logger.error("quality_snapshot_failed", error=str(e))


async def _ingredient_sync_job():
    """매일 04:00: 제품 전성분 스프레드시트 → MariaDB 적재.

    성분 판정을 제품명 문자열 매칭으로 하다가 오답이 났던 건(노션 AI Tester)을
    실제 전성분 데이터로 답하기 위한 적재다. Sheets + BigQuery 를 호출한다.
    """
    from app.core.self_check import track_job
    try:
        with track_job("ingredient_sync_daily") as jr:
            from app.core.ingredients import sync_ingredients
            stats = await asyncio.to_thread(sync_ingredients)
            jr.set_note(str(stats)[:400])
        logger.info("ingredient_sync_done", **stats)
    except Exception as e:
        logger.error("ingredient_sync_failed", error=str(e))


async def _knowledge_map_job():
    """매일 03:00: 지식맵 그래프 빌드.

    2026-08-05 APP 서버 크론에서 이관. 이 빌드는 app/knowledge_map/semantic.py 에서
    Gemini(Flash)를 호출하는데, APP 서버는 프록시에 Gemini 가 열려 있지 않아
    이관 이후 한 번도 성공하지 못하고 매일 실패하고 있었다. WAS 는 Gemini 가
    열려 있어 정상 완주한다(실측 51초).
    """
    from app.core.self_check import track_job
    try:
        with track_job("knowledge_map_build") as jr:
            from app.knowledge_map.builder import build
            stats = await build(force=True)
            jr.set_note(str({k: v for k, v in (stats or {}).items()
                             if isinstance(v, (int, float))})[:400])
        logger.info("knowledge_map_build_done", **{k: v for k, v in (stats or {}).items()
                                                   if isinstance(v, (int, float))})
    except Exception as e:
        logger.error("knowledge_map_build_failed", error=str(e))


async def _self_check_job():
    """매일 07:30: 시스템 건강성·데이터 무결성 자가 점검.

    새로 깨진 검사만 잔디로 알린다. 2026-08-04 AD 동기화가 6일간 조용히
    실패하고도 아무도 몰랐던 일을 다시 겪지 않기 위한 잡이다.
    """
    from app.core.self_check import run_self_check, track_job
    try:
        with track_job("self_check_daily") as jr:
            result = await asyncio.to_thread(run_self_check, True, True)
            jr.set_note(f"{result.get('passed')}/{result.get('total')} 통과")
        logger.info("self_check_done", passed=result.get("passed"), failed=result.get("failed"),
                    repaired=result.get("repaired"), newly_broken=result.get("newly_broken"))
    except Exception as e:
        logger.error("self_check_failed", error=str(e))


async def _weekly_growth_report_job():
    """Monday 00:10: compute and persist weekly growth report."""
    from app.core.self_check import track_job
    try:
        with track_job("weekly_growth_report") as jr:
            from app.core.growth_report import compute_weekly_growth
            result = await asyncio.to_thread(compute_weekly_growth)
            jr.set_note(str({k: v for k, v in result.items() if k != "quality_trend"})[:400])
        logger.info("weekly_growth_done", **{k: v for k, v in result.items() if k != "quality_trend"})
    except Exception as e:
        logger.error("weekly_growth_failed", error=str(e))


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
