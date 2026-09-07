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
from zoneinfo import ZoneInfo

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core import asset_version

from app.api.admin_api import admin_router
from app.api.admin_group_api import group_router, ad_router
from app.api.auth_api import auth_api_router
from app.api.auth_middleware import get_optional_user
from app.api.auth_routes import auth_router
from app.api.entra_routes import entra_router
from app.api.attachment_api import router as attachment_router
from app.api.coa_finder_api import router as coa_finder_router
from app.api.conversation_api import conversation_router, ensure_message_columns
from app.api.eval_api import eval_router
from app.api.face_search_routes import router as face_search_router
from app.api.harness_api import router as harness_router
from app.api.middleware import setup_middleware
from app.api.personal_briefing_api import router as personal_briefing_router
from app.api.personal_profile_api import router as personal_profile_router
from app.api.saved_questions_api import router as saved_questions_router
from app.api.survey_api import survey_router
from app.api.jandi_briefing_api import router as jandi_briefing_router
from app.api.reports_api import router as reports_router
from app.api.notifications_api import router as notifications_router
from app.api.sql_export_api import router as sql_export_router
from app.api.routes import router
from app.config import get_settings, validate_jwt_secret
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
    validate_jwt_secret(settings.jwt_secret_key)

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
            ensure_visitor_analytics_permission_column,
            ensure_must_change_password_column,
            ensure_user_visits_table,
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
            ensure_visitor_analytics_permission_column,
            ensure_must_change_password_column,
            ensure_user_visits_table,
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
        # 붐따 처리 상태 컬럼 — 없으면 "처리했다"를 남길 곳이 없어
        # "인입은 됐는데 처리가 안 된 건지"를 영영 답할 수 없다 (2026-08-14)
        from app.core.feedback_inbox import (
            apply_deployed_resolutions,
            ensure_feedback_status_columns,
        )
        await asyncio.to_thread(ensure_feedback_status_columns)
        # 수정 배포와 붐따 상태를 한 흐름으로 묶는다. 해결 목록에 명시된 신고만
        # 날짜까지 검증한 뒤 done 처리하며, 재기동 시에는 이미 닫힌 행을 건너뛴다.
        await asyncio.to_thread(apply_deployed_resolutions)
        # 만족도 설문 — 접속일수 10·50·100일차에 별점을 한 번 묻는다 (2026-09-02)
        from app.core.satisfaction import ensure_survey_table
        await asyncio.to_thread(ensure_survey_table)
        from app.core.schema_watch import ensure_schema_watch_table
        await asyncio.to_thread(ensure_schema_watch_table)
        from app.core.ad_media_watch import ensure_ad_media_table
        await asyncio.to_thread(ensure_ad_media_table)
        from app.core.password_reset import ensure_password_reset_table
        await asyncio.to_thread(ensure_password_reset_table)
        from app.core.password_reset_google import ensure_google_reset_tables
        await asyncio.to_thread(ensure_google_reset_tables)
        from app.core.entra_auth import ensure_entra_tables
        await asyncio.to_thread(ensure_entra_tables)
        from app.core.value_lists import ensure_value_cache_table
        await asyncio.to_thread(ensure_value_cache_table)
        from app.core.ingredients import ensure_ingredient_tables
        await asyncio.to_thread(ensure_ingredient_tables)
        # OP 재고 — 시트를 매일 04:10 에 적재해 **표 조회**로 답한다 (벡터 아님)
        from app.core.inventory import ensure_inventory_table
        await asyncio.to_thread(ensure_inventory_table)
        # 수상/랭킹 — 시트를 매일 04:40 에 적재해 표 조회로 답한다 (벡터 아님)
        from app.core.awards import ensure_awards_table
        await asyncio.to_thread(ensure_awards_table)
        from app.core.term_aliases import ensure_term_aliases_table
        await asyncio.to_thread(ensure_term_aliases_table)
        from app.core.usage_meter import ensure_usage_table
        await asyncio.to_thread(ensure_usage_table)
        from app.core.golden_runner import ensure_golden_tables
        await asyncio.to_thread(ensure_golden_tables)
        from app.core.model_rights import ensure_model_rights_tables
        await asyncio.to_thread(ensure_model_rights_tables)
        from app.reports.store import ensure_report_tables
        await asyncio.to_thread(ensure_report_tables)
        from app.core.google_oauth_state import ensure_oauth_state_table
        await asyncio.to_thread(ensure_oauth_state_table)
        from app.core.query_profile import ensure_tables as ensure_query_profile_tables
        await asyncio.to_thread(ensure_query_profile_tables)
        from app.core.personal_briefing_store import ensure_tables as ensure_personal_briefing_tables
        await asyncio.to_thread(ensure_personal_briefing_tables)
        from app.core.jandi_briefing import ensure_tables as ensure_jandi_tables
        await asyncio.to_thread(ensure_jandi_tables)
        from app.core.saved_questions import ensure_tables as ensure_saved_question_tables
        await asyncio.to_thread(ensure_saved_question_tables)
        from app.core.fx_rates import ensure_tables as ensure_fx_tables
        await asyncio.to_thread(ensure_fx_tables)
        await asyncio.to_thread(ensure_message_columns)
        from app.core.announcements import ensure_tables as _ensure_announce
        await asyncio.to_thread(_ensure_announce)
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
            # 정의서 → BigQuery 컬럼 설명 (지식맵 03:00 뒤, 전성분 04:00 앞)
            # 질문 프로필 — 어제까지의 질문을 훑어 "내가 자주 묻는 것" 을 다시 만든다.
            # ⚠️ 파생이라 언제든 다시 만들 수 있다. 원본(`audit_logs`)은 건드리지 않는다.
            _scheduler.add_job(_query_profile_job, "cron", hour=3, minute=20,
                               id="query_profile_daily")
            _scheduler.add_job(_schema_docs_job, "cron", hour=3, minute=40, id="schema_docs_daily")
            _scheduler.add_job(_value_lists_job, "cron", hour=3, minute=50, id="value_lists_daily")
            # ⚠️ 적재가 **하루 두 번**이라 스냅샷도 두 번 뜬다 (2026-08-31 데이터팀 확인).
            #    한 번만 뜨면 오후 적재 실패를 다음 날 아침에야 안다 — 실제 사고가
            #    오후 적재 오류였다. 두 번 연속 안 보일 때만 유실로 확정하므로
            #    적재 도중에 찍혀도 오탐이 되지 않는다.
            _scheduler.add_job(_ad_media_snapshot_job, "cron", hour="4,18", minute=20,
                               id="ad_media_snapshot_daily")
            # ⚠️ 04:00 에 실패하면 **다음 시도가 24시간 뒤**라 성분 데이터가 하루 낡는다.
            #    실제로 2026-08-26·27 이틀 연속 구글 쪽 장애로 건너뛰었다. 06:30 에
            #    한 번 더 걸되, 그날 이미 성공했으면 잡 안에서 건너뛴다.
            _scheduler.add_job(_ingredient_sync_job, "cron", hour="4,6", minute=0,
                               id="ingredient_sync_daily")
            # ⛔ 시트는 **오전 10시경**에 갱신되고 안내문엔 "오후 2시 전후" 라고 적혀 있다.
            #    처음에 04:10 에 걸었다가 **매일 전날 데이터를 읽고 있었다** (2026-08-25).
            #    갱신 이후로 옮기고, 오후 갱신분까지 잡도록 하루 두 번 돈다.
            _scheduler.add_job(_op_inventory_sync_job, "cron", hour="11,16", minute=20, id="op_inventory_sync_daily")
            _scheduler.add_job(_awards_sync_job, "cron", hour=4, minute=40, id="awards_sync_daily")
            # CS/BP 제품 Q&A 시트 — ⛔ 기동 시 한 번만 읽던 것을 매시 갱신으로 바꿨다
            #    (2026-09-03). 없으면 시트를 고쳐도 재기동 전까지 반영되지 않는다
            _scheduler.add_job(_cs_cache_job, "cron", minute=40, id="cs_cache_hourly")
            _scheduler.add_job(_self_check_job, "cron", hour=7, minute=30, id="self_check_daily")
            # 골든셋 회귀 — 자가 점검(07:30)이 결과를 보게 그 전에 돈다. 일요일은 전체 런.
            _scheduler.add_job(_golden_job, "cron", hour=5, minute=30, id="golden_daily")
            _scheduler.add_job(_model_rights_job, "cron", hour=4, minute=30, id="model_rights_sync_daily")
            # 붐따 처리함 — 자가 점검(07:30) 뒤에 둔다. 밤새 들어온 것을 아침에 올린다
            _scheduler.add_job(_feedback_digest_job, "cron", hour=8, minute=0, id="feedback_digest_daily")
            _scheduler.add_job(_briefing_job, "cron", hour=8, minute=20, id="briefing_daily")
            # ⚠️ **잔디 발송 시각(08:00)보다 먼저 끝나야 한다** (2026-08-31 사용자 요청).
            #    DB_PC 릴레이는 만들어 둔 것을 가져갈 뿐이라, 생성이 늦으면 그날 몫이
            #    통째로 다음 회차(08:30)로 밀린다. 63명 × 동시 3 이면 2~3분이지만
            #    저장한 질문 실행이 앞에 붙으므로 30분을 비워 둔다.
            _scheduler.add_job(_personal_briefing_job, "cron", hour=7, minute=30,
                               id="personal_briefing_daily", timezone=ZoneInfo("Asia/Seoul"))
            # 셀라 알림 → 잔디 대기열. ⚠️ 근무 시간에만 돈다 — 밤에 밀어 넣어 봐야
            #    릴레이가 아침에나 보내고, 그 사이 읽음 처리되면 헛수고다.
            #    ⚠️ 릴레이 첫 회차(08:00)와 맞물리게 8시부터 돈다.
            _scheduler.add_job(_jandi_notify_job, "cron", day_of_week="mon-fri",
                               hour="8-18", minute=25, id="jandi_notify_hourly",
                               timezone=ZoneInfo("Asia/Seoul"))
            # AD sync is handled exclusively by the APP server crontab (22:00).
            # Removed from APScheduler to prevent concurrent dual-trigger race condition.
            _scheduler.start()
            _set_scheduler(_scheduler)
        logger.info("scheduler_started", jobs=["team_sync_daily_01:00", "wiki_extract_hourly_:15", "qdrant_pipeline_05:00", "quality_snapshot_00:05", "weekly_growth_mon_00:10", "knowledge_map_03:00", "ingredient_sync_04:00", "golden_05:30", "self_check_07:30", "personal_briefing_09:00"])
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

    # --- 자산 캐시 지문 (2026-08-31) ---
    # 손으로 세던 `?v=` 번호를 **내용 해시**로 갈아 끼운다. 한 곳에서 하는 이유는
    # 화면이 여럿(`/`·`/login`·`/coa-finder`·`/dashboard`…)이고, 새 화면을 붙인
    # 사람이 여기 넣는 것을 잊으면 그 화면만 조용히 캐시가 안 되기 때문이다.
    # ⚠️ HTML 만 건드린다 — SSE(`text/event-stream`)·JSON 은 그대로 흘려보낸다.
    @app.middleware("http")
    async def _stamp_assets(request: Request, call_next):
        response = await call_next(request)
        if "text/html" not in response.headers.get("content-type", ""):
            return response
        from starlette.responses import Response as _Response
        body = b"".join([chunk async for chunk in response.body_iterator])
        try:
            stamped = asset_version.rewrite_html(body.decode("utf-8")).encode("utf-8")
        except Exception:  # noqa: BLE001 - 못 고치면 원본을 그대로 내보낸다
            stamped = body
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return _Response(content=stamped, status_code=response.status_code,
                         headers=headers, media_type=response.media_type)

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
    app.include_router(entra_router)     # /auth/entra/*  (설정 없으면 503)
    app.include_router(auth_api_router)  # /api/auth/*
    app.include_router(personal_briefing_router)  # /api/personal-briefing/*
    app.include_router(personal_profile_router)   # /api/personal/suggestions
    app.include_router(saved_questions_router)  # /api/saved-questions/*
    app.include_router(survey_router)         # /api/survey — 만족도 설문
    app.include_router(jandi_briefing_router)  # /api/personal-briefing/jandi, /api/internal/*
    app.include_router(conversation_router)  # /api/conversations/*
    app.include_router(admin_router)         # /api/admin/*
    app.include_router(group_router)         # /api/admin/groups/*
    app.include_router(ad_router)            # /api/admin/ad/*
    app.include_router(eval_router)          # /api/admin/eval/*
    app.include_router(harness_router)       # /harness, /api/harness/*
    app.include_router(face_search_router)   # /face-search, /face-search/query, /face-search/thumb/*
    app.include_router(coa_finder_router)     # /coa-finder, /api/coa-finder/*
    app.include_router(attachment_router)     # /api/attachments/table — 엑셀·CSV → 표 텍스트
    app.include_router(reports_router)        # /api/reports/* — 본인이 만든 보고서만 열람
    app.include_router(notifications_router)   # /api/notifications/*
    app.include_router(sql_export_router)      # /api/sql-results/*csv — 본인이 조회한 결과만 다운로드

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

    class VersionedStaticFiles(StaticFiles):
        """`?v=<지금 파일의 지문>` 으로 들어온 요청만 1년 캐시한다.

        ⛔ 지문이 안 맞으면 예전처럼 `no-store` 다. 낡은 URL 은 굳지 않으므로
           **"영영 낡은 파일에 갇히는"** 경로가 구조적으로 없다 (asset_version 참조).
        """

        async def __call__(self, scope, receive, send):
            # ⚠️ Starlette 1.x 의 `scope["path"]` 는 **이미 전체 경로**이고
            #    `root_path` 에도 마운트 접두가 들어 있다 — 이어 붙이면
            #    `/frontend/frontend/chat.js` 가 돼 파일을 못 찾고, 그때 나는 것은
            #    에러가 아니라 **캐시가 조용히 안 켜지는 것**이다 (실측 후 수정).
            url_path = asset_version.normalize_path(
                scope.get("path", ""), scope.get("root_path", ""))
            version = asset_version.parse_version(
                scope.get("query_string", b"").decode("latin-1"))
            fresh = asset_version.matches(url_path, version)
            value = (b"public, max-age=31536000, immutable" if fresh
                     else b"no-store, no-cache, must-revalidate, max-age=0")

            async def _send(msg):
                if msg.get("type") == "http.response.start":
                    headers = [h for h in msg.get("headers", [])
                               if h[0].lower() != b"cache-control"]
                    headers.append([b"cache-control", value])
                    msg["headers"] = headers
                await send(msg)
            await super().__call__(scope, receive, _send)

    app.mount("/frontend", VersionedStaticFiles(directory=str(_FRONTEND_DIR)), name="frontend")
    app.mount("/static", VersionedStaticFiles(directory=str(_STATIC_DIR)), name="static")

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
    """Daily 01:00: Notion DB-HUB → MariaDB `team_resources`.

    ⛔ **이 잡을 지우지 마라.** 예전에는 팀 자료 전용 에이전트가 이 표를 썼는데 그
       경로가 죽어 있어 2026-08-25 에 걷어냈다. 표는 남겼다 — 05:00 벡터 파이프라인의
       **링크 카드**(`app/core/team_link_index.py`)가 이 표를 먹고 산다. 여기가 멈추면
       시트·드라이브 링크가 색인에서 조용히 낡는다.
    """
    from app.core.self_check import track_job
    try:
        import asyncio
        with track_job("team_sync_daily") as jr:
            from scripts.sync_team_resources import sync
            count = await asyncio.to_thread(sync, dry_run=False)
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


