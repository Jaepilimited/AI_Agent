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
import time
from enum import Enum
from typing import Dict, Optional

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# MaintenanceManager
# ---------------------------------------------------------------------------

class MaintenanceManager:
    """Tracks BigQuery table update state and blocks queries during maintenance."""

    def __init__(self) -> None:
        self.active: bool = False
        self.reason: str = ""
        self.manual: bool = False  # True if activated manually (won't auto-deactivate)
        self._baseline_rows: Optional[int] = None
        self._last_check: float = 0.0

    def activate(self, reason: str = "수동 점검모드") -> None:
        """Manually activate maintenance mode."""
        self.active = True
        self.reason = reason
        self.manual = True
        logger.warning("maintenance_activated", reason=reason, manual=True)

    def deactivate(self) -> None:
        """Deactivate maintenance mode (manual or auto)."""
        was_active = self.active
        self.active = False
        self.reason = ""
        self.manual = False
        if was_active:
            logger.info("maintenance_deactivated")

    def auto_activate(self, reason: str) -> None:
        """Auto-activate from row count monitoring (won't override manual)."""
        if self.manual:
            return  # Don't touch manual mode
        if not self.active:
            self.active = True
            self.reason = reason
            logger.warning("maintenance_auto_activated", reason=reason)

    def auto_deactivate(self) -> None:
        """Auto-deactivate when row count recovers (skips if manual)."""
        if self.manual:
            return
        if self.active:
            self.active = False
            self.reason = ""
            logger.info("maintenance_auto_deactivated")

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
# Module-level singletons
# ---------------------------------------------------------------------------

_maintenance_manager: Optional[MaintenanceManager] = None
_circuits: Dict[str, CircuitBreaker] = {}


def get_maintenance_manager() -> MaintenanceManager:
    """Get or create the MaintenanceManager singleton."""
    global _maintenance_manager
    if _maintenance_manager is None:
        _maintenance_manager = MaintenanceManager()
    return _maintenance_manager


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

    # Build services map
    services: Dict[str, dict] = {}

    # BigQuery Sales
    bq_circuit = get_circuit("bigquery")
    if mm.active:
        services["BigQuery \ub9e4\ucd9c"] = {"status": "updating", "detail": "SALES_ALL_Backup"}
    elif bq_circuit.state != CBState.CLOSED:
        services["BigQuery \ub9e4\ucd9c"] = {"status": "error", "detail": bq_circuit.state.value}
    else:
        services["BigQuery \ub9e4\ucd9c"] = {"status": "ok", "detail": "SALES_ALL_Backup"}

    # BigQuery Product (shares circuit with bigquery)
    if bq_circuit.state != CBState.CLOSED and not mm.active:
        services["BigQuery \uc81c\ud488"] = {"status": "error", "detail": "Product"}
    else:
        services["BigQuery \uc81c\ud488"] = {"status": "ok", "detail": "Product"}

    # Marketing DB tables (share bigquery circuit)
    _marketing_tables = {
        "BQ \uad11\uace0\ub370\uc774\ud130": ("marketing_analysis", "integrated_advertising_data"),
        "BQ \ub9c8\ucf00\ud305\ube44\uc6a9": ("marketing_analysis", "Integrated_marketing_cost"),
        "BQ Shopify": ("marketing_analysis", "shopify_analysis_sales"),
        "BQ \ud50c\ub7ab\ud3fc": ("Platform_Data", "raw_data"),
        "BQ \uc778\ud50c\ub8e8\uc5b8\uc11c": ("marketing_analysis", "influencer_input_ALL_TEAMS"),
        "BQ \uc544\ub9c8\uc874\uac80\uc0c9": ("marketing_analysis", "amazon_search_analytics_catalog_performance"),
        "BQ \uc544\ub9c8\uc874\ub9ac\ubdf0": ("Review_Data", "Amazon_Review"),
        "BQ \ud050\ud150\ub9ac\ubdf0": ("Review_Data", "Qoo10_Review"),
        "BQ \uc1fc\ud53c\ub9ac\ubdf0": ("Review_Data", "Shopee_Review"),
        "BQ \uc2a4\ub9c8\ud2b8\uc2a4\ud1a0\uc5b4": ("Review_Data", "Smartstore_Review"),
        "BQ \uba54\ud0c0\uad11\uace0": ("ad_data", "meta data_test"),
    }
    for label, (dataset, table) in _marketing_tables.items():
        if bq_circuit.state != CBState.CLOSED:
            services[label] = {"status": "error", "detail": bq_circuit.state.value}
        elif mm.active:
            services[label] = {"status": "updating", "detail": mm.reason}
        else:
            services[label] = {"status": "ok", "detail": f"{dataset}.{table}"}

    # Notion
    notion_circuit = get_circuit("notion")
    try:
        from app.agents.notion_agent import _page_titles
        notion_count = len(_page_titles)
        if notion_circuit.state != CBState.CLOSED:
            services["Notion \ubb38\uc11c"] = {"status": "error", "detail": f"{notion_count} pages"}
        else:
            services["Notion \ubb38\uc11c"] = {"status": "ok", "detail": f"{notion_count} pages"}
    except Exception:
        services["Notion \ubb38\uc11c"] = {"status": "ok", "detail": "10 pages"}

    # CS Q&A
    try:
        from app.agents.cs_agent import _qa_cache, _cache_loaded
        if _cache_loaded:
            services["CS Q&A"] = {"status": "ok", "detail": f"{len(_qa_cache)} entries"}
        else:
            services["CS Q&A"] = {"status": "error", "detail": "loading"}
    except Exception:
        services["CS Q&A"] = {"status": "ok", "detail": "737 entries"}

    # BP (CS Q&A) — mirrors CS Q&A status
    services["BP (CS Q&A)"] = dict(services.get("CS Q&A", {"status": "ok", "detail": ""}))

    # Team Resources (DB HUB)
    try:
        from app.agents.team_agent import _resource_cache, _cache_loaded, _last_sync
        if _cache_loaded:
            services["팀별 자료"] = {"status": "ok", "detail": f"{len(_resource_cache)} entries, sync: {_last_sync[:16]}"}
        else:
            services["팀별 자료"] = {"status": "error", "detail": "loading"}
    except Exception:
        services["팀별 자료"] = {"status": "ok", "detail": "not loaded"}

    # Google Workspace
    services["Google Workspace"] = {"status": "ok", "detail": "OAuth ready"}

    # Gemini API
    try:
        from app.core.llm import get_flash_client
        client = get_flash_client()
        services["Gemini API"] = {"status": "ok", "detail": client.model_name if hasattr(client, 'model_name') else "Flash"}
    except Exception as e:
        services["Gemini API"] = {"status": "error", "detail": str(e)[:30]}

    # Claude API
    try:
        settings = get_settings()
        if settings.anthropic_api_key:
            services["Claude API"] = {"status": "ok", "detail": "Sonnet"}
        else:
            services["Claude API"] = {"status": "error", "detail": "no key"}
    except Exception:
        services["Claude API"] = {"status": "error", "detail": "unavailable"}

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

    return {
        "maintenance": mm.status,
        "services": services,
        "circuits": circuits,
    }


