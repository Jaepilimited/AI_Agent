# -*- coding: utf-8 -*-
"""파생 사본이 **원본을 따라가고 있는가** — 사람이 알아차리기 전에 시스템이 안다.

⛔ **왜 만들었나** (2026-09-03 사용자 지시: *"그 db가 계속 업데이트 되는지 확인해야
   하는 시스템이 있어야 할 것 같음. 이렇게 누군가 알아차리고 말하는 것보다 니가
   스스로 발전해야 하는 거야"*).

   같은 날 세 가지가 **전부 사람 눈으로** 발견됐다:
     1. CS/BP 제품 Q&A 캐시가 **서버 기동 시에만** 갱신됐다 (17시간 낡음)
     2. 대표 제품 목록이 하드코딩이라 36만 개 팔린 신제품을 "없는 제품" 이라 답했다
     3. 물류 수량 칸에 주문번호가 들어가 총계가 3,492배 부풀었다

   ⚠️ **기존 감시로는 1번을 구조적으로 못 잡는다.** `EXPECTED_JOBS` 는 *잡이 돌았는가*
      를 보는데 CS 는 **잡 자체가 없었다** — 없는 잡은 "빠졌다" 고 말할 수가 없다.

**그래서 두 겹이다**:

  A. **신선도** — 사본이 원본보다 뒤처졌는가 (`check()`)
  B. **커버리지** — 사용자에게 답하는 소스 중 **감시가 안 붙은 것**이 있는가
     (`coverage_gaps()`). 이것이 1번을 잡는 층이다. `@@` 등록부(`_DB_REGISTRY`)를
     기준으로 대조하므로, 새 소스를 붙이고 감시를 안 붙이면 **그날 걸린다.**

⛔ **"잡이 돌았다" 를 신선도로 쓰지 마라.** 잡이 성공해도 원본을 못 읽었을 수 있고
   (권한 만료·탭 이름 변경), 애초에 잡이 없을 수도 있다. **데이터 자신의 시각**을 본다.

⚠️ **나이가 아니라 뒤처짐(lag)을 본다.** 원본이 안 바뀌었으면 사본이 오래돼도 정상이다.
   나이로만 보면 아무도 안 고친 시트마다 매일 경고가 뜨고, 그러면 아무도 안 읽는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Set

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Reading:
    name: str
    ok: bool
    detail: str
    ours: Optional[datetime] = None
    source: Optional[datetime] = None


@dataclass
class Source:
    """감시 대상 하나.

    `routes`/`keys` 는 이 소스가 덮는 `@@` 라우트·키다 — 커버리지 대조에 쓴다.
    """
    name: str
    probe: Callable[[], Reading]
    routes: Set[str] = field(default_factory=set)
    keys: Set[str] = field(default_factory=set)


def _now() -> datetime:
    return datetime.now()


def _drive_modified(file_id: str) -> Optional[datetime]:
    """원본 구글 파일의 수정 시각. 못 읽으면 None.

    ⚠️ **서비스 계정이 Sheets 는 읽는데 Drive 메타데이터는 못 읽는 시트가 있다**
       (실측 2026-09-03: OP 재고 시트가 Drive 404). 공유 방식 차이다 — 그때는 lag 을
       잴 수 없으니 사본 나이로 물러서고, 그 사실을 `detail` 에 적는다.
    """
    if not file_id:
        return None
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        from app.config import get_settings
        creds = Credentials.from_service_account_file(
            get_settings().google_application_credentials,
            scopes=["https://www.googleapis.com/auth/drive.readonly"])
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        ts = drive.files().get(fileId=file_id, fields="modifiedTime").execute().get("modifiedTime")
        if not ts:
            return None
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None)
    except Exception as e:
        logger.info("drive_modified_unavailable", file_id=file_id[:12], error=str(e)[:100])
        return None


def _lag_reading(name: str, ours: Optional[datetime], source: Optional[datetime],
                 max_lag_hours: float, max_age_hours: Optional[float] = None) -> Reading:
    """사본이 원본을 따라잡았는가. 원본 시각을 못 구하면 **나이로 물러선다.**"""
    if ours is None:
        return Reading(name, False, "사본이 아예 없다 (한 번도 적재되지 않았다)")
    if source is None:
        if max_age_hours is None:
            return Reading(name, True, f"사본 {ours:%m-%d %H:%M} (원본 시각 확인 불가)",
                           ours, None)
        age = (_now() - ours).total_seconds() / 3600
        return Reading(
            name, age <= max_age_hours,
            f"사본 {int(age)}시간 전 · 원본 시각을 읽을 수 없어 **나이로만** 판정"
            + ("" if age <= max_age_hours else f" — 기준 {int(max_age_hours)}시간 초과"),
            ours, None)
    lag = (source - ours).total_seconds() / 3600
    if lag <= max_lag_hours:
        return Reading(name, True,
                       f"원본 {source:%m-%d %H:%M} · 사본 {ours:%m-%d %H:%M} (뒤처짐 없음)",
                       ours, source)
    return Reading(
        name, False,
        f"**원본이 {lag:.1f}시간 앞서 있다** — 원본 {source:%m-%d %H:%M} · "
        f"사본 {ours:%m-%d %H:%M} (허용 {max_lag_hours}시간)",
        ours, source)


# ── 개별 프로브 ──────────────────────────────────────────────────────────────

def _probe_cs_qa() -> Reading:
    """CS/BP 제품 Q&A — 시트 → **메모리 캐시**.

    ⚠️ 모듈 캐시라 **앱 프로세스 안에서** 잴 때만 뜻이 있다 (밖에서 부르면 비어 있다).
    """
    from app.agents.cs_agent import status
    from app.config import get_settings

    st = status()
    if not st["loaded"] or not st["count"]:
        # ⚠️ 앱 **밖에서** 부르면 항상 여기에 걸린다 (모듈 캐시라 새 프로세스는 비어 있다).
        #    매일 도는 자가 점검은 앱 안에서 실행되므로 그때의 값이 진짜다.
        return Reading("CS/BP 제품 Q&A", False,
                       "캐시가 비어 있다 — 시트 권한·탭 이름 확인 "
                       "(앱 프로세스 밖에서 잰 값이면 정상)")
    age = st["age_seconds"]
    ours = None if age is None else _now() - timedelta(seconds=age)
    r = _lag_reading("CS/BP 제품 Q&A", ours,
                     _drive_modified(get_settings().cs_spreadsheet_id or ""),
                     max_lag_hours=2, max_age_hours=3)
    r.detail = f"{st['count']}건 · " + r.detail
    return r


def _probe_ingredients() -> Reading:
    from app.core.ingredients import SPREADSHEET_ID
    from app.db.mariadb import fetch_one

    row = fetch_one("SELECT COUNT(*) c, MAX(synced_at) t FROM product_ingredients") or {}
    r = _lag_reading("제품 전성분", row.get("t"), _drive_modified(SPREADSHEET_ID),
                     max_lag_hours=26, max_age_hours=26)
    r.detail = f"{row.get('c') or 0}종 · " + r.detail
    return r


def _probe_model_rights() -> Reading:
    from app.core.model_rights import SPREADSHEET_ID
    from app.db.mariadb import fetch_one

    row = fetch_one("SELECT COUNT(*) c, MAX(synced_at) t FROM model_rights") or {}
    r = _lag_reading("모델 초상권", row.get("t"), _drive_modified(SPREADSHEET_ID),
                     max_lag_hours=26, max_age_hours=26)
    r.detail = f"{row.get('c') or 0}명 · " + r.detail
    return r


def _probe_op_inventory() -> Reading:
    """OP 재고 — ⚠️ Drive 메타데이터를 못 읽는다(404). 시트가 **스스로 적어 둔** 시각을 쓴다."""
    from app.core.inventory import parse_stamp, status
    from app.db.mariadb import fetch_one

    row = fetch_one("SELECT COUNT(*) c, MAX(synced_at) t FROM op_inventory") or {}
    source = None
    try:
        source = parse_stamp((status() or {}).get("sheet_updated_at") or "")
    except Exception as e:
        logger.info("op_sheet_stamp_unavailable", error=str(e)[:100])
    r = _lag_reading("OP 재고", row.get("t"), source, max_lag_hours=8, max_age_hours=26)
    r.detail = f"{row.get('c') or 0}행 · " + r.detail
    return r


def _probe_value_lists() -> Reading:
    from app.db.mariadb import fetch_one

    row = fetch_one("SELECT COUNT(*) c, MAX(updated_at) t FROM bq_value_cache") or {}
    r = _lag_reading("프롬프트 값 목록", row.get("t"), None, 26, max_age_hours=26)
    r.detail = f"{row.get('c') or 0}개 · " + r.detail
    return r


def _probe_product_catalog() -> Reading:
    """대표 제품 목록 — 하드코딩이던 것을 실측으로 바꾼 자리 (2026-09-03)."""
    from app.db.mariadb import fetch_one

    row = fetch_one("SELECT n, updated_at t FROM bq_value_cache WHERE name = %s",
                    ("TopProducts",)) or {}
    r = _lag_reading("대표 제품 목록", row.get("t"), None, 26, max_age_hours=26)
    r.detail = f"{row.get('n') or 0}종 · " + r.detail
    return r


def _probe_qdrant() -> Reading:
    """노션 문서 벡터 — payload 에 적재 시각이 없어 **마지막 성공 적재**로 본다."""
    from app.db.mariadb import fetch_one

    row = fetch_one(
        "SELECT MAX(finished_at) t FROM job_runs WHERE job_id = %s AND ok = 1",
        ("qdrant_pipeline_daily",)) or {}
    r = _lag_reading("노션 문서 벡터", row.get("t"), None, 26, max_age_hours=30)
    r.detail = "마지막 성공 적재 · " + r.detail
    return r


def _probe_product_info() -> Reading:
    """제품정보(노션 제품 스펙) — Q&A 와 별개 소스다."""
    from app.core.product_info import status

    st = status()
    r = _lag_reading("제품정보(제품 스펙)", st.get("synced_at"), None, 26, max_age_hours=26)
    r.detail = f"{st.get('count') or 0}종 · {st.get('lines') or 0}라인 · " + r.detail
    return r


# ── 등록부 ──────────────────────────────────────────────────────────────────
# ⛔ 새 파생 사본을 만들면 **여기에 등록한다.** 등록을 잊으면 `coverage_gaps()` 가
#    그 소스의 `@@` 라우트를 "감시 없음" 으로 잡는다 — 잊는 것까지 감시한다.
SOURCES: List[Source] = [
    Source("CS/BP 제품 Q&A", _probe_cs_qa, routes={"cs"}, keys={"BP"}),
    Source("OP 재고", _probe_op_inventory, routes={"inventory"}, keys={"OP"}),
    Source("제품 전성분", _probe_ingredients),
    Source("제품정보(제품 스펙)", _probe_product_info),
    Source("모델 초상권", _probe_model_rights, routes={"model_rights"}, keys={"초상권"}),
    Source("노션 문서 벡터", _probe_qdrant, routes={"notion"}),
    Source("프롬프트 값 목록", _probe_value_lists, routes={"bigquery"}),
    Source("대표 제품 목록", _probe_product_catalog, routes={"direct"}),
]

# ⚠️ 파생 사본이 아니라서 이 등록부가 덮지 않는 라우트 — **이유를 적어 둔다.**
#    적어 두지 않으면 다음 사람이 "빠뜨렸나?" 를 매번 다시 조사한다.
NOT_A_COPY = {
    "gws": "구글을 그때그때 조회한다 — 사본이 없다",
    "report": "산출물이다 — 만들 때마다 새로 조회한다",
}


def check() -> List[Reading]:
    """모든 파생 사본의 신선도. ⚠️ 하나가 터져도 나머지는 잰다."""
    out: List[Reading] = []
    for src in SOURCES:
        try:
            out.append(src.probe())
        except Exception as e:
            out.append(Reading(src.name, False,
                               f"측정 실패: {type(e).__name__} {str(e)[:120]}"))
    return out


def coverage_gaps() -> List[str]:
    """사용자에게 답하는 소스인데 **신선도 감시가 없는 것**.

    ⛔ 이 층이 없었다면 CS 캐시는 계속 조용히 낡았을 것이다 — 잡이 아예 없어서
       `EXPECTED_JOBS` 로는 구조적으로 못 잡는다.
    """
    from app.agents.orchestrator import OrchestratorAgent

    watched_routes: Set[str] = set()
    watched_keys: Set[str] = set()
    for s in SOURCES:
        watched_routes |= s.routes
        watched_keys |= s.keys

    gaps = []
    for entry in OrchestratorAgent.get_db_registry():
        route = entry.get("route", "")
        key = entry.get("key", "")
        if route in NOT_A_COPY or route in watched_routes or key in watched_keys:
            continue
        gaps.append(f"{key}({route})")
    return sorted(set(gaps))
