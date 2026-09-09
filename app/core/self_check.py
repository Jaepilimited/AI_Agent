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
import time
from datetime import datetime, timedelta, timezone
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
    "entra_directory_sync": (26, "Entra 사용자 동기화 (APP 서버 22:00)"),
    "knowledge_map_build": (26, "지식맵 빌드 (WAS 03:00)"),
    "ingredient_sync_daily": (26, "제품 전성분 적재 (04:00)"),
    "op_inventory_sync_daily": (26, "OP 재고 시트 적재 (11:20·16:20)"),
    # ⚠️ 매시 잡이지만 여유를 3시간 준다 — 한 번 걸렀다고 경보를 울리면 소음이 된다
    "cs_cache_hourly": (3, "CS/BP 제품 Q&A 시트 재로딩 (매시 :40)"),
    "product_info_sync_daily": (26, "제품정보(노션 제품 스펙) 적재 (04:20)"),
    "golden_daily": (26, "골든셋 회귀 (05:30)"),
    "model_rights_sync_daily": (26, "모델 초상권 적재 (04:30)"),
    "feedback_digest_daily": (26, "붐따 처리함 다이제스트 (08:00)"),
    "briefing_daily": (26, "개인화 데일리 브리핑 (08:20)"),
    "personal_briefing_daily": (26, "출근 브리핑 생성 + 잔디 대기열 (07:30)"),
    "saved_questions_daily": (26, "저장 질문 자동 실행 (출근 브리핑 직전)"),
    # ⚠️ 근무일 09~18시에만 돈다 — 주말·야간을 고장으로 세지 않도록 넉넉히 잡는다
    "jandi_notify_hourly": (80, "셀라 알림 → 잔디 대기열 (평일 08~18시 :25)"),
    "query_profile_daily": (26, "질문 프로필 갱신 (03:20)"),
    "schema_docs_daily": (26, "정의서 → BigQuery 컬럼 설명 (03:40)"),
    "value_lists_daily": (26, "컬럼 값 목록 실측 갱신 (03:50)"),
    "ad_media_snapshot_daily": (14, "광고 매체 목록 스냅샷 (04:20·18:20)"),
    "notion_push_halfhourly": (2, "브리핑 노션 대기열 발송 (30분마다)"),
    "awards_sync_daily": (26, "수상/랭킹 시트 적재 (04:40)"),
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
    row = fetch_one("SELECT succeeded_at,error_message FROM directory_sync_state WHERE id=1")
    if row and row.get("error_message"):
        return CheckResult(False, row["error_message"])
    last = row and row.get("succeeded_at")
    if not last:
        return CheckResult(False, "Entra 전체 사용자 동기화 대기 — Graph 읽기 권한과 네트워크를 확인해 주세요.")
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


# 하루 재시작 허용치. 실측 근거 (2026-08-24):
#   정상일  8/10 2건 · 8/11 26건 · 8/12 4건
#   사고중  하루 약 5,300건 (8/13~8/24)
# 50 이면 가장 바빴던 정상일의 2배, 사고의 1/100 — 사이가 넓어 오탐도 미탐도 어렵다.
#: 그 시간대를 "재시작이 있었다" 로 칠 최소 횟수. 1~2회는 배포·수동 재기동이다.
_RESTART_LOOP_HOURLY_MIN = 3
#: 최근 12시간 중 이만큼의 시간대에서 재시작이 이어지면 사람 손으로는 설명되지 않는다.
_RESTART_LOOP_BUSY_HOURS = 8


def _check_restart_loop() -> CheckResult:
    """앱이 재시작을 **반복**하고 있지 않은가 — `llm_usage` 로 판정한다.

    `main._warmup_llm_clients()` 가 기동할 때마다 Gemini·Claude 에 `"hi"` 를 한 번씩
    보낸다. 그래서 **입력 토큰이 극히 작은 호출 수 = 재시작 횟수**다.

    ⛔ 이 검사를 만든 이유 — 2026-08-13~24, PM2 밖 고아 프로세스가 3000/3001 을
       점유해 PM2 프로세스가 **56,833회** 재시작했다. 고아가 계속 200 을 응답해서
       **화면은 멀쩡했고** 아무도 몰랐다.

    ⛔ **하루 총량으로 재지 않는다** (2026-08-27 변경). 배포가 많은 날이면 그것만으로
       상한을 넘는다 — 실제로 개발 이틀에 24시간 55회가 찍혀 경보가 울렸는데,
       시간대를 보니 **업무 시간(08~16시)에만 몰려 있고 밤새 0회**였다. 배포다.
       할 일이 없는데 울리는 알림은 곧 무시당한다 (이 파일이 스스로 세운 규칙이다).

    ⚠️ 크래시 루프와 배포를 가르는 것은 **양이 아니라 끈질김**이다. 배포는 사람이
       일하는 동안 몰렸다 그친다. 루프는 쉬지 않는다 (예전 사고는 11일간 시간당 9회).
       그래서 **재시작이 있었던 시간대의 수**를 본다 — 최근 12시간 중 8시간 이상에서
       재시작이 관측되면 사람이 붙어 있는 배포로는 설명되지 않는다.
    """
    try:
        # ⛔ `DATE_FORMAT(ts, '%Y-%m-%d %H')` 을 쓰지 마라 — pymysql 이 `%` 를 포맷
        #    지시자로 읽어 터진다. 그러면 아래 `except` 가 삼켜 **검사가 영원히
        #    "판정 보류" 로 통과한다** (2026-08-27 실제로 그렇게 넣었다가 잡았다).
        #    `DATE()`·`HOUR()` 로 나누면 `%` 가 아예 없다.
        rows = fetch_all(
            "SELECT DATE(ts) d, HOUR(ts) h, COUNT(*) c FROM llm_usage "
            "WHERE provider = 'claude' AND input_tokens BETWEEN 1 AND 20 "
            "AND ts >= DATE_SUB(NOW(), INTERVAL 12 HOUR) GROUP BY d, h"
        ) or []
    except Exception as e:  # 계측 테이블이 아직 없을 수 있다
        return CheckResult(True, f"llm_usage 조회 불가 (판정 보류): {str(e)[:80]}")

    busy_hours = sum(1 for row in rows if int(row.get("c") or 0) >= _RESTART_LOOP_HOURLY_MIN)
    total = sum(int(row.get("c") or 0) for row in rows)
    if busy_hours >= _RESTART_LOOP_BUSY_HOURS:
        return CheckResult(
            False,
            f"최근 12시간 중 {busy_hours}시간에서 재시작이 이어졌다 (총 {total}회) — "
            "크래시 루프 의심. 포트를 점유한 비-PM2 프로세스부터 확인할 것 "
            "(pm2 ↺ 카운터 · netstat 포트 소유 PID)",
        )
    return CheckResult(True, f"최근 12시간 재시작 {total}회 · 이어진 시간대 {busy_hours}")


# ---- DB 무결성 ----


def _orphan(sql: str, label: str) -> CheckResult:
    row = fetch_one(sql)
    n = (row or {}).get("c", 0)
    return CheckResult(n == 0, f"{label} 고아 {n}건")


