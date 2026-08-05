"""자가 점검 — 시스템 건강성·데이터 무결성 회귀를 매일 스스로 잡아낸다.

왜 필요한가:
    2026-08-04, AD 동기화가 **6일 동안 매일 밤 실패**하고 있었는데 아무도 몰랐다.
    크론은 정상 실행됐고 로그도 남았지만, 그 로그를 읽는 사람이 없었다. 원인은
    APP 서버 .env 의 DB 비밀번호가 이관 때 잘못 옮겨진 것(9자 vs 10자)이었다.
    기존 `quality_monitor` 는 **답변 품질**만 본다 — 배치가 죽었는지, 데이터가
    썩었는지, 권한 방어선이 뚫렸는지는 아무것도 감시하지 않았다.

무엇을 하는가:
    1. 검사    — 인프라·배치 신선도·DB 무결성·권한 불변식을 단언(assert)으로 검증
    2. 기록    — 결과를 DB 에 남겨 추세를 본다 (언제부터 깨졌는지 알 수 있게)
    3. 자가치유 — 안전하게 되돌릴 수 있는 것은 스스로 고친다
    4. 노출    — 새로 깨진 것을 사이드바 배지와 Admin 탭으로 알린다.
                 잔디 전송은 기본 꺼짐(self_check_notify) — 운영상 쓰지 않기로 했고
                 WAS 는 프록시에서 wh.jandi.com 이 막혀 있다.

설계 원칙:
    - 검사는 부작용이 없어야 한다. 고치는 것은 `repair` 로 분리한다.
    - 자가치유는 **되돌릴 수 있는 것만**. DB 스키마 변경·삭제는 절대 자동화하지 않는다.
    - 알림은 상태 변화(정상→실패, 실패→정상)에만. 반복 알림은 알림을 죽인다.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

import structlog

from app.db.mariadb import execute, execute_lastid, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

JANDI_URL = "https://wh.jandi.com/connect-api/webhook/11320800/7c1bdd4a0947be10377703affd57e97a"

SEV_CRITICAL = "critical"   # 서비스/데이터에 즉시 영향
SEV_WARNING = "warning"     # 방치하면 문제가 되는 것
SEV_INFO = "info"           # 참고

# ── 저장소 ────────────────────────────────────────────────────────────────────

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS self_check_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    total INT NOT NULL DEFAULT 0,
    passed INT NOT NULL DEFAULT 0,
    failed INT NOT NULL DEFAULT 0,
    repaired INT NOT NULL DEFAULT 0,
    duration_ms INT NOT NULL DEFAULT 0,
    INDEX idx_run_at (run_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_DDL_RESULTS = """