def _ingredients_loaded_today() -> bool:
    """오늘 성분 적재가 이미 성공했는가 (`job_runs` 기준).

    ⚠️ 테이블 행 수로 판정하지 않는다 — 어제 것이 남아 있으면 오늘 성공한 것처럼
       보인다. "할 일이 없어서 안 돈 것" 과 "죽어서 못 돈 것" 을 실행 기록으로
       가르는 것이 이 프로젝트의 규칙이다.
    """
    try:
        from app.db.mariadb import fetch_one

        row = fetch_one(
            "SELECT COUNT(*) n FROM job_runs WHERE job_id = %s AND ok = 1 "
            "AND DATE(started_at) = CURDATE()",
            ("ingredient_sync_daily",),
        ) or {}
        return int(row.get("n") or 0) > 0
    except Exception as e:
        # ⚠️ 판정을 못 하면 **돌린다.** 건너뛰는 쪽으로 실패하면 그날이 빈다
        logger.warning("ingredient_sync_guard_failed", error=str(e)[:160])
        return False


async def _query_profile_job():
    """매일 03:20: 질문 이력 → 사용자별 "자주 묻는 것" 프로필.

    ⛔ LLM 을 쓰지 않는다 — 화면의 제안은 사람이 그대로 누르므로, 왜 떴는지
       설명할 수 있어야 한다 (`app/core/query_profile.py` 머리말 참조).
    """
    from app.core.self_check import track_job

    try:
        with track_job("query_profile_daily") as jr:
            from app.core.query_profile import rebuild_all
            stats = await asyncio.to_thread(rebuild_all)
            jr.set_note(str(stats)[:200])
        logger.info("query_profile_done", **stats)
    except Exception as e:
        logger.error("query_profile_failed", error=str(e))


