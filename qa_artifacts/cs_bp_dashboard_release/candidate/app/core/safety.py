"""Safety module: MaintenanceManager + CircuitBreaker.

MaintenanceManager:
  - Manual toggle: activate(reason) / deactivate()
  - Auto-detect: 60s polling of __TABLES__ row_count metadata (free query)
  - Baseline comparison: >50% drop -> ON, >90% recovery -> OFF

CircuitBreaker (per-service):
  - States: CLOSED -> OPEN -> HALF_OPEN -> CLOSED
  - 3 consecutive failures -> OPEN (block calls for 60s)
  - After cooldown, one trial call (HALF_OPEN)
  - Success -> CLOSED, failure -> OPEN again
"""

import asyncio
import json
import re
import time
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# MaintenanceManager
# ---------------------------------------------------------------------------

#: 폴링으로 읽은 수정 시각이 이만큼 넘게 앞으로 갔을 때만 **새 쓰기**로 본다.
#: ⚠️ 0 으로 두지 마라 — 경과 초는 정수로 오고 폴링 간격·왕복 지연이 매번 다르므로,
#:    가만히 있는 테이블도 1~2초씩 흔들려 **매 폴링이 '방금 썼다'** 가 된다.
_LOAD_MOVE_EPSILON_SECONDS = 10


