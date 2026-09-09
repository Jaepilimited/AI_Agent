"""저장한 질문의 영속화와 출근 전 자동 실행을 맡는다.

질문 소유권, 실행 주기, 결과 기록을 한 경계에 모아 API나 배치 호출부가
보안 조건과 실행 상태 갱신을 각각 다시 구현하지 않게 한다.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from app.core import workday
from app.db.mariadb import execute, execute_lastid, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

SEOUL = ZoneInfo("Asia/Seoul")
MAX_QUESTIONS_PER_USER = 5
MAX_CONCURRENT_RUNS = 3
_CADENCES = frozenset({"daily", "weekly", "monthly"})

_DDL = """
CREATE TABLE IF NOT EXISTS saved_questions (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    question VARCHAR(500) NOT NULL,
    cadence ENUM('daily','weekly','monthly') NOT NULL,
    weekday TINYINT NULL,
    enabled TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_run_at DATETIME NULL,
    last_answer MEDIUMTEXT NULL,
    last_error VARCHAR(300) NULL,
    last_status VARCHAR(20) NULL,
    INDEX idx_saved_questions_user (user_id, created_at),
    INDEX idx_saved_questions_due (enabled, cadence, weekday, last_run_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_tables() -> None:
    """서버가 여러 번 기동돼도 같은 저장소를 안전하게 준비한다."""

    execute(_DDL)


def add(
    user_id: int,
    question: str,
    cadence: str,
    weekday: int | None = None,
) -> dict[str, Any]:
    """질문을 추가하고 성공 여부와 거부 이유를 호출자에게 돌려준다."""

    clean_question = (question or "").strip()
    clean_cadence = (cadence or "").strip().lower()
    if not clean_question:
        return {"ok": False, "id": None, "reason": "질문을 입력해 주세요."}
    if len(clean_question) > 500:
        return {"ok": False, "id": None, "reason": "질문은 500자 이하여야 합니다."}
    if clean_cadence not in _CADENCES:
        return {"ok": False, "id": None, "reason": "지원하지 않는 실행 주기입니다."}

    clean_weekday: int | None = None
    if clean_cadence == "weekly":
        if weekday is None or not 0 <= int(weekday) <= 4:
            # ⚠️ 주말 값은 영원히 due가 되지 않으므로 저장 시점에 조용한 무실행을 막는다.
            return {
                "ok": False,
                "id": None,
                "reason": "주간 질문은 월요일(0)부터 금요일(4) 중 하나를 골라 주세요.",
            }
        clean_weekday = int(weekday)

    count_row = fetch_one(
        "SELECT COUNT(*) AS c FROM saved_questions WHERE user_id = %s",
        (int(user_id),),
    )
    if int((count_row or {}).get("c") or 0) >= MAX_QUESTIONS_PER_USER:
        # ⛔ 상한은 서버 저장소에서 강제해야 API 외 호출부도 아침 조회 비용을 늘리지 못한다.
        return {
            "ok": False,
            "id": None,
            "reason": f"저장한 질문은 한 사람당 최대 {MAX_QUESTIONS_PER_USER}개입니다.",
        }

    question_id = execute_lastid(
        "INSERT INTO saved_questions (user_id, question, cadence, weekday) "
        "VALUES (%s, %s, %s, %s)",
        (int(user_id), clean_question, clean_cadence, clean_weekday),
    )
    return {"ok": True, "id": int(question_id), "reason": None}


def list_for(user_id: int) -> list[dict[str, Any]]:
    """한 사용자가 소유한 질문만 최신 생성 순으로 읽는다."""

    return fetch_all(
        "SELECT id,user_id,question,cadence,weekday,enabled,created_at,last_run_at,"
        "last_answer,last_error,last_status FROM saved_questions "
        "WHERE user_id = %s ORDER BY created_at DESC, id DESC",
        (int(user_id),),
    ) or []


def remove(user_id: int, id: int) -> bool:
    """소유자와 질문 ID가 동시에 맞을 때만 삭제한다."""

    # ⛔ 선행 SELECT와 분리하면 확인을 빠뜨린 호출부가 타인의 질문을 지울 수 있다.
    return bool(
        execute(
            "DELETE FROM saved_questions WHERE id = %s AND user_id = %s",
            (int(id), int(user_id)),
        )
    )


def set_enabled(user_id: int, id: int, on: bool) -> bool:
    """소유자와 질문 ID가 동시에 맞을 때만 실행 여부를 바꾼다."""

    # ⛔ 토글도 삭제와 같은 소유권 경계이므로 쓰기 SQL 안에서 원자적으로 확인한다.
    return bool(
        execute(
            "UPDATE saved_questions SET enabled = %s WHERE id = %s AND user_id = %s",
            (1 if on else 0, int(id), int(user_id)),
        )
    )


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def _first_monthly_workday(day: date) -> date:
    first = day.replace(day=1)
    while workday.is_weekend(first):
        first += timedelta(days=1)
    return first


def due(today: date) -> list[dict[str, Any]]:
    """오늘 실행할 활성 질문만 고른다."""

    if workday.is_weekend(today):
        return []

    rows = fetch_all(
        "SELECT id,user_id,question,cadence,weekday,enabled,created_at,last_run_at,"
        "last_answer,last_error,last_status FROM saved_questions "
        "WHERE enabled = 1 ORDER BY id",
    ) or []
    monthly_day = _first_monthly_workday(today)
    selected: list[dict[str, Any]] = []
    for row in rows:
        # ⚠️ 잡이 같은 날 재시작돼도 질문을 두 번 실행하면 비용과 브리핑 결과가 흔들린다.
        if _as_date(row.get("last_run_at")) == today:
            continue
        cadence = row.get("cadence")
        if cadence == "daily":
            selected.append(row)
        elif cadence == "weekly" and row.get("weekday") == today.weekday():
            selected.append(row)
        elif cadence == "monthly" and today == monthly_day:
            selected.append(row)
    return selected


def record_result(id: int, answer: str | None = None, error: str | None = None) -> None:
    """한 실행의 답변 또는 오류와 완료 시각을 함께 기록한다."""

    if error is not None:
        stored_answer = None
        stored_error = str(error)[:300]
        status = "error"
    else:
        stored_answer = "" if answer is None else str(answer).strip()
        stored_error = None
        status = "success" if stored_answer else "empty"
    execute(
        "UPDATE saved_questions SET last_run_at = NOW(), last_answer = %s, "
        "last_error = %s, last_status = %s WHERE id = %s",
        (stored_answer, stored_error, status, int(id)),
    )


def _new_orchestrator():
    """무거운 에이전트 의존성은 실제 배치 실행 때만 불러온다."""

    from app.agents.orchestrator import OrchestratorAgent

    return OrchestratorAgent()


def _default_model() -> str:
    from app.core.llm import MODEL_CLAUDE

    return MODEL_CLAUDE


async def run_saved_questions(now: datetime | None = None) -> dict[str, int | str]:
    """오늘 실행 대상 질문을 최대 세 개씩 병렬 처리하고 각각 결과를 격리한다."""

    current = now or datetime.now(SEOUL)
    today = current.date()
    if workday.is_weekend(today):
        logger.info("saved_questions_skipped_weekend", day=str(today))
        return {
            "selected": 0,
            "succeeded": 0,
            "failed": 0,
            "empty": 0,
            "skipped": "weekend",
        }

    rows = await asyncio.to_thread(due, today)
    if not rows:
        return {"selected": 0, "succeeded": 0, "failed": 0, "empty": 0}

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
    orchestrator = _new_orchestrator()
    model = _default_model()

    async def one(row: dict[str, Any]) -> str:
        async with semaphore:
            question_id = int(row["id"])
            user_id = int(row["user_id"])
            try:
                access = await asyncio.to_thread(
                    fetch_one,
                    "SELECT a.email, a.can_view_fi, u.role, "
                    "(u.requires_group_assignment AND NOT EXISTS "
                    "(SELECT 1 FROM user_groups ug JOIN access_groups g ON g.id=ug.group_id "
                    "WHERE ug.ad_user_id=u.ad_user_id AND g.brand_filter IS NOT NULL "
                    "AND g.brand_filter<>'')) AS requires_group_assignment, "
                    "(SELECT GROUP_CONCAT(DISTINCT g.brand_filter) FROM user_groups ug "
                    "JOIN access_groups g ON g.id=ug.group_id WHERE ug.ad_user_id=u.ad_user_id "
                    "AND g.brand_filter IS NOT NULL AND g.brand_filter<>'') AS brand_filter FROM users u "
                    "JOIN directory_users a ON a.id = u.ad_user_id "
                    "WHERE u.id = %s AND u.is_active = 1 AND a.is_active = 1 LIMIT 1",
                    (user_id,),
                )
                if not access or "can_view_fi" not in access:
                    # ⛔ 권한 조회 실패를 False로 대체하면 DB가 아닌 기본값이 보안 결정을 하게 된다.
                    raise RuntimeError("사용자 FI 권한을 DB에서 확인할 수 없습니다")
                permission = bool(access["can_view_fi"])
                is_admin = access.get("role") == "admin"
                if access.get("requires_group_assignment") and not is_admin:
                    raise RuntimeError("관리자의 데이터 조회 그룹 배정이 필요합니다")
                result = await orchestrator.route_and_execute(
                    str(row["question"]),
                    [],
                    model,
                    user_email=str(access.get("email") or ""),
                    can_view_fi=is_admin or permission,
                    brand_filter=None if is_admin else (access.get("brand_filter") or None),
                    user_id=user_id,
                )
                answer_value = result.get("answer", "") if isinstance(result, dict) else result
                answer = "" if answer_value is None else str(answer_value).strip()
                await asyncio.to_thread(record_result, question_id, answer=answer)
                return "success" if answer else "empty"
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
                try:
                    await asyncio.to_thread(record_result, question_id, error=error)
                except Exception as record_exc:
                    # ⚠️ 결과 기록 장애도 다른 질문의 실행까지 취소시키지 않는다.
                    logger.warning(
                        "saved_question_result_record_failed",
                        question_id=question_id,
                        error_type=type(record_exc).__name__,
                    )
                logger.warning(
                    "saved_question_run_failed",
                    question_id=question_id,
                    user_id=user_id,
                    error_type=type(exc).__name__,
                )
                return "error"

    statuses = await asyncio.gather(*(one(row) for row in rows))
    return {
        "selected": len(rows),
        "succeeded": sum(status in {"success", "empty"} for status in statuses),
        "failed": statuses.count("error"),
        "empty": statuses.count("empty"),
    }