async def _cs_cache_job():
    """매시 :40 — CS/BP 제품 Q&A 스프레드시트를 다시 읽는다.

    ⛔ **없어서 조용히 낡아 있었다** (2026-09-03 사용자 제보). `warmup()` 이
       서버 기동 시에만 불렸고 TTL 도 없어, 시트를 고쳐도 **재기동 전까지**
       옛 답을 자신 있게 내놨다. 실측: 시트 최종수정 09-03 09:41 /
       그 직전 재기동 **09-02 16:18** — 그 사이 편집은 전부 안 보였다.
    ⚠️ 매시로 둔 이유는 호출이 싸기 때문이다 (탭 목록 1회 + batchGet 1회).
       하루 두 번으로 줄이면 오전에 고친 문구가 오후까지 안 보인다.
    """
    from app.agents.cs_agent import refresh
    from app.core.self_check import track_job

    try:
        with track_job("cs_cache_hourly") as jr:
            n = await refresh()
            # ⚠️ `refresh()` 는 실패해도 **옛 캐시를 지키고** -1 을 준다.
            #    그것을 성공으로 기록하면 자가 점검이 영영 못 잡는다.
            if n < 0:
                raise RuntimeError("cs 시트 재로딩 실패 — 옛 캐시 유지")
            # ⛔ `jr` 는 딕셔너리가 아니다 — `_JobRun` 이고 `set_note()` 만 받는다.
            #    첨자 대입으로 쓰면 TypeError 가 나는데, 그 예외가 track_job
            #    안에서 발생하므로 **갱신은 성공했는데 잡이 매시 '실패'로 기록**됐다
            #    (⚠️ 그 잘못된 호출 모양을 여기 적지 마라 — 회귀가 소스를 글자로 훑어
            #     스스로 걸린다. 실제로 이 주석을 처음 쓸 때 그렇게 걸렸다)
            #    (2026-09-07 발견). 기능은 멀쩡하고 기록만 거짓이라 아무도 못 봤다 —
            #    배치 건강성을 `job_runs` 로 판정하는 자가 점검이 이 잡에 대해서만
            #    신호를 잃은 상태였다. 회귀가 `tests/test_no_silent_failures.py` 에 있다.
            jr.set_note(f"CS Q&A {n}건")
        logger.info("cs_cache_job_done", qa_count=n)
    except Exception as e:
        logger.error("cs_cache_job_failed", error=str(e)[:200])