class MaintenanceManager:
    """Tracks BigQuery table update state and blocks queries during maintenance.

    `active`/`reason` are an aggregate view (true if manually activated, or if
    any monitored table is currently auto-detected as updating) kept for callers
    that only care about "is something updating" (e.g. the inline answer warning).
    Per-table state lives in `tables` and is what the System Status UI reads, so
    one table updating no longer marks every other table as "업데이트 중".
    """

    def __init__(self) -> None:
        self.active: bool = False
        self.reason: str = ""
        self.manual: bool = False  # True if activated manually (won't auto-deactivate)
        self.tables: Dict[str, dict] = {}  # table_key -> {"active", "reason", "baseline_rows"}
        self._last_check: float = 0.0

    def activate(self, reason: str = "수동 점검모드") -> None:
        """Manually activate maintenance mode (applies to all services)."""
        self.active = True
        self.reason = reason
        self.manual = True
        logger.warning("maintenance_activated", reason=reason, manual=True)

    def deactivate(self) -> None:
        """Deactivate manual maintenance mode; falls back to per-table auto state."""
        was_active = self.active
        self.manual = False
        self._recompute_aggregate()
        if was_active:
            logger.info("maintenance_deactivated")

    def _table_entry(self, table_key: str) -> dict:
        return self.tables.setdefault(
            table_key,
            {"active": False, "reason": "", "baseline_rows": None,
             "modified_ts": None, "moved_ts": None})

    def note_table_modified(self, table_key: str, modified_ago_seconds) -> None:
        """테이블이 마지막으로 **수정된 시각**을 절대값으로 기록한다.

        ⛔ 경과 초를 그대로 저장하면 안 된다 — 감시 루프가 멈추면 그 값이 얼어붙어
           **영원히 "방금 적재됨"** 이 된다. 절대 시각으로 두면 루프가 죽어도
           경과가 자연히 커져 공시가 스스로 꺼진다 (안전한 쪽으로 실패한다).

        ⛔ **"최근에 수정됐다" 와 "지금 쓰고 있다" 는 다른 사실이다.** 수정 시각만
           보면 적재가 **끝난 뒤에도** 창이 닫힐 때까지 계속 "적재 중" 이라고
           말한다 — 실제로 11분 전에 끝난 적재를 두고 그렇게 나갔다
           (2026-08-31 사용자 제보: *"지금 적재중입니다는 맞는 표현이 아님.
           이미 업데이트가 되어있음"*). 쓰는 중이면 폴링마다 수정 시각이 **앞으로
           간다**. 그래서 값이 실제로 움직인 순간(`moved_ts`)을 따로 남기고,
           공시는 그것만 근거로 삼는다.
        """
        if modified_ago_seconds is None:
            return
        entry = self._table_entry(table_key)
        stamp = time.time() - float(modified_ago_seconds)
        previous = entry.get("modified_ts")
        if previous is not None and stamp > previous + _LOAD_MOVE_EPSILON_SECONDS:
            entry["moved_ts"] = time.time()
        entry["modified_ts"] = stamp

    def table_modified_ago(self, table_key: str):
        """마지막 수정 이후 경과(초). 모르면 None."""
        ts = self.tables.get(table_key, {}).get("modified_ts")
        return None if ts is None else max(0.0, time.time() - ts)

    def table_load_moved_ago(self, table_key: str):
        """수정 시각이 **실제로 앞으로 간 것을 본** 이후 경과(초). 못 봤으면 None."""
        ts = self.tables.get(table_key, {}).get("moved_ts")
        return None if ts is None else max(0.0, time.time() - ts)

    def _recompute_aggregate(self) -> None:
        active_tables = [k for k, v in self.tables.items() if v.get("active")]
        self.active = bool(active_tables)
        self.reason = self.tables[active_tables[0]]["reason"] if active_tables else ""

    def auto_activate_table(self, table_key: str, reason: str) -> None:
        """Auto-activate a single table from row count monitoring (won't override manual)."""
        if self.manual:
            return  # Don't touch manual mode
        entry = self._table_entry(table_key)
        was_active = entry["active"]
        entry["active"] = True
        entry["reason"] = reason
        if not was_active:
            logger.warning("maintenance_auto_activated", table=table_key, reason=reason)
        self._recompute_aggregate()

    def auto_deactivate_table(self, table_key: str) -> None:
        """Auto-deactivate a single table when its row count recovers (skips if manual)."""
        if self.manual:
            return
        entry = self.tables.get(table_key)
        if entry and entry["active"]:
            entry["active"] = False
            entry["reason"] = ""
            logger.info("maintenance_auto_deactivated", table=table_key)
        self._recompute_aggregate()

    def is_table_active(self, table_key: str) -> bool:
        return self.tables.get(table_key, {}).get("active", False)

    def table_reason(self, table_key: str) -> str:
        return self.tables.get(table_key, {}).get("reason", "")

    def table_baseline(self, table_key: str) -> Optional[int]:
        return self.tables.get(table_key, {}).get("baseline_rows")

    def set_table_baseline(self, table_key: str, rows: int) -> None:
        self._table_entry(table_key)["baseline_rows"] = rows

    @property
    def status(self) -> dict:
        return {
            "active": self.active,
            "reason": self.reason,
            "manual": self.manual,
        }


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-service circuit breaker with failure threshold and cooldown."""

    def __init__(self, name: str, failure_threshold: int = 3, cooldown_seconds: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.state: CBState = CBState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0

    def is_available(self) -> bool:
        """Check if the service is available for calls."""
        if self.state == CBState.CLOSED:
            return True
        if self.state == CBState.OPEN:
            # Check if cooldown has elapsed
            if time.time() - self._last_failure_time >= self.cooldown_seconds:
                self.state = CBState.HALF_OPEN
                logger.info("circuit_half_open", service=self.name)
                return True  # Allow one trial call
            return False
        # HALF_OPEN: allow one call
        return True

    def record_success(self) -> None:
        """Record a successful call — reset to CLOSED."""
        if self.state != CBState.CLOSED:
            logger.info("circuit_closed", service=self.name, prev_state=self.state.value)
        self.state = CBState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call — increment counter, maybe open circuit."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self.state == CBState.HALF_OPEN:
            # Trial call failed — back to OPEN
            self.state = CBState.OPEN
            logger.warning("circuit_reopened", service=self.name)
        elif self._failure_count >= self.failure_threshold:
            self.state = CBState.OPEN
            logger.warning("circuit_opened", service=self.name, failures=self._failure_count)

    @property
    def status_dict(self) -> dict:
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
        }


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# AnnouncementManager
# ---------------------------------------------------------------------------

class AnnouncementManager:
    """Stores a site-wide announcement banner message (file-backed, survives restart)."""

    _PATH = Path(__file__).resolve().parent.parent.parent / "data" / "announcement.json"

    def __init__(self) -> None:
        self.message: str = self._load()

    def _load(self) -> str:
        try:
            if self._PATH.exists():
                data = json.loads(self._PATH.read_text(encoding="utf-8"))
                return data.get("message", "")
        except Exception:
            pass
        return ""

    def _save(self) -> None:
        try:
            self._PATH.parent.mkdir(parents=True, exist_ok=True)
            self._PATH.write_text(
                json.dumps({"message": self.message}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("announcement_save_failed", error=str(e))

    def set(self, message: str) -> None:
        self.message = message.strip()
        self._save()
        logger.info("announcement_set", message=self.message[:80])

    def clear(self) -> None:
        self.message = ""
        self._save()
        logger.info("announcement_cleared")

    @property
    def active(self) -> bool:
        return bool(self.message)


# Module-level singletons
# ---------------------------------------------------------------------------

_maintenance_manager: Optional[MaintenanceManager] = None
_announcement_manager: Optional[AnnouncementManager] = None
_circuits: Dict[str, CircuitBreaker] = {}
_qdrant_cache: dict = {}
_qdrant_cache_time: float = 0


def get_maintenance_manager() -> MaintenanceManager:
    """Get or create the MaintenanceManager singleton."""
    global _maintenance_manager
    if _maintenance_manager is None:
        _maintenance_manager = MaintenanceManager()
    return _maintenance_manager


def get_announcement_manager() -> AnnouncementManager:
    """Get or create the AnnouncementManager singleton."""
    global _announcement_manager
    if _announcement_manager is None:
        _announcement_manager = AnnouncementManager()
    return _announcement_manager


def get_circuit(name: str) -> CircuitBreaker:
    """Get or create a CircuitBreaker for the given service name."""
    if name not in _circuits:
        _circuits[name] = CircuitBreaker(name)
    return _circuits[name]


def get_safety_status() -> dict:
    """Build full safety status for the /safety/status endpoint.

    Returns service-level status combining maintenance state,
    circuit breaker states, and subsystem health.
    """
    mm = get_maintenance_manager()

    # Build services map — clean names (no BQ/BigQuery prefix)
    services: Dict[str, dict] = {}

    # BigQuery-backed services: each checked against its OWN monitored table,
    # not a single shared flag — one table updating must not mark the rest.
    bq_circuit = get_circuit("bigquery")

    def _bq_service(label: str, detail: str) -> dict:
        if mm.manual:
            return {"status": "updating", "detail": detail, "reason": mm.reason}
        if bq_circuit.state != CBState.CLOSED:
            return {"status": "error", "detail": detail}
        if mm.is_table_active(label):
            return {"status": "updating", "detail": detail, "reason": mm.table_reason(label)}
        return {"status": "ok", "detail": detail}

    services["매출"] = _bq_service("매출", "SALES_ALL_Backup")
    services["제품"] = _bq_service("제품", "Product")

    _mkt_tables = {
        "광고": "통합 광고 데이터",
        "마케팅": "통합 마케팅 비용",
        "Shopify": "글로벌 자사몰 판매",
        "플랫폼": "플랫폼 순위/가격",
        "인플루언서": "인플루언서 마케팅",
        "아마존검색": "아마존 검색 분석",
        "메타광고": "메타 광고 라이브러리",
        "아마존 리뷰": "아마존 리뷰",
        "큐텐 리뷰": "큐텐 리뷰",
        "쇼피 리뷰": "쇼피 리뷰",
        "스마트스토어 리뷰": "스마트스토어 리뷰",
        "프로모션": "프로모션 캘린더 (실행 일정)",
        "물류": "수출 물류 — 발주·선적·출고 (Export_control)",
        "국내CS": "국내 CS 대시보드 · 처리 기록",
        "해외CS": "해외 CS 대시보드 · Shopify 환불 기록",
    }
    for label, detail in _mkt_tables.items():
        services[label] = _bq_service(label, detail)

    from app.core.cs_metrics import DASHBOARD_URL as _CS_DASHBOARD_URL
    for _cs_key in ("국내CS", "해외CS"):
        services[_cs_key]["url"] = _CS_DASHBOARD_URL
        services[_cs_key]["url_label"] = "대시보드"

    # 모델 초상권 (시트 → MariaDB, 매일 04:30 적재) — 적재 수·신선도로 판정
    # 원본 시트를 화면에 함께 걸어 둔다 — 앱이 판정 못 하는 건은 사람이 시트를 봐야 한다
    from app.core.model_rights import SHEET_URL as _RIGHTS_SHEET_URL
    try:
        from datetime import datetime as _dt

        from app.db.mariadb import fetch_one as _fetch_one
        _mr = _fetch_one("SELECT COUNT(*) c, MAX(synced_at) s FROM model_rights")
        if not _mr or not _mr.get("c"):
            services["초상권"] = {"status": "error", "detail": "모델 초상권 (미적재)",
                                  "url": _RIGHTS_SHEET_URL}
        else:
            _age_h = ((_dt.now() - _mr["s"]).total_seconds() / 3600) if _mr.get("s") else 999
            services["초상권"] = {
                "status": "ok" if _age_h <= 26 else "error",
                "detail": f"모델 초상권 — 모델 {_mr['c']}명"
                          + ("" if _age_h <= 26 else f" (적재 {int(_age_h)}시간 전)"),
                "url": _RIGHTS_SHEET_URL,
            }
    except Exception:
        services["초상권"] = {"status": "error", "detail": "모델 초상권 (조회 실패)",
                              "url": _RIGHTS_SHEET_URL}

    # Notion (Qdrant) — 팀별 분리 (5분 캐시)
    import time as _time
    _cs_docs_count = None
    _QDRANT_TEAM_LABELS = {
        "B2B1": "B2B1", "[GM]WEST": "GM WEST", "CS": "CS",
        "DB": "DB", "B2B2": "B2B2", "PEOPLE": "PEOPLE",
        "BCM": "BCM", "[GM]EAST": "GM EAST", "Craver": "Craver",
        "KBT": "KBT", "JBT": "JBT",
    }
    try:
        global _qdrant_cache, _qdrant_cache_time
        _now = _time.time()
        if not _qdrant_cache or _now - _qdrant_cache_time > 300:
            # Read from Qdrant Cloud Craver collection (5분 캐시)
            from qdrant_client import QdrantClient
            from app.agents.qdrant_agent import _qdrant_url, _qdrant_api_key
            _qclient = QdrantClient(
                url=_qdrant_url(),
                api_key=_qdrant_api_key(),
                timeout=5,
            )
            _team_counts: dict[str, int] = {}
            _offset = None
            while True:
                _result = _qclient.scroll("Craver", offset=_offset, limit=100, with_payload=True, with_vectors=False)
                for _pt in _result[0]:
                    _t = _pt.payload.get("team", "UNKNOWN")
                    _team_counts[_t] = _team_counts.get(_t, 0) + 1
                _offset = _result[1]
                if _offset is None:
                    break
            _qdrant_cache = _team_counts
            _qdrant_cache_time = _now
        else:
            _team_counts = _qdrant_cache

        _cs_docs_count = _team_counts.get("CS", 0)
        _SKIP_TEAMS = {"FI", "OP", "LOG", "IT", "CS", "UNKNOWN", "?", "google_sheets", "임베딩 된 구글시트"}
        for _qt, _qc in sorted(_team_counts.items(), key=lambda x: _QDRANT_TEAM_LABELS.get(x[0], x[0])):
            if _qt in _SKIP_TEAMS:
                continue
            _label = _QDRANT_TEAM_LABELS.get(_qt, _qt)
            services[_label] = {"status": "ok", "detail": f"{_qc} chunks"}
            if _qt == "PR":
                from app.core.pr_issues import SHEET_URL as _PR_SHEET_URL
                services[_label]["url"] = _PR_SHEET_URL

    except Exception as e:
        services["Notion"] = {"status": "error", "detail": str(e)[:30]}

    # BP는 제품 Q&A와 CS 문서를 함께 조회한다. 둘 중 한쪽이 없어도 정상으로 숨기지 않는다.
    _cs_docs_detail = ("CS 문서 연결" if _cs_docs_count else
                       "CS 문서 미적재" if _cs_docs_count == 0 else "CS 문서 확인 실패")
    services["BP"] = {"status": "error", "detail": "제품 Q&A 확인 실패 · " + _cs_docs_detail}
    try:
        from app.agents.cs_agent import source_links as _bp_source_links
        from app.agents.cs_agent import status as _bp_qa_status
        _links = _bp_source_links()
        services["BP"]["links"] = _links
        if _links:
            services["BP"]["url"] = _links[0]["url"]
        _qa = _bp_qa_status()
        _qa_ready = bool(_qa.get("loaded") and _qa.get("count"))
        services["BP"]["detail"] = (
            f"제품 Q&A {_qa.get('count') or 0}건" if _qa_ready else "제품 Q&A 준비 중"
        ) + " · " + _cs_docs_detail
        services["BP"]["status"] = "ok" if _qa_ready and _cs_docs_count else "error"
    except Exception:
        pass

    # OP 재고 (시트 → MariaDB, 매일 04:10) — 적재량과 **시트 갱신 시점**을 함께 낸다.
    # ⚠️ 매일 바뀌는 값이라 "언제 것인가" 가 숫자만큼 중요하다.
    # ⚠️ Qdrant 팀 목록(`_SKIP_TEAMS`)이 아니라 여기서 따로 낸다 — OP 는 벡터가 아니라
    #    표 조회다 (`route: "inventory"`).
    try:
        from app.core.inventory import SHEET_URL as _INV_URL
        from app.core.inventory import status as _inv_status
        _inv = _inv_status()
        if _inv.get("rows"):
            services["OP"] = {
                "status": "ok",
                # ⚠️ 상태 카드 한 줄은 좁다 — 길어지면 이름 칸을 밀어낸다
                #    (`OP` → `O`, 2026-08-26). 창고 수는 조회 답변에 늘 나오므로 뺀다
                "detail": "{:,} SKU · 유통기한 {:,}건".format(
                    _inv["skus"], _inv.get("expiry_rows") or 0),
                # ⚠️ 답변은 **조회 시점에 시트를 직접 읽는다.** 여기 숫자는 폴백용
                #    적재본이라, 둘을 같은 것으로 읽지 않게 밝혀 둔다
                "reason": ("재고 질문은 조회할 때마다 시트를 직접 읽습니다 "
                           "(아래는 시트를 못 읽을 때 쓰는 적재본 · 시트 기준 "
                           + (_inv.get("sheet_updated_at") or "시점 미상") + ")"),
                "url": _INV_URL,
            }
        else:
            services["OP"] = {"status": "updating", "detail": "적재 대기", "url": _INV_URL}
    except Exception as _e:
        services["OP"] = {"status": "error", "detail": str(_e)[:30]}

    # 수상/랭킹 (시트 → MariaDB, 매일 04:40 적재) — OP 재고와 같은 방식으로 낸다:
    # 적재 건수·구분별 분포·마지막 적재 시각. `app/core/awards.py` 는 읽기만 한다
    # (다른 세션이 그 파일을 작업 중 — CLAUDE.md 공유 작업트리 규칙).
    try:
        from app.core.awards import SHEET_URL as _AWD_URL
        from app.core.awards import status as _awd_status
        from app.db.mariadb import fetch_all as _fetch_all
        _awd = _awd_status()
        if _awd.get("count"):
            _cats = _fetch_all(
                "SELECT category, COUNT(*) c FROM awards_rankings "
                "GROUP BY category ORDER BY c DESC") or []
            _cat_str = " · ".join(
                f"{r['category'] or '미분류'} {r['c']}" for r in _cats)
            _synced = _awd.get("synced_at")
            _synced_str = _synced.strftime("%m-%d %H:%M") if _synced else "시점 미상"
            services["수상"] = {
                "status": "ok",
                "detail": "{:,}건 · {} (적재 {})".format(
                    _awd["count"], _cat_str, _synced_str),
                "url": _AWD_URL,
            }
        else:
            services["수상"] = {"status": "updating", "detail": "적재 대기", "url": _AWD_URL}
    except Exception as _e:
        services["수상"] = {"status": "error", "detail": str(_e)[:30]}

    # Google Workspace
    services["Google Workspace"] = {"status": "ok", "detail": "OAuth ready"}

    # 메일 발송 경로 — 열려 있는지 화면에서 바로 보이게 한다. 꺼져 있으면 '대기'
    try:
        from app.core import mailer as _mailer
        services["메일 발송"] = {
            "status": "ok" if _mailer.is_enabled() else "updating",
            "detail": _mailer.status(),
            "reason": "IT 에 SMTP 릴레이 개방을 요청한 상태입니다 (앱 알림은 정상)",
        }
    except Exception as _e:
        services["메일 발송"] = {"status": "error", "detail": str(_e)[:40]}

    # 보고서 — 데이터소스가 아니라 산출물이지만, **언제 만들어지는지**를 여기서 알린다.
    # 보고서는 조회 8~12회에 10~30초가 드는 특수 경로라 명시했을 때만 만든다
    # (2026-08-13 규칙). 그 조건을 사용자가 볼 수 있어야 "왜 안 만들어지지"가 없다.
    try:
        from app.reports import blocks as _rb
        services["보고서"] = {
            "status": "ok",
            "detail": (f"블록 {len(_rb.BLOCKS)}종 · @@보고서 또는 '보고서' 명시 시 생성 · "
                       "열람은 본인 + 지목해 공유한 사람"),
        }
    except Exception as e:
        services["보고서"] = {"status": "error", "detail": str(e)[:30]}

    # Gemini / Claude API — 내부 전용 (System Status에 노출하지 않음)

    # GWS Token (per-user OAuth)
    try:
        from app.core.google_auth import GoogleAuthManager
        mgr = GoogleAuthManager()
        token_dir = mgr.token_dir
        token_files = list(token_dir.glob("*.json"))
        services["GWS Token"] = {"status": "ok", "detail": f"{len(token_files)} users"}
    except Exception:
        services["GWS Token"] = {"status": "error", "detail": "unavailable"}

    # Circuits
    circuits = {name: cb.status_dict for name, cb in _circuits.items()}

    # Notion sync state (from routes module)
    notion_sync = {}
    try:
        from app.api.routes import _notion_sync_state
        notion_sync = _notion_sync_state
    except Exception:
        pass

    return {
        "maintenance": mm.status,
        "services": services,
        "circuits": circuits,
        "notion_sync": notion_sync,
    }


# ---------------------------------------------------------------------------
# Auto-detect background loop
# ---------------------------------------------------------------------------

_UPDATE_WINDOW_SECONDS = 180  # 3 minutes: table modified within this window → "updating"

# service label -> (dataset, table_id). One entry per System Status BigQuery service —
# each is tracked independently so one table updating doesn't flag the rest.
_MONITORED_TABLES: Dict[str, tuple] = {
    "매출": ("Sales_Integration", "SALES_ALL_Backup"),
    "제품": ("Sales_Integration", "Product"),
    "광고": ("marketing_analysis", "integrated_ad"),
    "마케팅": ("marketing_analysis", "Integrated_marketing_cost"),
    "Shopify": ("marketing_analysis", "shopify_analysis_sales"),
    "인플루언서": ("marketing_analysis", "influencer_input_ALL_TEAMS"),
    "아마존검색": ("marketing_analysis", "amazon_search_analytics_catalog_performance"),
    "플랫폼": ("Platform_Data", "raw_data"),
    "메타광고": ("ad_data", "meta data_test"),
    "아마존 리뷰": ("Review_Data", "New_Amazon_Review"),
    "큐텐 리뷰": ("Review_Data", "New_Qoo10_Review"),
    "쇼피 리뷰": ("Review_Data", "New_Shopee_Review"),
    "스마트스토어 리뷰": ("Review_Data", "New_Smartstore_Review"),
    "프로모션": ("promotion_calendar", "promotion"),
    "물류": ("Export_control", "export_logistics"),
    "환율": ("Sales_Integration", "Exchange_Rate"),
    "국내CS": ("cs_dashboard", "domestic_cs_records"),
    "해외CS": ("cs_dashboard", "overseas_cs_refunds"),
}


def data_update_notice_for_sql(
    sql: str,
    manager: Optional[MaintenanceManager] = None,
) -> str:
    """Return a user notice only for updating tables referenced by ``sql``.

    Aggregate maintenance state is intentionally insufficient here: an ad-table
    reload must not warn a sales-only question. BigQuery SQL uses qualified table
    names, so matching the monitored ``dataset.table`` pair identifies the data
    that actually contributed to the answer without guessing from question words.
    """
    if not (sql or "").strip():
        return ""
    mm = manager or get_maintenance_manager()
    if mm.manual:
        # Manual maintenance is a global hard block handled before SQL execution.
        return ""

    normalized = re.sub(r"[`\"]", "", sql).lower()
    active_labels = []
    for label, (dataset, table_id) in _MONITORED_TABLES.items():
        if not mm.is_table_active(label):
            continue
        qualified = f"{dataset}.{table_id}".lower()
        if re.search(rf"(?<![\w]){re.escape(qualified)}(?![\w])", normalized):
            active_labels.append(label)

    if not active_labels:
        # ⛔ **점검 감지에만 기대면 "적재 중" 을 거의 못 말한다.** 그 판정은 행이
        #    줄어드는 것(truncate)만 보는데, 실제 사고는 append 중이었다.
        #    그래서 수정 시각이 지금 움직이는가를 한 번 더 본다.
        #
        # ⛔ **질문이 최근 날짜를 포함한다는 이유만으로는 공시하지 않는다**
        #    (2026-09-02 사용자 지시). 그것은 적재 상태를 본 것이 아니라 날짜만 본
        #    추측이라, 적재가 진작 끝난 뒤에도 어제·오늘을 물을 때마다 붙었다 —
        #    매번 뜨는 경고는 곧 아무도 안 읽는다. 공시는 **실제로 쓰는 중일 때**만 한다.
        return recent_load_notice_for_sql(sql, mm)
    labels = "·".join(active_labels)
    return (
        f"> ⚠️ **데이터 업데이트 중**: 이 질문이 조회한 **{labels}** 데이터가 "
        "현재 업데이트되고 있습니다. 조회 결과가 일시적으로 불완전할 수 있으니 "
        "업데이트 완료 후 다시 확인해 주세요."
    )


#: 수정 시각이 이보다 오래됐으면 무슨 일이 있어도 조용하다 (바깥 울타리).
#: ⚠️ 넓히지 마라 — 적재가 끝난 뒤에도 계속 붙으면 매번 뜨는 경고가 되고,
#:    매번 뜨는 경고는 곧 아무도 안 읽는다.
_ACTIVE_LOAD_SECONDS = 15 * 60

#: **지금 쓰는 중**으로 볼 기간 — 수정 시각이 실제로 앞으로 간 것을 본 뒤 이만큼.
#: 감시 루프가 60초마다 도니 폴링 3회 분량이다. 적재가 진행 중이면 폴링마다 값이
#: 다시 움직여 창이 계속 갱신되고, 적재가 끝나면 값이 굳어 공시가 스스로 꺼진다.
#: ⛔ 이것을 `_ACTIVE_LOAD_SECONDS` 로 되돌리지 마라 — 그러면 **끝난 적재를 15분
#:    내내 "지금 적재 중" 이라고 말한다.** 실제로 11분 전에 끝난 적재를 두고
#:    그렇게 나갔다 (2026-08-31 사용자 제보).
_LOAD_IN_PROGRESS_SECONDS = 3 * 60


def recent_load_notice_for_sql(sql: str,
                               manager: Optional[MaintenanceManager] = None) -> str:
    """이 질문이 조회한 테이블이 **방금 적재됐으면** 답변에 그렇게 적는다.

    사용자 지시(2026-08-31): *"데이터가 적재중이면 답변에 적재중이라고 표기해야함.
    그래야 사람들이 이게 오답이 아니구나라고 판단함."*

    ⛔ 점검 감지(`maintenance_auto_detect_loop`)는 행이 **줄어드는 것**만 본다.
       실제 사고는 append 중이었고, 그래서 화면도 답변도 아무 말이 없었다 —
       8/30 KBT 광고비가 91.5만원(실제 571.9만원)으로 나갔다.

    ⛔ **"최근에 수정됨" 을 "적재 중" 이라고 말하지 마라** (2026-08-31 사용자 제보:
       *"지금 적재중입니다는 맞는 표현이 아님. 이미 업데이트가 되어있음"*).
       처음엔 수정 시각이 15분 안이면 무조건 붙였는데, 그건 적재가 **끝난 뒤에도**
       15분 내내 참이다. 판정은 `moved_ts` 로 한다 — 폴링 사이에 수정 시각이
       실제로 앞으로 갔는가. 쓰는 중이면 계속 움직이고, 끝났으면 굳는다.
    ⚠️ 조회를 늘리지 않는다. 감시 루프가 60초마다 이미 읽어 둔 값을 쓴다.
    """
    if not (sql or "").strip():
        return ""
    mm = manager or get_maintenance_manager()
    normalized = re.sub(r"[`\"]", "", sql).lower()
    fresh = []
    for label, (dataset, table_id) in _MONITORED_TABLES.items():
        qualified = f"{dataset}.{table_id}".lower()
        if not re.search(rf"(?<![\w]){re.escape(qualified)}(?![\w])", normalized):
            continue
        ago = mm.table_modified_ago(label)
        moved_ago = mm.table_load_moved_ago(label)
        if ago is None or ago > _ACTIVE_LOAD_SECONDS:
            continue
        # 움직인 것을 본 적이 없으면 적재 중인지 알 수 없다 — 모르면 말하지 않는다.
        if moved_ago is None or moved_ago > _LOAD_IN_PROGRESS_SECONDS:
            continue
        fresh.append((label, int(ago // 60)))
    if not fresh:
        return ""
    parts = "·".join(f"{label}({mins}분 전)" for label, mins in fresh)
    return (
        f"> ⚠️ **지금 적재 중입니다** — 이 답이 쓴 {parts} 데이터가 방금 갱신됐습니다. "
        "**틀린 값이 아니라 아직 다 채워지지 않은 값**일 수 있습니다. "
        "잠시 뒤 다시 물으면 수치가 달라질 수 있습니다."
    )


async def maintenance_auto_detect_loop(interval: float = 60.0) -> None:
    """Background coroutine: poll __TABLES__ every `interval` seconds, per monitored table.

    Two detection methods (hybrid), applied independently to each table in
    `_MONITORED_TABLES`:
    1. last_modified_time: if table was modified within 3 min → updating
    2. row_count drop > 5% → updating (DELETE+INSERT pattern)

    Tables sharing a dataset are queried together (one __TABLES__ call per
    dataset) to keep BigQuery metadata query cost down.
    """
    mm = get_maintenance_manager()
    logger.info("maintenance_auto_detect_started", interval=interval, tables=len(_MONITORED_TABLES))

    # Wait a bit for server startup to complete
    await asyncio.sleep(10)

    _by_dataset: Dict[str, list] = {}
    for label, (dataset, table_id) in _MONITORED_TABLES.items():
        _by_dataset.setdefault(dataset, []).append((label, table_id))

    while True:
        for dataset, entries in _by_dataset.items():
            try:
                info_by_table = await _fetch_dataset_tables_info(dataset, [t for _, t in entries])
            except Exception as e:
                logger.warning("maintenance_auto_detect_error", dataset=dataset, error=str(e))
                continue

            for label, table_id in entries:
                info = info_by_table.get(table_id)
                if info is None:
                    continue

                row_count = info["row_count"]
                modified_ago = info["modified_ago_seconds"]
                # ⛔ 점검 판정과 **별개로** 항상 기록한다. 점검(truncate) 판정은
                #    append 를 안전하다고 넘기지만, 날짜를 지목한 질문에는 append 도
                #    위험하다 — 답변 공시가 이 값을 읽는다 (2026-08-31 사용자 지시).
                mm.note_table_modified(label, modified_ago)

                # Set baseline on first successful read
                if mm.table_baseline(label) is None:
                    mm.set_table_baseline(label, row_count)
                    logger.info("maintenance_baseline_set", table=label, baseline=row_count)

                # Detection 1: 적재 중으로 보이는 경우 (최근 수정 **그리고** 행이 줄어듦)
                # ⛔ 예전엔 "최근 수정"만으로 점검 중을 켰다. 상시 적재되는 테이블
                #    (광고·프로모션·마케팅·Shopify)은 늘 "0초 전 수정"이라 **영구히
                #    점검 중**으로 표시됐다 — 7일간 153회 (2026-08-18 로그 분석).
                #    게다가 아래 `continue` 가 Detection 2 를 건너뛰어 **베이스라인이
                #    영영 갱신되지 않았다.** 오탐이 스스로를 고착시키는 구조였다.
                #    적재 중이라는 진짜 신호는 "행이 줄어드는 것"이다 (truncate+reload).
                #    단순 append 는 조회해도 안전하므로 점검 중으로 보지 않는다.
                _base = mm.table_baseline(label)
                if (modified_ago is not None and modified_ago < _UPDATE_WINDOW_SECONDS
                        and _base and row_count < _base):
                    mm.auto_activate_table(
                        label,
                        f"테이블 적재 중 (row {row_count:,} < 기준 {_base:,}, "
                        f"{modified_ago:.0f}초 전 수정)",
                    )
                    continue

                # Detection 2: row count drop > 5%
                baseline = mm.table_baseline(label)
                if baseline and baseline > 0:
                    ratio = row_count / baseline

                    if ratio < 0.95:
                        mm.auto_activate_table(
                            label,
                            f"테이블 업데이트 감지 (row: {row_count:,} / baseline: {baseline:,}, -{(1-ratio)*100:.1f}%)",
                        )
                    elif ratio >= 0.98 and mm.is_table_active(label):
                        mm.set_table_baseline(label, row_count)
                        mm.auto_deactivate_table(label)
                        logger.info("maintenance_baseline_updated", table=label, new_baseline=row_count)
                    elif ratio >= 0.98:
                        mm.set_table_baseline(label, row_count)

                # If none of the above triggered, and table hasn't been modified recently → stable
                if mm.is_table_active(label) and modified_ago is not None and modified_ago >= _UPDATE_WINDOW_SECONDS:
                    mm.set_table_baseline(label, row_count)
                    mm.auto_deactivate_table(label)
                    logger.info("maintenance_stable_deactivated", table=label, modified_ago=modified_ago)

        await asyncio.sleep(interval)


async def _fetch_dataset_tables_info(dataset: str, table_ids: list) -> dict:
    """Fetch row_count + last_modified for multiple tables in one dataset via __TABLES__.

    table_ids come only from the hardcoded `_MONITORED_TABLES` registry, never
    from user input, so building the IN-list with an f-string is safe here.
    """
    result: dict = {}
    try:
        from app.core.bigquery import get_bigquery_client
        bq = get_bigquery_client()

        id_list = ", ".join(f"'{t}'" for t in table_ids)
        sql = (
            "SELECT table_id, row_count, "
            "TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), TIMESTAMP_MILLIS(last_modified_time), SECOND) as modified_ago "
            f"FROM `skin1004-319714.{dataset}.__TABLES__` "
            f"WHERE table_id IN ({id_list})"
        )
        rows = await asyncio.to_thread(bq.execute_query, sql, timeout=10.0, max_rows=len(table_ids))
        for row in rows or []:
            result[row.get("table_id")] = {
                "row_count": int(row.get("row_count", 0)),
                "modified_ago_seconds": float(row.get("modified_ago", 99999)),
            }
    except Exception as e:
        logger.warning("fetch_dataset_tables_info_failed", dataset=dataset, error=str(e))
    return result