def _check_orphan_user_groups_ad() -> CheckResult:
    return _orphan(
        "SELECT COUNT(*) c FROM user_groups ug "
        "LEFT JOIN directory_users a ON ug.ad_user_id = a.id WHERE a.id IS NULL",
        "user_groups→directory_users",
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
    r"""`\uXXXX` 로 이스케이프된 채 저장된 이름을 찾는다. **두 표를 다 본다.**

    ⛔ 예전엔 `directory_users` 만 봤다. 그런데 로그인 자동완성이 실제로 그리는 값은
       `COALESCE(u.display_name, ad.display_name)` 이라 **`users` 쪽이 이긴다** —
       `directory_users` 가 멀쩡해도 `users` 가 깨져 있으면 사람은 자기 이름을 못 찾고,
       그 행을 클릭하지 못하면 `auth.js` 가 프론트에서 막아 **서버 기록이 아예 없다**.
       2026-09-01 이주원 님(users.id=68)이 그랬고 2개월간 검사 밖이었다.

    치유 범위가 표마다 다르다:
      - `directory_users` : **비활성만**. 활성은 다음 AD sync 가 덮어쓴다 (CLAUDE.md 규칙)
      - `users`    : **전부**. 덮어써 주는 것이 없어 여기서 안 고치면 영영 그대로다
    """
    ad_rows = fetch_all(
        "SELECT id, username, display_name, is_active FROM directory_users "
        f"WHERE {_ESCAPED_NAME_COND}"
    )
    user_rows = fetch_all(
        "SELECT id, display_name, email FROM users "
        f"WHERE {_ESCAPED_NAME_COND}"
    )
    inactive = [r for r in ad_rows if not r["is_active"]]
    total = len(ad_rows) + len(user_rows)
    detail = (
        f"이스케이프된 이름 {total}건 "
        f"(users {len(user_rows)}건 · directory_users {len(ad_rows)}건 중 비활성 {len(inactive)}건)"
    )
    if user_rows:
        # ⚠️ 어느 계정인지 적는다 — 로그인 화면에서만 드러나는 결함이라
        #    "몇 건" 만으로는 누구를 도와야 하는지 알 수 없다.
        detail += " · users id=" + ",".join(str(r["id"]) for r in user_rows)
    return CheckResult(
        not (ad_rows or user_rows),
        detail,
        repairable=bool(inactive or user_rows),
        repair_payload={
            "ids": [r["id"] for r in inactive],
            "user_ids": [r["id"] for r in user_rows],
        },
    )


def _decode_escaped(raw: str) -> str | None:
    r"""`이주원` → `이주원`. 되돌릴 수 없는 쓰기라 **확신할 때만** 값을 낸다.

    ⛔ 디코딩 결과가 비었거나 여전히 이스케이프면 None 이다 — 덮어쓰면 더 나쁜 값이
       굳고, 원본이 사라져 되돌릴 수도 없다.
    """
    text = raw or ""
    if "\\u" not in text:
        return None
    try:
        decoded = text.encode("utf-8").decode("unicode_escape")
    except Exception:
        return None
    if not decoded or "\\u" in decoded:
        return None
    return decoded


def _repair_name_encoding(payload: dict) -> str:
    fixed = 0
    for uid in payload.get("ids", []):
        row = fetch_one("SELECT display_name FROM directory_users WHERE id = %s", (uid,))
        decoded = _decode_escaped((row or {}).get("display_name") or "")
        if decoded is None:
            continue
        execute("UPDATE directory_users SET display_name = %s WHERE id = %s", (decoded, uid))
        fixed += 1
    # `users` 는 활성 여부와 무관하게 고친다 — 덮어써 주는 sync 가 없다.
    healed = 0
    for uid in payload.get("user_ids", []):
        row = fetch_one("SELECT display_name FROM users WHERE id = %s", (uid,))
        decoded = _decode_escaped((row or {}).get("display_name") or "")
        if decoded is None:
            continue
        execute("UPDATE users SET display_name = %s WHERE id = %s", (decoded, uid))
        healed += 1
    return f"AD 비활성 계정 {fixed}건 · 가입 사용자 {healed}건 이름 복원"


# ---- 권한 불변식 ----


def _check_auth_config() -> CheckResult:
    """로그인이 **가능한 상태**인가 — 인증 설정이 유효한지 확인한다.

    ⛔ 2026-08-21: `JWT_SECRET_KEY` 를 필수로 만드는 변경이 들어왔다. 좋은 변경이지만
       `.env` 는 **배포 대상이 아니라서**, 서버 값을 먼저 채우지 않고 코드만 올리면
       그 순간부터 전 직원의 로그인·모든 인증 요청이 RuntimeError 로 죽는다.
       그런데 `/health` 는 인증을 안 타므로 배포는 **성공한 것처럼 보인다** —
       사용자가 로그인을 시도해야만 드러난다. 그 구간을 없앤다.

    ⚠️ 값 자체는 절대 로그에 남기지 않는다. 길이와 판정만 남긴다.
    """
    from app.config import get_settings, validate_jwt_secret

    s = get_settings()
    try:
        key = validate_jwt_secret(s.jwt_secret_key)
    except Exception as e:
        return CheckResult(False, f"인증 설정 무효 — 로그인 불가: {str(e)[:120]}")

    # 실제로 토큰을 만들고 되읽어 본다. 설정만 보고 통과시키면 알고리즘·라이브러리
    # 문제를 놓친다 (검증은 "될 것 같다" 가 아니라 "된다" 여야 한다)
    try:
        import jwt as _jwt
        tok = _jwt.encode({"sub": "self-check"}, key, algorithm="HS256")
        back = _jwt.decode(tok, key, algorithms=["HS256"])
        if back.get("sub") != "self-check":
            return CheckResult(False, "토큰을 되읽었는데 내용이 다르다")
    except Exception as e:
        return CheckResult(False, f"토큰 발급·검증 실패: {type(e).__name__}: {str(e)[:100]}")

    return CheckResult(True, f"인증 설정 정상 (키 {len(key)}자)")


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
    row = fetch_one("SELECT COUNT(*) c FROM directory_users WHERE can_view_fi = 1")
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


def _check_logistics_quantity_outlier() -> CheckResult:
    """수출 물류 `quantity_ea` 에 물리적으로 불가능한 값이 있는가.

    ⛔ **실측 사고 (2026-09-03)**: 코스타리카 1건의 `quantity_ea` 에
       `202602190028` 이 들어 있었다 — 주문번호(`YYYYMMDDnnnn`)를 수량 칸에 잘못
       적은 것이다. 그 한 행 때문에 **2026년 총 수출수량이 58,038,175 →
       202,660,228,203 (3,492배)** 로 나갔고, 채팅은 아무 경고 없이
       "총 202,602,265,193개" 라고 답했다. **에러가 아니라 조용한 오답이다.**

    ⚠️ 판정은 `logistics_quality.outlier_rows()` **한 곳**이 한다 — 답변에 붙는
       공시와 같은 함수여야 화면과 답변이 어긋나지 않는다.
    ⚠️ 고치는 것은 우리가 아니다 — **원본(물류관리 시스템)의 셀**이다.
       여기서는 조용히 지나가지 않게 **매일 보이게** 만드는 것까지만 한다.
    """
    from app.core.logistics_quality import outlier_rows

    rows = outlier_rows(force=True)
    if not rows:
        return CheckResult(True, "수출 물류 수량 이상치 없음")
    bad = ", ".join(
        f"{r.get('order_date')} {r.get('country')} "
        f"{r.get('order_number')}={int(r.get('quantity_ea') or 0):,}ea"
        for r in rows[:3])
    return CheckResult(
        False,
        f"수량 칸에 주문번호로 보이는 값 {len(rows)}건 — 합계가 통째로 틀린다: {bad}"
        + (" 외" if len(rows) > 3 else ""))


def _check_notion_unshared() -> CheckResult:
    """노션에 걸어 뒀는데 **학습되지 않은** 자료가 새로 생겼는가.

    ⛔ 실제 사고 (2026-09-04): 제품 라인업 DB 를 DB-HUB 에 걸었는데 학습되지 않았고
       **사람이 물어봐서야** 알았다. 파이프라인은 매일 `404스킵=20` 을 찍고 있었지만
       로그는 읽는 사람이 없으면 없는 것과 같다.
    ⛔ **총 건수로 매일 울리지 않는다** — 밀린 20건 때문에 경보가 소음이 된다.
       새로 생긴 것만 실패로 올린다 ("방금 건 자료가 안 들어왔다" 가 진짜 신호다).
    """
    from app.core.notion_watch import as_lines, newly_unshared

    r = newly_unshared()
    if not r["known"]:
        return CheckResult(True, "미학습 기록 없음 (파이프라인이 아직 안 돌았다)")
    if not r["new"]:
        return CheckResult(True, f"새로 생긴 미학습 자료 없음 (밀린 것 {r['total']}건)")
    return CheckResult(
        False,
        f"학습되지 않은 새 자료 {len(r['new'])}건 — Skin1004_AI 연결 필요: "
        + " / ".join(as_lines(r["new"], limit=3)))


def _check_data_freshness() -> CheckResult:
    """파생 사본이 원본을 따라가고 있는가 (`app/core/data_freshness.py`).

    ⛔ **"잡이 돌았다" 와 다르다.** 잡이 성공해도 원본을 못 읽었을 수 있고, 애초에
       잡이 없을 수도 있다 — CS 캐시가 그랬다 (2026-09-03). 데이터 자신의 시각을 본다.
    ⚠️ 나이가 아니라 **뒤처짐**을 본다. 원본이 안 바뀌었으면 사본이 오래돼도 정상이다.
    """
    from app.core.data_freshness import check

    readings = check()
    bad = [r for r in readings if not r.ok]
    if not bad:
        return CheckResult(True, f"파생 사본 {len(readings)}종 모두 최신")
    detail = " / ".join(f"{r.name}: {r.detail}" for r in bad[:3])
    return CheckResult(False, f"뒤처진 사본 {len(bad)}/{len(readings)}종 — {detail}")


def _check_freshness_coverage() -> CheckResult:
    """사용자에게 답하는 소스 중 **신선도 감시가 안 붙은 것**이 있는가.

    ⛔ 이것이 "감시를 붙이는 걸 잊었다" 를 잡는 층이다. CS 캐시는 **잡이 아예 없어서**
       `EXPECTED_JOBS` 로는 구조적으로 못 잡혔다 — 없는 잡은 빠졌다고 말할 수 없다.
       `@@` 등록부를 기준으로 대조하므로, 새 소스를 붙이고 감시를 안 붙이면 그날 걸린다.
    """
    from app.core.data_freshness import SOURCES, coverage_gaps

    gaps = coverage_gaps()
    if gaps:
        return CheckResult(
            False,
            f"신선도 감시가 없는 소스 {gaps} — data_freshness.SOURCES 에 등록하거나 "
            f"사본이 아니면 NOT_A_COPY 에 이유와 함께 적을 것")
    return CheckResult(True, f"감시 등록 {len(SOURCES)}종 · 빠진 소스 없음")


def _check_cs_cache() -> CheckResult:
    """CS/BP 제품 Q&A 캐시가 살아 있고 최신인가.

    ⛔ 잡이 돌았다는 것과 **캐시가 채워져 있다**는 것은 다르다. 권한이 끊기거나
       탭 이름이 바뀌면 `refresh()` 가 0건을 받고 옛 캐시를 지킨다 — 잡은 실패로
       기록되지만, 캐시 자체가 얼마나 낡았는지는 여기서만 보인다.
    ⚠️ 이 검사는 **앱 프로세스 안에서** 돌 때만 뜻이 있다 (모듈 캐시라서).
    """
    from app.agents.cs_agent import status

    st = status()
    if not st["loaded"] or not st["count"]:
        return CheckResult(False, "CS Q&A 캐시가 비어 있다 — 시트 권한·탭 이름 확인")
    age = st["age_seconds"]
    if age is None:
        return CheckResult(True, f"CS Q&A {st['count']}건 (적재 시각 미기록)")
    hours = age / 3600.0
    return CheckResult(
        hours <= 3,
        f"CS Q&A {st['count']}건 · {int(hours)}시간 전 적재"
        + ("" if hours <= 3 else " — 매시 갱신이 멈췄다"))


def _check_qdrant() -> CheckResult:
    from qdrant_client import QdrantClient

    from app.agents.qdrant_agent import COLLECTION, _qdrant_api_key, _qdrant_url

    cl = QdrantClient(url=_qdrant_url(), api_key=_qdrant_api_key(), timeout=20)
    cols = {c.name for c in cl.get_collections().collections}
    if COLLECTION not in cols:
        return CheckResult(False, f"기본 컬렉션 '{COLLECTION}' 없음 (있는 것: {sorted(cols)})")
    n = cl.get_collection(COLLECTION).points_count or 0
    return CheckResult(n > 0, f"{COLLECTION} {n} points / 컬렉션 {len(cols)}개")


def _check_notion_allowlist() -> CheckResult:
    """허용 목록의 노션 페이지를 인테그레이션이 **실제로 볼 수 있는가**.

    ⛔ 안 보이는 페이지는 조용히 목록에서 빠진다 — 사용자는 "그 문서 얘기는 못 한다"는
       사실을 모른 채 빈 답을 받는다. 로그에는 `notion_warmup_fetch_failed` 가 남지만
       **7일간 228회 반복**되는 동안 아무도 못 봤다 (2026-08-18 로그 분석).
       10개 중 2개(네이버 스마트스토어 운영방법 · 네이버 브랜드스토어 업무 공유)가
       404 였고, 두 페이지 모두 **살아 있다** — 인테그레이션에 공유가 안 된 것이다.

    반복 로그가 아니라 **상시 상태**로 보여야 누군가 고친다. 고치는 방법은 코드가
    아니라 노션 UI 다 (페이지 → 연결 → 인테그레이션 추가) — 그래서 자가 점검이 맡는다.
    """
    import httpx

    from app.agents.notion_agent import _ALLOWED_PAGES
    from app.config import get_settings

    tok = get_settings().notion_mcp_token
    if not tok:
        return CheckResult(False, "NOTION_MCP_TOKEN 미설정")
    headers = {"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28"}
    blocked = []
    with httpx.Client(timeout=20, headers=headers) as cl:
        for entry in _ALLOWED_PAGES:
            pid = entry["id"]
            kind = "databases" if entry.get("type") == "database" else "pages"
            try:
                r = cl.get(f"https://api.notion.com/v1/{kind}/{pid}")
                if r.status_code == 404:
                    blocked.append(entry.get("description") or pid[:8])
            except Exception as e:
                return CheckResult(False, f"조회 실패: {str(e)[:80]}")
    total = len(_ALLOWED_PAGES)
    if blocked:
        return CheckResult(
            False,
            f"{len(blocked)}/{total}개 접근 불가 — {', '.join(blocked)} "
            f"(노션에서 해당 페이지 → 연결 → 인테그레이션 추가 필요)")
    return CheckResult(True, f"{total}개 전부 접근 가능")


# ── 프로덕션 로그의 **새 에러 유형** ────────────────────────────────────────
# ⛔ 이걸 하려고 만든 `SKIN1004-Nightly-Debug` 가 있었지만 **7/09 부터 멈춰 있었고**
#    로그·재기동·헬스체크가 전부 구서버(pm2 skin1004-prod, logs/pm2-prod-error.log,
#    127.0.0.1:3000)를 향하고 있었다. 게다가 LLM 이 만든 diff 를 프로덕션에
#    **자동 적용**하는 구조라 되살리기에 위험이 컸다.
#    실제로 오늘(2026-08-18) 일주일치 로그를 사람이 훑어 여섯 종을 찾았다 —
#    자동 수정이 아니라 **읽히는 것**이 값이었다. 그 부분만 여기로 옮긴다.
_LOG_ERROR_BASELINE_HOURS = 24 * 7


def _check_new_log_errors() -> CheckResult:
    """어제 로그에 **직전 주에 없던 에러 유형**이 있는가.

    ⚠️ 절대량이 아니라 **새로 나타난 종류**만 본다 — 늘 나던 에러를 매일 알리면
       곧 무시당한다 (붐따 다이제스트·자가 점검 알림과 같은 판단).
    """
    import json
    import subprocess
    from collections import Counter

    def _events(since: str, until: str = "") -> Counter:
        cmd = ["journalctl", "-u", "ai-craver", "--since", since, "--no-pager", "-o", "cat"]
        if until:
            cmd += ["--until", until]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=90).stdout
        except Exception:
            return Counter()
        found = Counter()
        for line in out.splitlines():
            i = line.find("{")
            if i < 0:
                continue
            try:
                d = json.loads(line[i:])
            except Exception:
                continue
            if str(d.get("level", "")).lower() in ("error", "critical"):
                found[d.get("event", "?")] += 1
        return found

    recent = _events("1 day ago")
    baseline = _events(f"{_LOG_ERROR_BASELINE_HOURS // 24 + 1} days ago", "1 day ago")
    if not recent and not baseline:
        return CheckResult(True, "최근 에러 로그 없음 (또는 journal 접근 불가)")
    fresh = {e: n for e, n in recent.items() if e not in baseline}
    if fresh:
        top = ", ".join(f"{e}({n})" for e, n in sorted(fresh.items(), key=lambda kv: -kv[1])[:5])
        return CheckResult(False, f"새 에러 유형 {len(fresh)}종: {top}")
    return CheckResult(True, f"신규 에러 유형 없음 (어제 {sum(recent.values())}건 / 직전주 {sum(baseline.values())}건)")