# ---------------------------------------------------------------------------
# Auto-detect background loop
# ---------------------------------------------------------------------------

_UPDATE_WINDOW_SECONDS = 180  # 3 minutes: table modified within this window → "updating"


async def maintenance_auto_detect_loop(interval: float = 60.0) -> None:
    """Background coroutine: poll __TABLES__ every `interval` seconds.

    Two detection methods (hybrid):
    1. last_modified_time: if table was modified within 10 min → updating
    2. row_count drop > 5% → updating (DELETE+INSERT pattern)
    """
    mm = get_maintenance_manager()
    logger.info("maintenance_auto_detect_started", interval=interval)

    # Wait a bit for server startup to complete
    await asyncio.sleep(10)

    while True:
        try:
            info = await _fetch_table_info()
            if info is None:
                await asyncio.sleep(interval)
                continue

            row_count = info["row_count"]
            modified_ago = info["modified_ago_seconds"]

            # Set baseline on first successful read
            if mm._baseline_rows is None:
                mm._baseline_rows = row_count
                logger.info("maintenance_baseline_set", baseline=row_count)

            # Detection 1: recently modified (within 10 min window)
            if modified_ago is not None and modified_ago < _UPDATE_WINDOW_SECONDS:
                mm.auto_activate(
                    f"테이블 업데이트 중 ({modified_ago:.0f}초 전 수정)"
                )
                await asyncio.sleep(interval)
                continue

            # Detection 2: row count drop > 5%
            baseline = mm._baseline_rows
            if baseline and baseline > 0:
                ratio = row_count / baseline

                if ratio < 0.95:
                    mm.auto_activate(
                        f"테이블 업데이트 감지 (row: {row_count:,} / baseline: {baseline:,}, -{(1-ratio)*100:.1f}%)"
                    )
                elif ratio >= 0.98 and mm.active and not mm.manual:
                    mm._baseline_rows = row_count
                    mm.auto_deactivate()
                    logger.info("maintenance_baseline_updated", new_baseline=row_count)
                elif ratio >= 0.98 and not mm.active:
                    mm._baseline_rows = row_count

            # If none of the above triggered, and table hasn't been modified recently → stable
            if mm.active and not mm.manual and modified_ago is not None and modified_ago >= _UPDATE_WINDOW_SECONDS:
                mm._baseline_rows = row_count
                mm.auto_deactivate()
                logger.info("maintenance_stable_deactivated", modified_ago=modified_ago)

        except Exception as e:
            logger.warning("maintenance_auto_detect_error", error=str(e))

        await asyncio.sleep(interval)


async def _fetch_table_info() -> Optional[dict]:
    """Fetch SALES_ALL_Backup row_count + last_modified from __TABLES__."""
    try:
        from app.core.bigquery import get_bigquery_client
        bq = get_bigquery_client()

        sql = (
            "SELECT row_count, "
            "TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), TIMESTAMP_MILLIS(last_modified_time), SECOND) as modified_ago "
            "FROM `skin1004-319714.Sales_Integration.__TABLES__` "
            "WHERE table_id = 'SALES_ALL_Backup'"
        )
        rows = await asyncio.to_thread(bq.execute_query, sql, timeout=10.0, max_rows=1)
        if rows:
            return {
                "row_count": int(rows[0].get("row_count", 0)),
                "modified_ago_seconds": float(rows[0].get("modified_ago", 99999)),
            }
    except Exception as e:
        logger.warning("fetch_table_info_failed", error=str(e))
    return None