async def _ingredient_sync_job():
    """매일 04:00: 제품 전성분 스프레드시트 → MariaDB 적재.

    성분 판정을 제품명 문자열 매칭으로 하다가 오답이 났던 건(노션 AI Tester)을
    실제 전성분 데이터로 답하기 위한 적재다. Sheets + BigQuery 를 호출한다.
    """
    from app.core.self_check import track_job

    # ⛔ 오늘 이미 성공했으면 돌지 않는다. 두 번째 실행은 **실패를 메우려는 것**이지
    #    두 번 적재하려는 것이 아니다 — 성공한 날까지 매번 구글을 두 번 부르면
    #    없던 실패를 만들 여지만 늘린다.
    if await asyncio.to_thread(_ingredients_loaded_today):
        logger.info("ingredient_sync_skipped", reason="already_ok_today")
        return
    try:
        with track_job("ingredient_sync_daily") as jr:
            from app.core.ingredients import sync_ingredients
            stats = await asyncio.to_thread(sync_ingredients)
            jr.set_note(str(stats)[:400])
        logger.info("ingredient_sync_done", **stats)
    except Exception as e:
        logger.error("ingredient_sync_failed", error=str(e))


async def _op_inventory_sync_job():
    """매일 04:10: OP 재고 시트 → MariaDB 적재.

    ⛔ 재고는 **벡터가 아니라 표**로 답한다 (2026-08-25 결정). 시트가 SKU × 창고
       수량이라 임베딩으로는 숫자를 지킬 수 없다 — `app/core/inventory.py` 머리말 참조.
    ⚠️ 적재가 멈추면 재고가 조용히 낡는다. 그래서 `EXPECTED_JOBS` 에 등록해
       자가 점검이 매일 실행 기록을 본다.
    """
    from app.core.self_check import track_job
    try:
        with track_job("op_inventory_sync_daily") as jr:
            from app.core.inventory import sync_inventory
            stats = await asyncio.to_thread(sync_inventory)
            jr.set_note(str({k: v for k, v in stats.items() if k != "locations"})[:400])
        logger.info("op_inventory_sync_done",
                    **{k: v for k, v in stats.items() if k != "locations"})
    except Exception as e:
        logger.error("op_inventory_sync_failed", error=str(e))