def _check_ad_media_missing() -> CheckResult:
    """광고 **매체가 통째로 사라졌는가** (2026-08-31 실측으로 만든 검사).

    ⛔ 실제로 하루 사이에 `KakaoMoments` 가 사라졌다. 오전엔 최신 8/27·594,843원이
       있었고 오후엔 매체 19종 어디에도 없었다. 셀라는 두 번 다 그 시점의 사실을
       답했지만, 사람이 두 답을 나란히 놓기 전까지 아무도 몰랐다 — 에러가 없다.
    ⚠️ `schema_watch` 는 테이블·컬럼을 보고, 점검 감지는 전체 행 수를 본다.
       **작은 매체가 통째로 빠지는 것은 둘 다 못 잡는다.**
    """
    from app.core.ad_media_watch import run, summarize
    ok, detail = summarize(run())
    return CheckResult(ok, detail)


def _check_awards_unknown_usage() -> CheckResult:
    """새 활용 표기(`usage_flag`)가 조용히 늘어나는 것을 사람이 보게 한다.

    ⛔ 뜻풀이(`USAGE_LEGEND`)에 없는 표기가 들어오면 `format_answer` 가 그 표기를
       설명 없이 그대로 내보낸다 — 사람이 봐야 새 표기인지 오타인지 판단할 수 있다.
    """
    from app.core.awards import USAGE_LEGEND
    rows = fetch_all("SELECT DISTINCT usage_flag f FROM awards_rankings") or []
    unknown = sorted({(r["f"] or "").strip() for r in rows} - set(USAGE_LEGEND))
    if unknown:
        return CheckResult(False, f"미등록 활용 표기: {unknown}")
    return CheckResult(True, "표기 전부 등록됨")