CREATE TABLE IF NOT EXISTS self_check_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id INT NOT NULL,
    check_id VARCHAR(64) NOT NULL,
    category VARCHAR(32) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    ok TINYINT(1) NOT NULL,
    detail TEXT,
    repaired TINYINT(1) NOT NULL DEFAULT 0,
    repair_note TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_run (run_id),
    INDEX idx_check (check_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


_DDL_JOB_RUNS = """
CREATE TABLE IF NOT EXISTS job_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    job_id VARCHAR(64) NOT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME NULL,
    ok TINYINT(1) NULL,
    detail TEXT,
    duration_ms INT NULL,
    INDEX idx_job (job_id, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_self_check_tables() -> None:
    """검사 결과·잡 실행 기록 테이블 생성 (idempotent)."""
    for ddl in (_DDL_RUNS, _DDL_RESULTS, _DDL_JOB_RUNS):
        try:
            execute(ddl)
        except Exception as e:  # 이미 있으면 무시
            logger.debug("self_check_ddl_skip", error=str(e)[:120])


# ── 잡 실행 기록 ──────────────────────────────────────────────────────────────
#
# 배치 건강성을 **부수효과**(테이블에 행이 늘었나)로 판정하면 함정에 빠진다.
# 위키 추출 잡은 처리할 메시지가 없으면 로그를 남기지 않아, 밤새 한산했을 뿐인데
# "3시간째 기록 없음 = 고장"으로 오탐이 났다 (2026-08-05).
#
# 그래서 **실행 자체**를 기록한다. 일감이 없어도 "돌았고 할 일이 없었다"가 남으므로
# '할 일이 없어서 안 돈 것'과 '죽어서 못 돈 것'이 구분된다.


class _JobRun:
    """track_job 이 넘겨주는 핸들 — 잡이 자기 결과를 한 줄로 남긴다."""

    def __init__(self) -> None:
        self.note = ""

    def set_note(self, note: str) -> None:
        self.note = str(note)[:1000]


@contextmanager
def track_job(job_id: str):
    """스케줄 잡 실행을 job_runs 에 기록한다. 예외는 실패로 남기고 그대로 올린다."""
    ensure_self_check_tables()
    started = datetime.now()
    run_id = None
    try:
        run_id = execute_lastid(
            "INSERT INTO job_runs (job_id, started_at) VALUES (%s, %s)", (job_id, started)
        )
    except Exception as e:
        logger.warning("job_run_insert_failed", job=job_id, error=str(e)[:120])

    handle = _JobRun()
    try:
        yield handle
    except Exception as e:
        _finish_job(run_id, started, False, f"{type(e).__name__}: {str(e)[:400]}")
        raise
    else:
        _finish_job(run_id, started, True, handle.note)


def _finish_job(run_id, started, ok: bool, detail: str) -> None:
    if run_id is None:
        return
    try:
        execute(
            "UPDATE job_runs SET finished_at = %s, ok = %s, detail = %s, duration_ms = %s "
            "WHERE id = %s",
            (datetime.now(), 1 if ok else 0, (detail or "")[:1000],
             int((datetime.now() - started).total_seconds() * 1000), run_id),
        )
    except Exception as e:
        logger.warning("job_run_update_failed", run_id=run_id, error=str(e)[:120])


# 잡별 허용 간격(시간). 스케줄 주기 + 여유를 준다.
EXPECTED_JOBS: dict[str, tuple[float, str]] = {
    "wiki_extract_hourly": (3, "위키 추출 (매시 :15)"),
    "team_sync_daily": (26, "팀 리소스 동기화 (01:00)"),
    "qdrant_pipeline_daily": (26, "Qdrant 파이프라인 (05:00)"),
    "quality_snapshot_daily": (26, "품질 스냅샷 (00:05)"),
    "self_check_daily": (26, "자가 점검 (07:30)"),
    "weekly_growth_report": (24 * 8, "주간 성장 리포트 (월 00:10)"),
    "ad_sync": (26, "AD 동기화 (APP 서버 22:00)"),
    "knowledge_map_build": (26, "지식맵 빌드 (WAS 03:00)"),
}


# ── 검사 정의 ─────────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    ok: bool
    detail: str = ""
    repairable: bool = False
    repair_payload: dict = field(default_factory=dict)


@dataclass
class Check:
    id: str
    category: str
    severity: str
    description: str
    fn: Callable[[], CheckResult]
    repair: Optional[Callable[[dict], str]] = None


# ---- 배치 신선도 (AD sync 6일 침묵을 잡았어야 할 검사) ----


def _check_ad_sync_fresh() -> CheckResult:
    row = fetch_one("SELECT MAX(synced_at) AS m FROM ad_users")
    last = row and row.get("m")
    if not last:
        return CheckResult(False, "synced_at 기록이 없다")
    age_h = (datetime.now() - last).total_seconds() / 3600
    # 매일 22:00 실행 → 26시간이면 한 번 걸렀다는 뜻
    return CheckResult(age_h <= 26, f"마지막 동기화 {last} ({age_h:.1f}시간 전)")


def _check_wiki_extract_fresh() -> CheckResult:
    """위키 추출 잡이 **밀린 일감을 처리하고 있는가**.

    "마지막 기록이 N시간 전"만 보면 안 된다. 이 잡은 처리할 메시지가 없으면
    로그를 남기지 않으므로, 밤새 아무도 안 쓰면 정상인데도 실패로 잡힌다
    (2026-08-05 실제로 이 오탐이 났다). 신선도가 아니라 **backlog** 로 판정한다.
    """
    # 컬럼명은 processed_at 이다 (created_at 아님 — 실제로 헛짚었던 부분)
    row = fetch_one("SELECT MAX(processed_at) AS m FROM wiki_extraction_log")
    last = row and row.get("m")
    if not last:
        return CheckResult(False, "추출 기록 없음")

    pending = fetch_one("SELECT COUNT(*) c FROM messages WHERE created_at > %s", (last,))
    n_pending = (pending or {}).get("c", 0)
    age_h = (datetime.now() - last).total_seconds() / 3600
    if n_pending == 0:
        return CheckResult(True, f"마지막 추출 {last} — 이후 신규 메시지 없음 (처리할 일감 없음)")
    # 매시 :15 실행 → 일감이 있는데 3시간째 안 줄었으면 멈춘 것
    return CheckResult(
        age_h <= 3,
        f"마지막 추출 {last} ({age_h:.1f}시간 전) · 미처리 메시지 {n_pending}건",
    )


def _check_job_heartbeats() -> CheckResult:
    """등록된 모든 스케줄 잡이 제 주기 안에 **성공적으로** 돌았는가.

    부수효과가 아니라 job_runs 의 실행 기록으로 판정하므로, 일감이 없어 아무것도
    하지 않은 실행도 정상으로 잡힌다. 잡 하나를 새로 추가할 때 EXPECTED_JOBS 에만
    등록하면 자동으로 감시 대상이 된다.
    """
    now = datetime.now()
    # 계측을 켠 시점. 이보다 주기가 긴 잡은 아직 한 번도 돌 기회가 없었을 수 있으므로
    # "기록 없음"을 실패로 보지 않는다. 유예가 지나면 자동으로 실패로 전환된다 —
    # 무기한 봐주면 잡이 통째로 사라져도 영영 모른다.
    first = fetch_one("SELECT MIN(started_at) AS m FROM job_runs")
    tracking_since = (first or {}).get("m")
    tracking_h = ((now - tracking_since).total_seconds() / 3600) if tracking_since else 0.0

    stale, failing, never, pending, hung = [], [], [], [], []
    for job_id, (max_h, label) in EXPECTED_JOBS.items():
        row = fetch_one(
            "SELECT started_at, finished_at, ok, detail FROM job_runs WHERE job_id = %s "
            "ORDER BY id DESC LIMIT 1",
            (job_id,),
        )
        if not row:
            # 계측을 켠 지 이 잡의 주기보다 오래됐는데도 기록이 없으면 진짜 안 도는 것
            (never if tracking_h > max_h else pending).append(label)
            continue
        age_h = (now - row["started_at"]).total_seconds() / 3600
        # 시작만 하고 끝나지 않은 실행 — 외부 호출에 매달려 멈춘 경우가 여기 걸린다.
        # started_at 이 최근이라 "신선"해 보이므로 이 분기가 없으면 통과해버린다
        # (2026-08-05 지식맵 빌드가 Gemini 호출에 걸려 실제로 이 상태였다).
        if row["finished_at"] is None and age_h > max_h:
            hung.append(f"{label} {age_h:.0f}h째 미종료")
        elif age_h > max_h:
            stale.append(f"{label} {age_h:.0f}h 전")
        elif row["ok"] == 0:
            failing.append(f"{label}: {(row['detail'] or '')[:60]}")

    parts = []
    if stale:
        parts.append(f"주기 초과 {len(stale)}건 — {', '.join(stale)}")
    if failing:
        parts.append(f"실패 {len(failing)}건 — {'; '.join(failing)}")
    if hung:
        parts.append(f"시작 후 끝나지 않음 {len(hung)}건 — {', '.join(hung)}")
    if never:
        parts.append(f"주기가 지났는데 실행 기록이 아예 없음 {len(never)}건 — {', '.join(never)}")
    if pending:
        parts.append(f"첫 실행 대기 {len(pending)}건 (계측 {tracking_h:.1f}h 경과) — {', '.join(pending)}")
    ok = not (stale or failing or never or hung)
    return CheckResult(ok, " / ".join(parts) if parts else
                       f"등록된 잡 {len(EXPECTED_JOBS)}개 모두 정상 주기")


def _check_quality_snapshot_fresh() -> CheckResult:
    row = fetch_one("SELECT MAX(snapshot_date) AS m FROM quality_snapshots")
    last = row and row.get("m")
    if not last:
        return CheckResult(False, "스냅샷 없음")
    age_d = (datetime.now().date() - last).days
    return CheckResult(age_d <= 2, f"마지막 품질 스냅샷 {last} ({age_d}일 전)")


# ---- DB 무결성 ----


def _orphan(sql: str, label: str) -> CheckResult:
    row = fetch_one(sql)
    n = (row or {}).get("c", 0)
    return CheckResult(n == 0, f"{label} 고아 {n}건")


def _check_orphan_user_groups_ad() -> CheckResult:
    return _orphan(
        "SELECT COUNT(*) c FROM user_groups ug "
        "LEFT JOIN ad_users a ON ug.ad_user_id = a.id WHERE a.id IS NULL",
        "user_groups→ad_users",
    )


def _check_orphan_user_groups_grp() -> CheckResult:
    return _orphan(
        "SELECT COUNT(*) c FROM user_groups ug "
        "LEFT JOIN access_groups g ON ug.group_id = g.id WHERE g.id IS NULL",
        "user_groups→access_groups",
    )


def _check_users_email() -> CheckResult:
    rows = fetch_all("SELECT id, display_name FROM users WHERE email IS NULL OR email = ''")
    names = [r["display_name"] for r in rows]
    return CheckResult(not rows, f"이메일 누락 {len(rows)}명: {names[:5]}")


# LIKE '%...%' 를 쓰면 pymysql 이 `%` 를 포맷 지시자로 읽어 터진다(파라미터가 없어도
# 빈 튜플이 전달되면 포맷을 시도한다). 게다가 MySQL LIKE 에서 백슬래시는 이스케이프
# 문자라 백슬래시 리터럴 매칭이 지저분해진다. CHAR(92)+LOCATE 로 둘 다 피한다.
_ESCAPED_NAME_COND = "LOCATE(CONCAT(CHAR(92), 'u'), display_name) > 0"


def _check_name_encoding() -> CheckResult:
    r"""`\uXXXX` 로 이스케이프된 채 저장된 이름을 찾는다.

    AD 에서 삭제된(비활성) 계정에만 남아 있는 레거시라 자동 복원이 안전하다.
    활성 계정은 다음 AD sync 가 덮어쓰므로 손대지 않는다 (CLAUDE.md 규칙).
    """
    rows = fetch_all(
        "SELECT id, username, display_name, is_active FROM ad_users "
        f"WHERE {_ESCAPED_NAME_COND}"
    )
    inactive = [r for r in rows if not r["is_active"]]
    return CheckResult(
        not rows,
        f"이스케이프된 이름 {len(rows)}건 (비활성 {len(inactive)}건)",
        repairable=bool(inactive),
        repair_payload={"ids": [r["id"] for r in inactive]},
    )


def _repair_name_encoding(payload: dict) -> str:
    fixed = 0
    for uid in payload.get("ids", []):
        row = fetch_one("SELECT display_name FROM ad_users WHERE id = %s", (uid,))
        raw = (row or {}).get("display_name") or ""
        if "\\u" not in raw:
            continue
        try:
            decoded = raw.encode("utf-8").decode("unicode_escape")
        except Exception:
            continue
        # 복원 결과가 여전히 이스케이프거나 비어 있으면 건드리지 않는다
        if not decoded or "\\u" in decoded:
            continue
        execute("UPDATE ad_users SET display_name = %s WHERE id = %s", (decoded, uid))
        fixed += 1
    return f"비활성 계정 이름 {fixed}건 복원"


# ---- 권한 불변식 ----


def _check_fi_permission_enforced() -> CheckResult:
    """무권한 사용자의 FI SQL 이 실제로 차단되는지 — 방어선이 살아 있는지 확인."""
    from app.agents.sql_agent import _allowed_tables_from_sources
    from app.core.security import FI_ACCESS_DENIED_MESSAGE, validate_sql

    fi_sql = (
        "SELECT 1 FROM `skin1004-319714.Sales_Integration.FI_LLM_Flat` LIMIT 1"
    )
    blocked, err = validate_sql(fi_sql, allowed_tables=_allowed_tables_from_sources(None, False))
    allowed, _ = validate_sql(fi_sql, allowed_tables=_allowed_tables_from_sources(None, True))
    ok = (not blocked) and err == FI_ACCESS_DENIED_MESSAGE and allowed
    return CheckResult(ok, f"무권한 차단={not blocked} / 권한자 허용={allowed}")


def _check_fi_grant_count() -> CheckResult:
    row = fetch_one("SELECT COUNT(*) c FROM ad_users WHERE can_view_fi = 1")
    n = (row or {}).get("c", 0)
    # 인원이 바뀌는 것 자체는 정상이다. 0명이면 설정이 날아간 것이고,
    # 갑자기 대폭 늘면 사고다 — 둘 다 사람이 봐야 한다.
    return CheckResult(0 < n <= 30, f"FI 열람 허용 {n}명")


def _check_alert_channel() -> CheckResult:
    """알림 경로(잔디)가 실제로 열려 있는가.

    자가 점검이 아무리 잘 잡아도 알림이 안 나가면 소용이 없다. 실제로
    WAS 는 프록시 화이트리스트에 wh.jandi.com 이 없어 `Tunnel connection
    failed: 403` 이 났다 (2026-08-05). 탐지는 되는데 통보가 안 되는 상태였다.

    메시지를 보내지 않고 **터널만** 확인한다 — 매일 테스트 메시지를 쏘면
    그 자체가 소음이다. 엔드포인트가 GET 을 거절(4xx/5xx)해도 터널은 열린 것이다.
    """
    try:
        req = urllib.request.Request(JANDI_URL, method="GET")
        urllib.request.urlopen(req, timeout=8)
        return CheckResult(True, "알림 경로 정상")
    except urllib.error.HTTPError as e:
        # 응답 코드를 받았다 = 터널은 열렸다. 웹훅이 GET 을 거절한 것뿐.
        return CheckResult(True, f"알림 경로 정상 (엔드포인트 HTTP {e.code})")
    except Exception as e:
        return CheckResult(
            False,
            f"알림 전송 불가 — {type(e).__name__}: {str(e)[:120]}. "
            "이 상태에서는 검사가 실패해도 아무도 통보받지 못한다.",
        )


def _check_admin_exists() -> CheckResult:
    row = fetch_one("SELECT COUNT(*) c FROM users WHERE role = 'admin'")
    n = (row or {}).get("c", 0)
    return CheckResult(n >= 1, f"admin {n}명")


# ---- 데이터 소스 ----


def _check_bq_tables() -> CheckResult:
    from app.config import get_settings
    from app.core.bigquery import get_bigquery_client

    bq = get_bigquery_client()
    bad = []
    for tp in get_settings().allowed_tables:
        try:
            bq.execute_query(f"SELECT 1 AS ok FROM `{tp}` LIMIT 1", timeout=60.0, max_rows=1)
        except Exception as e:
            bad.append(f"{tp.split('.')[-1]}({str(e)[:40]})")
    return CheckResult(not bad, f"접근 불가 {len(bad)}개: {bad[:4]}" if bad else "전 테이블 접근 정상")


def _check_qdrant() -> CheckResult:
    from qdrant_client import QdrantClient

    from app.agents.qdrant_agent import COLLECTION, _qdrant_api_key, _qdrant_url

    cl = QdrantClient(url=_qdrant_url(), api_key=_qdrant_api_key(), timeout=20)
    cols = {c.name for c in cl.get_collections().collections}
    if COLLECTION not in cols:
        return CheckResult(False, f"기본 컬렉션 '{COLLECTION}' 없음 (있는 것: {sorted(cols)})")
    n = cl.get_collection(COLLECTION).points_count or 0
    return CheckResult(n > 0, f"{COLLECTION} {n} points / 컬렉션 {len(cols)}개")


# ---- 등록 ----

CHECKS: list[Check] = [
    Check("ad_sync_fresh", "batch", SEV_CRITICAL,
          "AD 동기화가 26시간 내 성공했는가", _check_ad_sync_fresh),
    Check("wiki_extract_fresh", "batch", SEV_WARNING,
          "위키 추출이 3시간 내 동작했는가", _check_wiki_extract_fresh),
    Check("quality_snapshot_fresh", "batch", SEV_WARNING,
          "품질 스냅샷이 2일 내 생성됐는가", _check_quality_snapshot_fresh),
    Check("job_heartbeats", "batch", SEV_CRITICAL,
          "모든 스케줄 잡이 제 주기 안에 성공했는가", _check_job_heartbeats),
    Check("orphan_user_groups_ad", "integrity", SEV_WARNING,
          "user_groups 가 실재하는 AD 사용자를 가리키는가", _check_orphan_user_groups_ad),
    Check("orphan_user_groups_grp", "integrity", SEV_WARNING,
          "user_groups 가 실재하는 그룹을 가리키는가", _check_orphan_user_groups_grp),
    Check("users_email_present", "integrity", SEV_WARNING,
          "users 에 이메일 누락이 없는가", _check_users_email),
    Check("name_encoding", "integrity", SEV_INFO,
          "이름이 이스케이프된 채 저장되지 않았는가", _check_name_encoding,
          repair=_repair_name_encoding),
    Check("fi_permission_enforced", "permission", SEV_CRITICAL,
          "FI 차단 방어선이 살아 있는가", _check_fi_permission_enforced),
    Check("fi_grant_count", "permission", SEV_WARNING,
          "FI 열람 허용 인원이 정상 범위인가", _check_fi_grant_count),
    Check("admin_exists", "permission", SEV_CRITICAL,
          "관리자 계정이 존재하는가", _check_admin_exists),
    Check("bq_tables", "datasource", SEV_CRITICAL,
          "허용된 BigQuery 테이블에 전부 접근되는가", _check_bq_tables),
    Check("qdrant", "datasource", SEV_CRITICAL,
          "Qdrant 기본 컬렉션에 데이터가 있는가", _check_qdrant),
]


# ── 실행 ──────────────────────────────────────────────────────────────────────


def _previous_state() -> dict[str, bool]:
    """직전 실행의 check_id → ok. 상태가 바뀐 것만 알리기 위해 쓴다."""
    prev = fetch_one("SELECT id FROM self_check_runs ORDER BY id DESC LIMIT 1")
    if not prev:
        return {}
    rows = fetch_all(
        "SELECT check_id, ok FROM self_check_results WHERE run_id = %s", (prev["id"],)
    )
    return {r["check_id"]: bool(r["ok"]) for r in rows}


def _notify(title: str, body: str, color: str) -> bool:
    try:
        data = json.dumps({
            "body": body,
            "connectColor": color,
            "connectInfo": [{"title": title,
                             "description": datetime.now().strftime("%Y-%m-%d %H:%M")}],
        }).encode("utf-8")
        req = urllib.request.Request(
            JANDI_URL, data=data, method="POST",
            headers={"Accept": "application/vnd.tosslab.jandi-v2+json",
                     "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception as e:
        # 조용히 삼키면 "알림이 안 온다 = 문제가 없다"로 오해하게 된다
        logger.error("self_check_notify_failed", error=str(e)[:200],
                     hint="프록시 화이트리스트에 wh.jandi.com 이 있는지 확인")
        return False


def run_self_check(auto_repair: bool = True, notify: bool = True) -> dict:
    """전체 자가 점검 실행 → 저장 → (선택) 자가치유 → (선택) 알림.

    Returns:
        요약 dict — API/스케줄러가 그대로 쓴다.
    """
    ensure_self_check_tables()
    started = datetime.now()
    prev = _previous_state()

    results = []
    for chk in CHECKS:
        try:
            res = chk.fn()
        except Exception as e:
            res = CheckResult(False, f"검사 자체 실패: {type(e).__name__}: {str(e)[:120]}")
            logger.warning("self_check_error", check=chk.id, error=str(e)[:200])

        repaired, note = False, ""
        if auto_repair and not res.ok and res.repairable and chk.repair:
            try:
                note = chk.repair(res.repair_payload)
                # 고친 뒤 다시 확인 — 정말 나았는지 본다
                res2 = chk.fn()
                repaired = res2.ok
                if repaired:
                    res = CheckResult(True, f"{res.detail} → 자가치유: {note}")
                else:
                    note = f"{note} (치유 후에도 미해결)"
            except Exception as e:
                note = f"치유 실패: {str(e)[:120]}"
                logger.warning("self_check_repair_failed", check=chk.id, error=str(e)[:200])

        results.append((chk, res, repaired, note))

    passed = sum(1 for _, r, _, _ in results if r.ok)
    failed = len(results) - passed
    repaired_n = sum(1 for _, _, rp, _ in results if rp)
    dur = int((datetime.now() - started).total_seconds() * 1000)

    run_id = execute_lastid(
        "INSERT INTO self_check_runs (total, passed, failed, repaired, duration_ms) "
        "VALUES (%s, %s, %s, %s, %s)",
        (len(results), passed, failed, repaired_n, dur),
    )
    for chk, res, rp, note in results:
        execute(
            "INSERT INTO self_check_results "
            "(run_id, check_id, category, severity, ok, detail, repaired, repair_note) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, chk.id, chk.category, chk.severity, 1 if res.ok else 0,
             res.detail[:2000], 1 if rp else 0, (note or "")[:1000]),
        )

    # 상태가 바뀐 것만 알린다 — 매일 같은 알림은 곧 무시당한다
    newly_broken = [(c, r) for c, r, _, _ in results
                    if not r.ok and prev.get(c.id, True)]
    recovered = [c for c, r, _, _ in results if r.ok and prev.get(c.id) is False]

    from app.config import get_settings
    if notify and get_settings().self_check_notify and (newly_broken or recovered):
        lines = []
        if newly_broken:
            lines.append("*새로 실패한 검사*")
            for c, r in newly_broken:
                mark = "🔴" if c.severity == SEV_CRITICAL else "🟠"
                lines.append(f"{mark} [{c.category}] {c.description}\n    → {r.detail}")
        if recovered:
            lines.append("\n*복구된 검사*")
            for c in recovered:
                lines.append(f"🟢 {c.description}")
        if repaired_n:
            lines.append(f"\n자가치유 {repaired_n}건")
        color = "#d93636" if any(c.severity == SEV_CRITICAL for c, _ in newly_broken) else "#e89200"
        if not newly_broken:
            color = "#2ecc71"
        _notify(f"Cella 자가점검 — {passed}/{len(results)} 통과", "\n".join(lines), color)

    logger.info("self_check_completed", run_id=run_id, passed=passed, failed=failed,
                repaired=repaired_n, duration_ms=dur,
                newly_broken=[c.id for c, _ in newly_broken])

    return {
        "run_id": run_id,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "repaired": repaired_n,
        "duration_ms": dur,
        "newly_broken": [c.id for c, _ in newly_broken],
        "recovered": [c.id for c in recovered],
        "results": [
            {"check_id": c.id, "category": c.category, "severity": c.severity,
             "description": c.description, "ok": r.ok, "detail": r.detail,
             "repaired": rp, "repair_note": note}
            for c, r, rp, note in results
        ],
    }


def get_latest_self_check(limit_history: int = 14) -> dict:
    """최근 실행 결과 + 추세 (관리자 화면용)."""
    ensure_self_check_tables()
    run = fetch_one(
        "SELECT id, run_at, total, passed, failed, repaired, duration_ms "
        "FROM self_check_runs ORDER BY id DESC LIMIT 1"
    )
    if not run:
        return {"run": None, "results": [], "history": []}
    results = fetch_all(
        "SELECT check_id, category, severity, ok, detail, repaired, repair_note "
        "FROM self_check_results WHERE run_id = %s "
        "ORDER BY ok ASC, FIELD(severity,'critical','warning','info')",
        (run["id"],),
    )
    history = fetch_all(
        "SELECT id, run_at, total, passed, failed, repaired "
        "FROM self_check_runs ORDER BY id DESC LIMIT %s",
        (limit_history,),
    )
    desc = {c.id: c.description for c in CHECKS}
    for r in results:
        r["description"] = desc.get(r["check_id"], r["check_id"])
        r["ok"] = bool(r["ok"])
        r["repaired"] = bool(r["repaired"])
    return {"run": run, "results": results, "history": history}


def get_check_trend(check_id: str, limit: int = 30) -> list[dict]:
    """특정 검사의 이력 — '언제부터 깨졌나'를 답하기 위한 것."""
    return fetch_all(
        "SELECT r.run_at, s.ok, s.detail, s.repaired FROM self_check_results s "
        "JOIN self_check_runs r ON s.run_id = r.id "
        "WHERE s.check_id = %s ORDER BY r.id DESC LIMIT %s",
        (check_id, limit),
    )