async def _awards_sync_job():
    """매일 04:40: 수상/랭킹 시트 → MariaDB 적재.

    ⛔ 재고와 같은 이유로 **벡터가 아니라 표**로 답한다 — 핵심이 숫자(랭킹)와
       판정(마케팅 활용 O/△/X)이라 임베딩으로는 지킬 수 없다 (`app/core/awards.py` 참조).
    ⚠️ 0행은 성공이 아니다 — 권한 만료·탭 이름 변경이 이렇게, 에러 없이 온다.
       성공으로 기록하면 자가 점검이 영영 못 잡는다.
    """
    from app.core.self_check import track_job
    try:
        with track_job("awards_sync_daily") as jr:
            from app.core.awards import sync_awards
            stat = await asyncio.to_thread(sync_awards)
            if stat.get("empty"):
                raise RuntimeError("수상/랭킹 시트가 0행이다 — 권한·탭 이름을 확인할 것")
            jr.set_note(str(stat)[:400])
        logger.info("awards_sync_done", **{k: v for k, v in stat.items() if k != "rows"})
    except Exception as e:
        logger.error("awards_sync_failed", error=str(e)[:200])


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


async def _model_rights_job():
    """매일 04:30: 모델 초상권 시트 → MariaDB 적재."""
    from app.core.self_check import track_job
    try:
        with track_job("model_rights_sync_daily") as jr:
            from app.core.model_rights import sync_model_rights
            stats = await asyncio.to_thread(sync_model_rights)
            jr.set_note(str(stats)[:200])
        logger.info("model_rights_sync_done", **stats)
    except Exception as e:
        logger.error("model_rights_sync_failed", error=str(e))