def _check_awards_sheet_freshness() -> CheckResult:
    """수상/랭킹 시트가 안 바뀐 것인지, 우리가 못 읽은 것인지 가른다.

    ⚠️ 잡이 돌았다는 것과 데이터가 있다는 것은 다르다 — 0건이면 먼저 그것부터 말한다
       (권한 만료·탭 이름 변경이 정확히 이렇게, 에러 없이 온다).
    """
    row = fetch_one("SELECT MAX(synced_at) s, COUNT(*) n FROM awards_rankings") or {}
    n = int(row.get("n") or 0)
    if not n:
        return CheckResult(False, "적재된 수상/랭킹 행이 0건 — 권한·탭 이름을 확인할 것")
    stamp = row.get("s")
    age_h = (datetime.now() - stamp).total_seconds() / 3600 if stamp else 999
    from app.core.awards import FRESHNESS_MAX_HOURS
    return CheckResult(age_h <= FRESHNESS_MAX_HOURS,
                       f"{n}행 · 마지막 적재 {age_h:.0f}시간 전")


def _check_schema_changes() -> CheckResult:
    """어제 대비 **앱이 쓰는 테이블**의 스키마가 바뀌었는가.

    ⛔ 리뷰 테이블이 국내/해외/매장으로 통합됐는데 앱이 **한 달 넘게 몰랐다**
       (2026-08-18 이주훈 님 제보로 발견). 에러 없이 숫자만 작게 나와서
       아무도 못 알아챘다 — "국내몰 리뷰" 가 42,427건 중 4,140건만 셌다.
    ⚠️ 화이트리스트 밖 변화는 실패로 올리지 않는다. 프로젝트 전체는 3,500개가
       넘어(백업·테스트·중간 산출물) 매일 뜨면 소음이 된다.
    """
    from app.core.schema_watch import run as _watch
    r = _watch()
    if not r.get("ok"):
        return CheckResult(False, str(r.get("detail"))[:150])
    if r.get("baseline"):
        return CheckResult(True, str(r.get("detail")))
    watched = r.get("watched") or []
    total = r.get("total", 0)
    if watched:
        return CheckResult(
            False,
            f"앱이 쓰는 테이블 변경 {len(watched)}건 — " + " / ".join(watched[:4])
            + (f" 외 {len(watched)-4}건" if len(watched) > 4 else "")
            + " (프롬프트·화이트리스트 반영 필요)")
    return CheckResult(True, f"화이트리스트 변화 없음 (전체 변화 {total}건)")


def _check_value_lists() -> CheckResult:
    """프롬프트에 들어가는 값 목록이 최신인가.

    ⛔ 캐시가 비면 자리표시자가 **빈 줄로 사라진다.** 목록이 통째로 없으면 LLM 이
       값을 지어내므로(에콰도르 사고와 같은 부류), 비었는지를 반드시 감시한다.
    """
    from app.core.value_lists import status
    st = status()
    if st["missing"]:
        return CheckResult(False, f"값 목록 {len(st['missing'])}개 비어 있음: "
                                  + ", ".join(st["missing"][:6]))
    if st["stale"]:
        return CheckResult(False, f"값 목록이 낡았다 (마지막 갱신 {st['updated_at'][:16]})")
    return CheckResult(True, f"{st['cached']}/{st['total']}개 최신 ({st['updated_at'][:16]})")


# ---- 답변 품질 (canary) ----


_CANARY_QUESTIONS = [
    # (라벨, 질문, 허용 시간 s, 최소 길이) — 내용 정답성이 아니라 **구조적 건강**만 본다
    # 최소 길이는 질문별이다: "오늘 며칠이야?" 의 정답은 원래 ~30자라
    # 일괄 50자 기준을 걸었더니 정상 답변이 "너무 짧음" 오탐으로 잡혔다 (2026-08-06 첫 실행).
    ("bigquery", "2026년 상반기 국가별 매출 top3 알려줘", 90.0, 150),
    ("direct", "오늘 며칠이야?", 45.0, 10),
]

_CANARY_ERROR_PHRASES = ("오류가 발생", "시간 초과", "오래 걸리고", "일시적으로 불안정")


def _check_canary_answers() -> CheckResult:
    """대표 질문을 실제 API 로 돌려 답변 경로가 구조적으로 온전한지 본다.

    👎 피드백의 잘림·타임아웃 유형은 간헐적이라 사후 재현이 안 됐다 (2026-08-06,
    4건 모두 재현 실패). 매일 같은 질문을 돌려두면 재발한 **시점**이 기록에 남아
    "언제부터 깨졌나"를 답할 수 있다. 검사 항목: 오류 문구 / 앞부분 유실
    (문장 중간에서 시작) / 코드블록 미닫힘 / 빈 답변 / 응답 시간.
    내용이 맞는지는 보지 않는다 — LLM 표현 변동으로 오탐이 나기 때문이다.
    """
    import httpx as _httpx

    from app.config import get_settings
    from app.core.session_auth import create_service_token

    s = get_settings()
    adm = fetch_one("SELECT id, email, role FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    if not adm:
        return CheckResult(False, "admin 계정이 없어 카나리아를 돌릴 수 없다")
    token = create_service_token(
        adm["id"], adm["email"], role="admin",
        service="self_check", lifetime_seconds=900,
    )

    problems = []
    with _httpx.Client(base_url=f"http://127.0.0.1:{s.port}") as client:
        for label, q, limit, min_len in _CANARY_QUESTIONS:
            t0 = time.time()
            try:
                r = client.post(
                    "/v1/chat/completions",
                    json={"model": "claude", "stream": False,
                          "messages": [{"role": "user", "content": q}]},
                    cookies={"token": token}, timeout=limit + 30,
                )
                el = time.time() - t0
                if r.status_code != 200:
                    problems.append(f"{label}: HTTP {r.status_code}")
                    continue
                a = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
            except Exception as e:
                problems.append(f"{label}: {type(e).__name__} ({time.time()-t0:.0f}s)")
                continue

            if len(a.strip()) < min_len:
                problems.append(f"{label}: 답변이 비었거나 너무 짧음 ({len(a)}자 < {min_len})")
            if any(ph in a for ph in _CANARY_ERROR_PHRASES):
                problems.append(f"{label}: 오류 문구 노출 — {a[:60]!r}")
            if re.match(r"^\s*(니다|습니다|입니다)\b|^\s*[.,)\]}]", a):
                problems.append(f"{label}: 앞부분 유실 의심 — {a[:40]!r}")
            if a.count("```") % 2 != 0:
                problems.append(f"{label}: 코드블록 미닫힘 (뒷부분 잘림 의심)")
            if el > limit:
                problems.append(f"{label}: {el:.0f}s (허용 {limit:.0f}s 초과)")

    return CheckResult(
        not problems,
        " / ".join(problems) if problems else
        f"카나리아 {len(_CANARY_QUESTIONS)}건 모두 온전 (잘림·오류문구·시간 정상)",
    )


def _check_golden_regression() -> CheckResult:
    """골든셋 최신 런이 직전 런 대비 회귀했는가.

    카나리아가 '구조적 건강'을 본다면 골든셋은 '내용 회귀'를 본다 —
    이번 주 사고들(라우팅 오분류, 후속 맥락 유실, 브랜드/대륙 오답)을
    문항으로 고정해뒀으므로, 같은 유형이 재발하면 여기서 이름이 찍힌다.
    """
    from app.core.golden_runner import latest_regression

    reg = latest_regression()
    if not reg.get("comparable"):
        return CheckResult(True, "비교할 런이 2개 미만 (첫 주에는 정상)")
    newly = reg.get("newly_failed", [])
    if newly:
        names = ", ".join(f["item_id"] for f in newly[:5])
        return CheckResult(
            False,
            f"직전 런 대비 새로 깨진 문항 {len(newly)}건: {names}"
            f" (런 {reg['prev_run']}→{reg['latest_run']}, Admin>골든셋에서 비교)",
        )
    return CheckResult(True, f"신규 실패 0건 (통과율 {reg.get('pass_rate')}%, 런 {reg['latest_run']})")


# 미처리가 이만큼 쌓이면 사람이 봐야 한다. 절대량으로 본다 (아래 주석 참조).
_FEEDBACK_BACKLOG_LIMIT = 5


def _check_fixed_bugs_have_a_regression() -> CheckResult:
    """고쳤다고 닫은 붐따가 **회귀로 지켜지고 있는가**.

    ⛔ 실측(2026-08-27): 👎 74건 중 골든셋에 반영된 것이 **0건**이었다. 처리 완료로
       닫혔는데 골든에 없는 것이 40건 — **고친 40가지가 재발해도 아무도 모른다.**
       CLAUDE.md 에 "규칙을 바꿨으면 골든 문항도 같이 추가한다" 고 적혀 있지만
       손으로 하는 일은 결국 안 된다. 빠진 것을 매일 보이게 만든다.

    ⚠️ 오래된 것까지 매일 세면 첫날부터 40건이 떠서 곧 무시당한다 — **최근에 처리한
       것**만 경보로 본다. 밀린 것은 문구에 수만 적는다.
    ⚠️ 코멘트 없는 👎 는 뺀다. 무엇이 틀렸는지 모르면 기대 문구를 쓸 수 없다.
    """
    try:
        from app.core.failure_learning import ALERT_AT, status

        st = status()
    except Exception as e:
        return CheckResult(True, f"판정 보류: {str(e)[:80]}")

    detail = (f"최근 {st['window_days']}일에 고쳤는데 회귀가 없는 붐따 {st['recent']}건 "
              f"(누적 {st['total']}건)")
    if st["recent"] >= ALERT_AT:
        sample = " · ".join(st["questions"][:3])
        return CheckResult(False, detail + (f" — 예: {sample}" if sample else ""))
    return CheckResult(True, detail)

def _check_feedback_backlog() -> CheckResult:
    """👎 중 **아직 처리하지 않은 것**이 쌓이지 않았는가.

    ⛔ 예전엔 들어온 **양**만 셌다 (`feedback_spike`: 최근 7일이 직전 주의 2배이고
       5건 이상). 그래서 **다 고친 뒤에도 7일 창이 지나갈 때까지 계속 빨갛게** 남았다 —
       2026-08-26 실제로 14건이 전부 `done` 인데 경보가 떠 있었고, 관리자가
       "admin 에 4로 되어 있는데 그게 뭔지 모르겠다" 고 물었다.
       **할 일이 없는데 울리는 알림은 곧 무시당한다** (이 파일이 스스로 세운 규칙이다).

    ⚠️ **주 대비 비율로 미처리를 비교하면 안 된다.** 지난주 것은 대개 처리돼 0 에
       수렴하므로, 이번 주에 하나만 들어와도 비율이 폭발해 매번 울린다. 절대량으로 본다.
    ⚠️ **최근 7일로 자르지 않는다.** 미처리는 나이와 무관하게 미처리다 — 7일이 지나면
       조용히 사라지는 대기열은 대기열이 아니다. 진단할 수 없는 건(코멘트 없음)은
       `wontfix` 로 닫는 것이 정상 흐름이고, 그래야 이 숫자가 진실을 말한다.
    """
    row = fetch_one(
        "SELECT COUNT(*) total, "
        "       SUM(status IS NULL OR status NOT IN ('done','wontfix')) open_cnt, "
        "       SUM((status IS NULL OR status NOT IN ('done','wontfix')) "
        "           AND comment IS NOT NULL AND TRIM(comment) <> '') open_comment "
        "FROM message_feedback WHERE rating = -1") or {}
    open_cnt = int(row.get("open_cnt") or 0)
    open_comment = int(row.get("open_comment") or 0)
    week = int((fetch_one(
        "SELECT COUNT(*) c FROM message_feedback WHERE rating = -1 "
        "AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)") or {}).get("c") or 0)

    detail = ("미처리 {}건 (코멘트 있는 것 {}건) · 최근 7일 유입 {}건 · 누적 {}건"
              .format(open_cnt, open_comment, week, int(row.get("total") or 0)))
    return CheckResult(open_cnt < _FEEDBACK_BACKLOG_LIMIT, detail)

# ---- 등록 ----


# ---- 정적 검사 (에러가 안 나는 고장) ----
# ⛔ 판정 로직은 `app/core/static_checks.py` 한 곳에 있다. 개발 중에는 pytest 가,
#    서버에서는 이 자가 점검이 **같은 함수**를 부른다 — 서버에는 pytest 도 tests/ 도
#    없어서 테스트만으로는 매일 돌 수 없었다 (2026-08-13).


def _static(name: str):
    """`static_checks` 의 함수 하나를 CheckResult 로 감싼다 (부작용 없음).

    ⛔ **없는 id 는 실패로 답한다.** 예전엔 `CheckResult(True, "검사 정의 없음 — 건너뜀")`
       이었다 — id 에 오타가 나거나 `SC.ALL` 에서 함수가 빠지면 그 검사는 **영원히
       초록으로 통과**한다. 방어선이 사라진 것과 없는 것이 화면에서 똑같이 보인다.
       이 브랜치가 고친 `dca36c3`(SC.ALL 에는 있는데 CHECKS 에 없어 서버에서 안 돌던
       검사)의 정확히 반대 방향이고, 같은 부류의 조용한 실패다 (2026-08-24 리뷰).
       거울 목록 두 벌은 **양쪽 다** 대조해야 한다.
    """
    def run() -> CheckResult:
        from app.core import static_checks as SC
        for cid, fn, _label in SC.ALL:
            if cid == name:
                ok, detail = fn()
                return CheckResult(ok, detail)
        return CheckResult(False, f"'{name}' 이 static_checks.ALL 에 없다 — "
                                  "id 오타이거나 검사가 삭제됐다 (통과로 세지 않는다)")
    return run


def _check_jandi_relay() -> CheckResult:
    """잔디 대기열이 밀려 있지 않은가 — 밀렸다면 DB_PC 릴레이가 안 도는 것이다.

    ⛔ 서버는 잔디에 직접 붙지 못한다 (WAS·APP 모두 wh.jandi.com 403, 2026-08-18 실측).
       그래서 '보냈다'를 서버 로그로는 알 수 없고, **대기열이 비는지**로만 알 수 있다.
       아무도 등록하지 않았으면 검사할 것이 없다 (기능 미사용은 고장이 아니다).
    """
    from app.core import jandi_briefing

    registered = fetch_one(
        "SELECT COUNT(*) c FROM user_jandi_webhooks WHERE enabled = 1",
    ) or {}
    if not int(registered.get("c") or 0):
        return CheckResult(True, "잔디 브리핑을 등록한 사용자가 없다")
    # ⚠️ **도착 시각을 기다리는 중인 것을 밀린 것으로 세지 마라** — 사용자가 18:30 을
    #    고르면 07:30 에 만든 브리핑이 11시간 대기한다. 그것까지 세면 자가 점검이
    #    매일 실패하고, 매일 뜨는 경고는 곧 아무도 안 읽는다.
    #    ⚠️ 두 조건은 **각자의 시계**로 본다: `created_at` 은 DB 가 찍었으니 DB 의
    #    `NOW()` 로, `send_after` 는 KST 벽시계로 넣었으니 파이썬 KST 로 견준다.
    stuck = fetch_one(
        "SELECT COUNT(*) c FROM briefing_jandi_outbox "
        "WHERE status = 'pending' AND created_at < DATE_SUB(NOW(), INTERVAL 6 HOUR) "
        "  AND (send_after IS NULL OR send_after <= %s)",
        (jandi_briefing.now_kst() - timedelta(hours=6),),
    ) or {}
    waiting = int(stuck.get("c") or 0)
    counts = jandi_briefing.status_counts()
    if waiting:
        return CheckResult(
            False,
            f"6시간 넘게 대기 중인 브리핑 {waiting}건 — DB_PC 릴레이"
            f"(scripts/jandi_briefing_relay.py) 예약 작업을 확인하라",
        )
    if counts["failed"]:
        return CheckResult(
            False, f"최근 7일 발송 실패 {counts['failed']}건 (웹훅 주소가 지워졌을 수 있다)",
        )
    return CheckResult(True, f"최근 7일 발송 {counts['sent']}건 · 대기 {counts['pending']}건")


def _check_notion_push() -> CheckResult:
    """도착 시각이 **지난** 대기 건이 쌓이거나, 최근 발송이 실패로 굳었는가
    (브리핑 노션 대기열).

    ⛔ 잔디와 마찬가지로 릴레이가 없어 '보냈다'를 서버 로그로는 알 수 없다 —
       대기열이 비는지로만 안다.
    ⚠️ 기다리는 중인 것(18:30 을 고른 사람)을 밀린 것으로 세지 않는다 —
       매일 뜨는 경고는 곧 아무도 안 읽는다.
    ⛔ **밀린 것과 실패로 굳은 것은 다른 사고다.** 페이지의 `셀라` 연결을 떼면
       3번 시도 후 `status='failed'` 로 굳어 `pending` 대기 조회에서 사라진다 —
       `pending` 만 세면 가장 흔한 실패가 이 검사에서 통째로 빠진다
       (`_check_jandi_relay` 가 이미 `failed` 를 세는 것과 같은 이유).
    """
    from app.core.notion_briefing import now_kst

    row = fetch_one(
        "SELECT COUNT(*) AS n FROM briefing_notion_outbox "
        "WHERE status='pending' AND ("
        # 도착 시각이 정해진 건: 그 시각이 지났는데도 남아 있으면 밀린 것이다.
        " (send_after IS NOT NULL AND send_after < DATE_SUB(%s, INTERVAL 6 HOUR))"
        # ⚠️ 즉시 발송분은 도착 시각이 없다 — 만든 시각으로 센다.
        #    `pending()` 은 이런 행을 **일부러** 함께 꺼내므로, 감시에서만 빼면
        #    나중에 즉시 발송 경로가 생겼을 때 조용히 쌓인다.
        " OR (send_after IS NULL AND created_at < DATE_SUB(%s, INTERVAL 6 HOUR))"
        ")", (now_kst(), now_kst()))
    stuck = int((row or {}).get("n") or 0)
    if stuck:
        return CheckResult(False, f"도착 시각이 6시간 넘게 지난 대기 {stuck}건")
    failed_row = fetch_one(
        "SELECT COUNT(*) AS n FROM briefing_notion_outbox "
        "WHERE status='failed' AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)")
    failed = int((failed_row or {}).get("n") or 0)
    if failed:
        return CheckResult(
            False,
            f"최근 7일 발송 실패 {failed}건 (페이지의 셀라 연결이 끊겼을 수 있다)",
        )
    return CheckResult(True, "밀린 건 없음")


def _check_drive_shared_access() -> CheckResult:
    """드라이브가 **네트워크·프록시·API 수준에서** 살아 있는가 (계정 하나로 찌른다).

    ⚠️ 토큰이 죽어도 화면은 에러가 아니라 **'전부 없음'** 으로 보인다 — 조용한 실패다.
       그래서 "몇 건 나왔나"가 아니라 "응답이 왔나"로 판정한다. **빈 결과(0건)는 고장이
       아니다** — 검색어에 맞는 파일이 정말 없을 수 있다.

    ⛔ `get_credentials()` 는 미연결·만료·일시 오류를 전부 `None` 하나로 뭉갠다.
       그러면 "예전에 연결했다가 토큰이 죽은 사람"이 "한 번도 연결한 적 없는 사람"과
       같은 안내를 받는다 — 운영자가 잘못된 조치(재연결 안내 대신 방치)를 하게 된다.
       `load_credentials()` 로 원인을 구분해 뭘 해야 하는지 명확히 남긴다.

    ⚠️ **이 검사는 일부러 계정 하나로만 찌른다** — 목적이 "구글 API 가 살아
       있는가"(네트워크·프록시·인증서 문제)이지 "누구 토큰이 죽었는가"가 아니다.
       프로덕션은 연결 계정이 15개다. 나머지 14명의 개별 죽음은 [[google_account_health]]
       (`_check_google_account_health`) 가 이름을 대며 잡는다 — 하나로 합치면
       "드라이브가 죽었다"와 "그 사람만 재연결이 필요하다"를 구분할 수 없고,
       그 둘은 운영자가 해야 할 조치가 다르다.
    """
    from app.core.google_auth import GoogleAuthManager
    from app.core.google_workspace import search_drive

    email = "jeffrey@skin1004korea.com"
    outcome = GoogleAuthManager().load_credentials(email)
    if outcome.status == "disconnected":
        return CheckResult(False, f"{email} 구글 미연결 — 인증서류 찾기가 동작하지 않는다")
    if outcome.status == "invalid":
        return CheckResult(
            False,
            f"{email} 구글 인증 만료/무효 — 재연결이 필요하다 "
            "(인증서류 찾기가 조용히 '전부 없음' 으로 보일 수 있다)",
        )
    if outcome.status == "transient_error":
        return CheckResult(False, f"{email} 구글 인증 조회 일시 실패 — 다음 실행에서 재확인")
    try:
        files = search_drive(outcome.credentials, "COA", max_results=1)
    except Exception as exc:                          # noqa: BLE001
        return CheckResult(False, f"드라이브 조회 실패: {str(exc)[:200]}")
    return CheckResult(True, f"공유드라이브 조회 정상 (표본 {len(files)}건)")


def _stored_google_accounts() -> list[dict]:
    """구글을 연결한 적 있는 활성 사용자 — 저장된 토큰 파일이 있는 사람만.

    ⛔ **`gws_tokens/*.json` 파일명을 거꾸로 email 로 복원하지 마라.** 그 이름은
       `email.replace("@","_at_").replace(".","_")` 로 만들어졌는데, 원래 email 에
       밑줄이 있으면 되돌릴 수 없다 (`_at_` 를 `@` 로, 남은 `_` 를 `.` 로 바꾸는
       역변환은 추측일 뿐이다 — 잘못 복원한 email 로 `load_credentials()` 를
       부르면 파일이 안 열려 "죽었다"로 오탐한다). 대신 앱이 이미 아는 사용자
       이메일마다 `has_credentials()` 로 저장 여부만 물어본다 — 출근 브리핑
       발송 대상 선정(`personal_briefing.run_morning_precompute`)과 같은 패턴이다.
    """
    from app.core.google_auth import GoogleAuthManager

    rows = fetch_all(
        "SELECT u.id, COALESCE(a.email, u.email) email, "
        "COALESCE(a.display_name, u.display_name) name "
        "FROM users u LEFT JOIN directory_users a ON a.id = u.ad_user_id "
        "WHERE u.is_active = 1"
    ) or []
    mgr = GoogleAuthManager()
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        email = (r.get("email") or "").strip()
        if not email or email in seen:
            continue
        seen.add(email)
        if mgr.has_credentials(email):
            out.append({"email": email, "name": r.get("name") or email})
    return out


def _check_google_account_health() -> CheckResult:
    """저장된 구글 계정 **각각**의 토큰이 살아 있는가 — 죽은 계정의 이름을 남긴다.

    [[drive_shared_access]] 는 계정 하나로 "구글 API 가 살아 있는가"만 본다.
    이 검사는 **연결된 전 계정**을 돈다 — 프로덕션 연결 계정이 15개인데 고정된
    하나만 보면 나머지 14개가 죽어도 화면은 계속 초록이다. 인증서류 찾기는 각자
    자기 OAuth 로 도는 기능이라, 죽은 토큰은 "그 계정을 쓰는 그 사람"에게만
    조용히 '전부 없음' 으로 보인다 — 화면은 잘못이 없다는 듯 멀쩡하다.

    ⛔ **이 검사는 부작용이 있다 — 이 파일의 다른 검사와 다른, 의도된 예외다.**
       `load_credentials()` 는 만료된 access token 을 갱신하고 그 결과를 토큰
       파일에 다시 쓴다. 하지만 그건 **그 사람이 앱을 쓸 때마다 어차피 일어나는
       것과 같은 갱신**이다 — 상태를 새로 만드는 게 아니라 정상 사용을 하루
       앞당겨 흉내 낼 뿐이다. 매일 이 검사를 돌리면, 그 사람이 인증서류 찾기를
       열어 '전부 없음' 을 자신 있게 오답으로 받기 **전에** 죽은 토큰을 먼저
       찾아낼 수 있다 — 그래서 "검사는 부작용이 없어야 한다" 는 이 파일의
       원칙을 여기서만 깬다.
    """
    accounts = _stored_google_accounts()
    if not accounts:
        return CheckResult(True, "구글을 연결한 사용자가 없다")

    from app.core.google_auth import GoogleAuthManager

    mgr = GoogleAuthManager()
    counts: dict[str, int] = {}
    dead: list[str] = []
    for acc in accounts:
        outcome = mgr.load_credentials(acc["email"])
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
        if outcome.status != "ready":
            dead.append(f"{acc['name']}({acc['email']}): {outcome.status}")

    detail = f"연결 {len(accounts)}명 중 정상 {counts.get('ready', 0)}명"
    if dead:
        shown = dead[:10]
        detail += f" / 재연결 필요 {len(dead)}명 — " + ", ".join(shown)
        if len(dead) > len(shown):
            detail += f" 외 {len(dead) - len(shown)}명"
    return CheckResult(not dead, detail)


CHECKS: list[Check] = [
    Check("ad_sync_fresh", "batch", SEV_CRITICAL,
          "Entra 사용자 동기화가 26시간 내 성공했는가", _check_ad_sync_fresh),
    Check("wiki_extract_fresh", "batch", SEV_WARNING,
          "위키 추출이 3시간 내 동작했는가", _check_wiki_extract_fresh),
    Check("quality_snapshot_fresh", "batch", SEV_WARNING,
          "품질 스냅샷이 2일 내 생성됐는가", _check_quality_snapshot_fresh),
    Check("job_heartbeats", "batch", SEV_CRITICAL,
          "모든 스케줄 잡이 제 주기 안에 성공했는가", _check_job_heartbeats),
    Check("restart_loop", "batch", SEV_CRITICAL,
          "앱이 재시작을 반복하고 있지 않은가 (크래시 루프)", _check_restart_loop),
    Check("jandi_relay", "batch", SEV_WARNING,
          "출근 브리핑 잔디 대기열이 비워지고 있는가 (DB_PC 릴레이)", _check_jandi_relay),
    Check("notion_push", "batch", SEV_WARNING,
          "브리핑 노션 대기열이 비워지고 있는가", _check_notion_push),
    Check("orphan_user_groups_ad", "integrity", SEV_WARNING,
          "user_groups 가 실재하는 셀라 사용자를 가리키는가", _check_orphan_user_groups_ad),
    Check("orphan_user_groups_grp", "integrity", SEV_WARNING,
          "user_groups 가 실재하는 그룹을 가리키는가", _check_orphan_user_groups_grp),
    Check("users_email_present", "integrity", SEV_WARNING,
          "users 에 이메일 누락이 없는가", _check_users_email),
    Check("name_encoding", "integrity", SEV_INFO,
          "이름이 이스케이프된 채 저장되지 않았는가", _check_name_encoding,
          repair=_repair_name_encoding),
    Check("auth_config", "permission", SEV_CRITICAL,
          "로그인이 가능한 상태인가 (JWT 설정 유효성)", _check_auth_config),
    Check("fi_permission_enforced", "permission", SEV_CRITICAL,
          "FI 차단 방어선이 살아 있는가", _check_fi_permission_enforced),
    Check("fi_grant_count", "permission", SEV_WARNING,
          "FI 열람 허용 인원이 정상 범위인가", _check_fi_grant_count),
    Check("admin_exists", "permission", SEV_CRITICAL,
          "관리자 계정이 존재하는가", _check_admin_exists),
    Check("bq_tables", "datasource", SEV_CRITICAL,
          "허용된 BigQuery 테이블에 전부 접근되는가", _check_bq_tables),
    Check("value_lists", "datasource", SEV_WARNING,
          "프롬프트 값 목록이 최신인가", _check_value_lists),
    Check("schema_changes", "datasource", SEV_WARNING,
          "어제 대비 앱이 쓰는 테이블 스키마가 바뀌었는가", _check_schema_changes),
    Check("ad_media_missing", "datasource", SEV_WARNING,
          "광고 매체가 통째로 사라졌는가 (조용한 데이터 유실)",
          _check_ad_media_missing),
    Check("logistics_quantity_outlier", "datasource", SEV_WARNING,
          "수출 물류 수량 칸에 주문번호가 들어가 있지 않은가 (합계가 3,492배 틀렸다)",
          _check_logistics_quantity_outlier),
    Check("awards_unknown_usage", "datasource", SEV_INFO,
          "수상 활용 표기 미등록", _check_awards_unknown_usage),
    Check("awards_sheet_freshness", "datasource", SEV_WARNING,
          "수상/랭킹 적재 신선도", _check_awards_sheet_freshness),
    Check("new_log_errors", "quality", SEV_WARNING,
          "어제 로그에 직전 주에 없던 에러 유형이 있는가", _check_new_log_errors),
    Check("notion_allowlist", "datasource", SEV_WARNING,
          "노션 허용 페이지를 인테그레이션이 볼 수 있는가", _check_notion_allowlist),
    Check("qdrant", "datasource", SEV_CRITICAL,
          "Qdrant 기본 컬렉션에 데이터가 있는가", _check_qdrant),
    Check("cs_cache", "datasource", SEV_WARNING,
          "CS/BP 제품 Q&A 캐시가 최신인가 (기동 시 한 번만 읽던 것을 매시로 바꿈)",
          _check_cs_cache),
    Check("notion_unshared", "datasource", SEV_WARNING,
          "노션에 걸었는데 학습 안 된 자료가 새로 생겼는가 (미공유)",
          _check_notion_unshared),
    Check("data_freshness", "datasource", SEV_WARNING,
          "파생 사본이 원본을 따라가고 있는가 (나이가 아니라 뒤처짐)",
          _check_data_freshness),
    # ⛔ 감시를 **붙이는 걸 잊은 것**까지 잡는 층 — CS 캐시가 그렇게 조용히 낡았다
    Check("freshness_coverage", "datasource", SEV_CRITICAL,
          "신선도 감시가 안 붙은 데이터소스가 있는가",
          _check_freshness_coverage),
    Check("drive_shared_access", "datasource", SEV_WARNING,
          "구글 드라이브 API 가 살아 있는가 (계정 1개로 확인 — 네트워크/프록시)",
          _check_drive_shared_access),
    Check("google_account_health", "datasource", SEV_WARNING,
          "연결된 구글 계정마다 토큰이 살아 있는가 (죽은 계정 이름을 남긴다)",
          _check_google_account_health),
    Check("canary_answers", "quality", SEV_WARNING,
          "대표 질문 답변이 구조적으로 온전한가", _check_canary_answers),
    # ⚠️ id 를 `feedback_spike` 에서 바꿨다 (2026-08-26). 재는 대상이 달라졌는데
    #    이름만 남으면 다음 사람이 "급증" 으로 읽는다 — Admin 추세 그래프는
    #    옛 id 에서 끊기고 새 id 로 다시 쌓인다.
    # ⛔ 고친 것이 재발해도 모르면 고친 것이 아니다 (2026-08-27 추가).
    Check("fixed_bugs_regression", "quality", SEV_WARNING,
          "고친 붐따가 골든셋으로 지켜지는가", _check_fixed_bugs_have_a_regression),
    Check("feedback_backlog", "quality", SEV_WARNING,
          "👎 미처리가 쌓이지 않았는가", _check_feedback_backlog),
    Check("golden_regression", "quality", SEV_WARNING,
          "골든셋이 직전 런 대비 회귀하지 않았는가", _check_golden_regression),
    # 정적 검사 — 코드·자산을 읽어 "정상처럼 보이는 고장"을 찾는다
    Check("static_assets", "static", SEV_CRITICAL,
          "프론트 자산이 비었거나 잘리지 않았는가 (0바이트면 화면이 백지다)",
          _static("static_assets")),
    Check("static_value_list_dupes", "static", SEV_WARNING,
          "자동 주입 컬럼의 값 목록을 프롬프트에 손으로 다시 나열하지 않았는가 "
          "(낡은 목록을 LLM 이 믿고 0건을 '데이터 없음'으로 오답한 사고)",
          _static("static_value_list_dupes")),
    # ⚠️ 프롬프트만 보면 절반이다 — 같은 목록이 파이썬 소스에도 손으로 적혀 있었고
    #    (0건 진단 힌트) **둘 다 낡아 있었다**: 없는 값(남미·중미·북아프리카)을
    #    "유효 값" 이라 알려주고 있는 값(중남미)은 빠져 있었다 (2026-08-27 실측).
    Check("static_value_list_src", "static", SEV_WARNING,
          "LLM 에 주는 값 목록을 파이썬 소스에 손으로 적어 두지 않았는가 "
          "(프롬프트 사본만 고치고 소스 사본이 남아 낡은 채로 돌던 사고)",
          _static("static_value_list_src")),
    Check("static_css_vars", "static", SEV_WARNING,
          "정의되지 않은 CSS 변수를 참조하지 않는가 (폴백이 조용히 먹는다)",
          _static("static_css_vars")),
    Check("static_at_sources", "static", SEV_WARNING,
          "@@ 데이터소스 목록이 프론트·서버에서 일치하는가", _static("static_at_sources")),
    # ⛔ 클래스 안에 모듈 레벨 `def` 를 넣으면 거기서 클래스가 끊긴다 — import 는
    #    멀쩡하고 라우팅이 그 속성을 만질 때서야 터진다 (2026-09-03 실제 배포 사고)
    Check("static_orch_class", "static", SEV_CRITICAL,
          "오케스트레이터 클래스 본문이 끊기지 않았는가", _static("static_orch_class")),
    Check("static_prompt_copies", "static", SEV_WARNING,
          "direct 시스템 프롬프트 사본이 하나인가", _static("static_prompt_copies")),
    Check("static_asset_stamp", "static", SEV_WARNING,
          "자산 캐시 지문 배선 (끊기면 캐시가 조용히 안 켜진다)",
          _static("static_asset_stamp")),
    Check("static_kw_collision", "static", SEV_WARNING,
          "라우팅 키워드가 다른 경로의 긴 낱말에 삼켜지지 않는가",
          _static("static_kw_collision")),
    Check("static_fi_mask", "static", SEV_CRITICAL,
          "권한 없는 프롬프트에서 손익 섹션이 실제로 지워지는가",
          _static("static_fi_mask")),
    Check("static_flow_spec", "static", SEV_WARNING,
          "흐름 선언(캔버스)이 실제 코드·@@ 라우트와 일치하는가",
          _static("static_flow_spec")),
    # ⛔ 라우트만 보던 위 검사는 **관문이 통째로 빠져도 통과했다** (2026-09-09:
    #    12개 중 6개 누락 + 순서 불일치인데 매일 "일치"). 관문은 따로 본다.
    Check("static_flow_gates", "static", SEV_WARNING,
          "캔버스의 관문 순서가 코드(비스트리밍·스트리밍)와 같은가",
          _static("static_flow_gates")),
    # ⛔ 정규식의 `\b` 가 진짜 백스페이스 문자(0x08)로 들어가면 에러 없이 컴파일되고
    #    영영 매치하지 않는다. 화면에도 안 보인다 — 이 파일의 "답변 앞부분 유실" 감지가
    #    실제로 그렇게 죽어 있었다 (2026-08-25 발견).
    # ⛔ `@@팀` 필터가 색인 표기와 어긋나면 에러 없이 0건이다 — 화면에서는
    #    "자료가 없네요" 와 구분되지 않는다 (2026-08-25).
    # ⛔ 링크 단계가 빠지면 에러 없이 검색 결과만 얇아진다 (2026-08-25 이전 상태:
    #    시트·드라이브 69%가 색인에 없었다).
    # ⛔ 낡은 조각은 에러가 아니라 **옛 사실을 자신 있게 답하는 것**으로 드러난다.
    Check("static_stale_copies", "static", SEV_WARNING,
          "같은 문서의 옛 조각이 색인에 남아 최신본과 경쟁하는가",
          _static("static_stale_copies")),
    Check("static_team_links", "static", SEV_WARNING,
          "팀 자료 링크(시트·드라이브)가 벡터 색인에 들어가 있는가",
          _static("static_team_links")),
    # ⛔ 설문 배선이 끊기면 에러가 아니라 **팝업이 아무에게도 안 뜬다** — 침묵이다.
    Check("static_survey_wiring", "static", SEV_WARNING,
          "만족도 설문 배선이 /me ↔ 프론트 ↔ 처리함에서 이어지는가",
          _static("static_survey_wiring")),
    Check("static_qdrant_teams", "static", SEV_WARNING,
          "@@팀 지정이 벡터 색인의 team 값과 맞물리는가 (어긋나면 조용히 0건)",
          _static("static_qdrant_teams")),
    # ⛔ 수정일 없이 색인된 노션 문서는 "최신 우선" 규칙을 무력화한다 —
    #    낡은 값이 이기는데 에러는 안 난다 (붐따 #105, 2026-08-25).
    Check("static_notion_dates", "static", SEV_WARNING,
          "수정일 없이 색인된 노션 문서가 있는가 (인테그레이션 미공유)",
          _static("static_notion_dates")),
    Check("static_ctrl_chars", "static", SEV_WARNING,
          "소스에 눈에 안 보이는 제어문자가 섞였는가 (정규식이 조용히 안 맞는다)",
          _static("static_ctrl_chars")),
    Check("static_page_scroll", "static", SEV_WARNING,
          "style.css 를 쓰는 문서 페이지가 스크롤을 되살렸는가 "
          "(채팅용 overflow:hidden 을 물려받아 화면 밖 내용에 닿지 못하던 사고)",
          _static("static_page_scroll")),
    # ⛔ 막대 라벨이 플롯 경계에 잘리면 **가장 큰 값부터** 사라진다 (2026-08-31 제보).
    #    캔버스는 에러 없이 그려져서 사람이 볼 때까지 아무도 모른다.
    Check("static_chart_labels", "static", SEV_WARNING,
          "막대 라벨이 플롯 경계를 보는가 (긴 막대의 숫자가 잘린다)",
          _static("static_chart_labels")),
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