async def _golden_job():
    """매일 05:30: 골든셋 회귀 런 (일요일은 전체 문항).

    자가 점검(07:30)의 golden_regression 검사가 직전 런과 비교해
    새로 깨진 문항을 알린다 — 여기서는 실행·기록만 한다.
    """
    from app.core.self_check import track_job
    try:
        with track_job("golden_daily") as jr:
            from app.core.golden_runner import run_golden
            result = await asyncio.to_thread(run_golden, "scheduled", None)
            jr.set_note(f"{result.get('passed')}/{result.get('total')} 통과 ({result.get('scope')})")
        logger.info("golden_run_job_done", **{k: v for k, v in result.items() if k != "note"})
    except Exception as e:
        logger.error("golden_run_job_failed", error=str(e))


async def _feedback_digest_job():
    """매일 08:00: 새로 들어온 붐따(👎)를 읽어 처리 대상으로 올린다.

    ⛔ 이 잡이 생기기 전까지 **붐따 코멘트를 읽는 경로가 없었다.** 넉 달치 39건이
       쌓여 있었고, 그중 하나는 9일 뒤 개발자가 같은 증상을 직접 겪고서야 고쳐졌다.
       "매일 개선하는 시스템"(Nightly-Debug)은 서버 로그 에러를 봤지 붐따를 본
       적이 없다 (2026-08-14 확인).
    """
    from app.core.self_check import track_job
    try:
        with track_job("feedback_digest_daily") as jr:
            from app.core.feedback_inbox import run_daily_digest
            result = await asyncio.to_thread(run_daily_digest)
            jr.set_note(f"신규 {result['new']}건(코멘트 {result['new_with_comment']}) "
                        f"· 미처리 {result['open']}건")
        logger.info("feedback_digest_done", **result)
    except Exception as e:
        logger.error("feedback_digest_failed", error=str(e))


async def _briefing_job():
    """매일 08:20: 개인화 데일리 브리핑 — 사용자가 묻지 않아도 먼저 찾아간다.

    ⛔ 이 잡이 없으면 Cella 는 "궁금할 때만 찾아가는 도구" 로 남는다. 실측(2026-08-20):
       30일 활성 29명 중 **하루만 쓴 사람이 11명(38%)**. 열 이유를 매일 만드는 것이
       사용 빈도를 올리는 유일한 길이다.
    ⚠️ 변화가 없거나 어제와 같은 이야기면 **만들지 않는다** — 매일 뜨는 알림은 무시당한다.
    """
    from app.core.self_check import track_job
    try:
        with track_job("briefing_daily") as jr:
            from app.core.briefing import run_daily
            result = await asyncio.to_thread(run_daily)
            from app.core.briefing import effect_stats
            eff = await asyncio.to_thread(effect_stats)
            jr.set_note(f"기준일 {result.get('base')} · 생성 {result.get('made')}건 "
                        f"· 변화없음 {result.get('skipped')}건 · 메일 {result.get('mailed')}건 "
                        f"| 최근7일 열람 {eff['seen_pct']}% · 같은날 질문 {eff['conversion_pct']}%")
        logger.info("briefing_done", **result)
    except Exception as e:
        logger.error("briefing_failed", error=str(e))


async def _personal_briefing_job():
    """매일 09:00 KST: 출근 브리핑을 만들고 잔디 발송 대기열에 넣는다."""

    from app.core.self_check import track_job

    try:
        with track_job("personal_briefing_daily") as jr:
            if not get_settings().personal_briefing_enabled:
                jr.set_note("feature disabled")
                return
            # ⛔ 답을 먼저 갱신해야 바로 뒤에서 만드는 오늘 브리핑에 같은 날 결과가 실린다.
            try:
                with track_job("saved_questions_daily") as saved_jr:
                    from app.core.saved_questions import run_saved_questions

                    saved_result = await run_saved_questions()
                    saved_jr.set_note(
                        f"selected={saved_result['selected']} "
                        f"succeeded={saved_result['succeeded']} "
                        f"failed={saved_result['failed']} "
                        f"empty={saved_result.get('empty', 0)}"
                    )
            except Exception as exc:
                # ⚠️ 저장 질문 잡 전체가 실패해도 기존 일정·메일 브리핑은 계속 만든다.
                logger.error("saved_questions_daily_failed", error_type=type(exc).__name__)
            from app.core.personal_briefing import run_morning_precompute

            result = await run_morning_precompute()
            jr.set_note(
                f"selected={result['selected']} succeeded={result['succeeded']} "
                f"failed={result['failed']} queued={result.get('queued', 0)}"
            )
        logger.info(
            "personal_briefing_precompute_done",
            selected=result["selected"],
            succeeded=result["succeeded"],
            failed=result["failed"],
        )
    except Exception as exc:
        logger.error("personal_briefing_precompute_failed", error_type=type(exc).__name__)


async def _jandi_notify_job():
    """셀라 알림을 잔디 대기열에 넣는다 (발송은 DB_PC 릴레이가 한다).

    ⛔ 서버는 `wh.jandi.com` 에 붙지 못한다 — 만들어 두기만 한다.
    """
    from app.core.self_check import track_job

    try:
        with track_job("jandi_notify_hourly") as jr:
            from app.core.jandi_notify import run

            result = await asyncio.to_thread(run)
            jr.set_note(f"recipients={result['recipients']} queued={result['queued']}")
    except Exception as exc:
        logger.error("jandi_notify_failed", error_type=type(exc).__name__)


async def _schema_docs_job():
    """매일 03:40: 노션 BigQuery 정의서 → BigQuery 컬럼 설명.

    ⛔ 컬럼의 **뜻**이 앱에 닿을 경로가 없어서 만든 잡이다. 앱은 컬럼 이름과 타입만
       봤고, 뜻은 프롬프트에 사람이 손으로 적어야 했다 — 그건 반드시 낡는다.
       실제로 `Store_Review.shopname` 이 매장명인 줄 몰라 `channel` 로 찾아
       "뉴욕 플래그십 0건"(실제 95건)이라고 답했다 (이주훈 님 제보 2026-08-14).
    ⚠️ 지식맵 빌드(03:00) 뒤, 전성분 적재(04:00) 앞에 둔다.
    """
    from app.core.self_check import track_job
    try:
        with track_job("schema_docs_daily") as jr:
            from app.core.schema_docs import sync
            stats = await asyncio.to_thread(sync)
            jr.set_note(f"{stats['tables_matched']}개 테이블 · {stats['updated']}컬럼 갱신")
        logger.info("schema_docs_job_done", **{k: v for k, v in stats.items() if k != "changed"})
    except Exception as e:
        logger.error("schema_docs_job_failed", error=str(e))


async def _value_lists_job():
    """매일 03:50: 컬럼 DISTINCT 값 목록 실측 → 캐시.

    ⛔ 프롬프트에 값 목록을 손으로 적으면 반드시 낡는다. 실제로 Continent1 의
       `남미`·`중미` 가 `중남미` 로 통합됐는데 프롬프트만 옛 값이었다 (2026-08-18).
    ⚠️ 질문 경로에서는 캐시만 읽는다 — 여기서만 BigQuery 를 부른다.
    """
    from app.core.self_check import track_job
    try:
        with track_job("value_lists_daily") as jr:
            from app.core.value_lists import refresh
            stats = await asyncio.to_thread(refresh)
            jr.set_note(f"{len(stats)}개 목록 갱신")
        logger.info("value_lists_job_done", **stats)
        # ⛔ 대표 제품 목록도 **실측**이다 (2026-09-03). 손으로 적어 두었다가
        #    36만 개 팔린 센텔라 테카 앰플을 "없는 제품" 이라고 답한 사고가 있었다.
        try:
            from app.core.product_catalog import refresh as _catalog_refresh
            logger.info("product_catalog_job_done", products=_catalog_refresh())
        except Exception as _e:
            logger.error("product_catalog_job_failed", error=str(_e)[:200])
    except Exception as e:
        logger.error("value_lists_job_failed", error=str(e))


async def _ad_media_snapshot_job():
    """매일 04:20: 광고 매체 목록 스냅샷 → 사라진 매체 감지.

    ⛔ 2026-08-31 에 `KakaoMoments` 가 하루 사이 통째로 사라졌다 (오전 최신
       8/27·594,843원 → 오후 매체 19종 어디에도 없음). 스키마는 그대로였고
       전체 행 수도 임계에 못 미쳐 **기존 감시 어느 것도 못 잡았다.**
    ⚠️ 적재가 끝난 뒤에 떠야 한다 — 적재 중에 뜨면 정상 재적재를 유실로 읽는다.
    """
    from app.core.self_check import track_job
    try:
        with track_job("ad_media_snapshot_daily") as jr:
            from app.core.ad_media_watch import run, summarize
            result = await asyncio.to_thread(run)
            ok, detail = summarize(result)
            jr.set_note(detail[:400])
        logger.info("ad_media_snapshot_done", ok=ok, detail=detail[:200])
    except Exception as e:
        logger.error("ad_media_snapshot_failed", error=str(e))


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
