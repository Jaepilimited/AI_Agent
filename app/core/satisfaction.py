# -*- coding: utf-8 -*-
"""만족도 설문 — 접속일수 10일차부터 20일 간격으로 별점을 한 번 묻는다.

**왜 마일스톤인가**: 붐따(👎)는 답변 하나가 틀렸을 때 눌린다. 그래서 "이 서비스가
쓸 만한가" 는 아무도 답한 적이 없다. 실측(2026-09-02)으로 가입 67명 중 실제 사용
이력이 있는 사람은 62명이고, 그중 10일 이상 쓴 사람이 12명·50일 이상이 2명이다 —
**오래 쓴 사람에게만 묻는다**. 처음 온 사람에게 만족도를 물으면 답할 근거가 없다.

⛔ **방문일수는 `user_visits` 만으로 세지 않는다.** 그 원장은 2026-08-11 부터라
   3주치뿐이고, 그것만 세면 **6개월 쓴 사람이 '10일차' 로 잡힌다** — 에러가 아니라
   조용한 거짓말이다. `conversations`(대화를 시작한 날, 2026-03-10 부터)와 날짜를
   합집합으로 센다.

⛔ **코멘트는 별점과 무관하게 다 받는다** (2026-09-02 사용자 지시). 다만 처리
   대기열(붐따 처리함)에 올리는 것은 **3점 이하이거나 코멘트가 달린 것**뿐이다.
   읽을 게 없는 행이 대기열을 채우면 대기열이 뜻을 잃는다 — 코멘트 없는 👎 를
   대기열과 섞지 않는 것과 같은 규칙이다.

처리 컬럼(`status`·`handled_*`·`reply_seen_at`)은 `message_feedback` 과 **같은
이름**을 쓴다. 처리함이 두 소스를 같은 방식으로 다루기 위해서다.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import structlog

from app.db.mariadb import execute, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

# 10일차부터 **20일 간격**으로 계속 묻는다 (2026-09-02 사용자 지시: 10·30·50·70…).
# ⛔ 처음엔 10·50·100 이었는데, 간격이 벌어질수록 오래 쓴 사람이 몇 달을 조용히
#    지나간다 (실측 최대 이력 60일 → 100일차는 3~6개월 뒤였다). 지금은 끝이 없다.
# ⚠️ 상한은 **닿지 않을 만큼** 넉넉해야 한다 — 상한에서 멈추면 에러가 아니라
#    "오래 쓴 사람에게만 조용히 안 뜨는" 상태가 된다 (3년치를 둔다).
_MILESTONE_START = 10
_MILESTONE_STEP = 20
_MILESTONE_MAX = 1090
MILESTONES = tuple(range(_MILESTONE_START, _MILESTONE_MAX + 1, _MILESTONE_STEP))

# 이 점수 이하는 코멘트가 없어도 처리 대상으로 본다
LOW_RATING_MAX = 3

_TABLE = "satisfaction_surveys"


def ensure_survey_table() -> None:
    """앱 기동 시 idempotent 생성 (다른 ensure_* 와 같은 방식)."""
    execute(
        f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            milestone SMALLINT UNSIGNED NOT NULL COMMENT '접속일차 (10, 30, 50 …)',
            visit_days SMALLINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '물어본 시점의 접속일수',
            rating TINYINT UNSIGNED NULL COMMENT '1~5 별점, NULL=나중에',
            comment TEXT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'new',
            handled_by VARCHAR(255) NULL,
            handled_at DATETIME NULL,
            handled_note TEXT NULL,
            reply_seen_at DATETIME NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_survey_user_milestone (user_id, milestone),
            INDEX idx_survey_status (status),
            INDEX idx_survey_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )


# ── 판정 (순수 함수 — 회귀가 여기를 고정한다) ──────────────────────────────

def select_milestone(days: int, recorded: Iterable[int],
                     asked_at_days: int = 0) -> Optional[int]:
    """도달했고 아직 안 물어본 임계 중 **가장 큰 것**을 고른다.

    가장 작은 것을 고르면 60일 쓴 사람에게 '10일차 축하' 가 뜬다. 지나간 작은
    임계는 다시 뜨지 않는다 — 이미 지난 지점을 뒤늦게 묻는 것도 거짓말이다.

    ⛔ **이미 답한 것보다 작은 임계는 함께 닫는다.** 50일차에 답한 사람은 10 이
       기록에 없지만 그 지점을 이미 지났다 — 빼지 않으면 다음 세션에 '10일차'
       팝업이 뜬다. 에러가 아니라 오작동으로 읽히는 종류다.
    ⛔ **바닥은 물어본 그때의 접속일수(`asked_at_days`)까지 올린다.** 40일 쓴
       사람에게 10일차를 물었다면 30일차도 이미 지난 지점이다 — 안 올리면 방금
       '나중에' 를 누른 사람에게 다음 접속에 또 뜬다 (임계를 촘촘하게 바꾸면서
       실제로 그렇게 됐다).
    """
    done = {int(m) for m in (recorded or ())}
    floor = max([*done, int(asked_at_days or 0)]) if (done or asked_at_days) else 0
    reached = [m for m in MILESTONES
               if m <= int(days or 0) and m > floor and m not in done]
    return max(reached) if reached else None


def needs_attention(rating: Optional[int], comment: Optional[str]) -> bool:
    """처리 대기열(붐따 처리함)에 올릴 응답인가."""
    if rating is None:
        return False   # '나중에' 는 불만이 아니다
    if int(rating) <= LOW_RATING_MAX:
        return True
    return bool((comment or "").strip())


# ── 조회 ──────────────────────────────────────────────────────────────────

def visit_days(user_id: int) -> int:
    """누적 접속일수 — 방문 원장과 대화 기록의 **날짜 합집합**."""
    row = fetch_one(
        """SELECT COUNT(DISTINCT dt) AS d FROM (
               SELECT visit_date AS dt FROM user_visits WHERE user_id = %s
               UNION
               SELECT DATE(created_at) AS dt FROM conversations WHERE user_id = %s
           ) x""",
        (int(user_id), int(user_id)),
    ) or {}
    return int(row.get("d") or 0)


def _recorded(user_id: int) -> List[Dict[str, Any]]:
    """이 사람에게 물어본 기록 — 임계와 **그때의 접속일수**를 함께 가져온다."""
    return fetch_all(
        f"SELECT milestone, visit_days FROM {_TABLE} WHERE user_id = %s",
        (int(user_id),)) or []


def pending_milestone(user_id: int) -> Optional[Dict[str, Any]]:
    """지금 물어볼 임계 (없으면 None). `/me` 가 매번 부르므로 값싸야 한다.

    ⚠️ 실패해도 로그인·채팅을 막지 않는다 — 설문은 부가 기능이다.
    """
    try:
        rows = _recorded(user_id)
        recorded = [int(r["milestone"]) for r in rows]
        asked_at = max([int(r.get("visit_days") or 0) for r in rows], default=0)
        if recorded and max(recorded) >= MILESTONES[-1]:
            return None            # 마지막 지점까지 물었다 — 일수를 셀 필요도 없다
        days = visit_days(user_id)
        milestone = select_milestone(days, recorded, asked_at)
        if milestone is None:
            return None
        return {"milestone": milestone, "visit_days": days}
    except Exception as e:
        logger.warning("survey_pending_failed", user_id=user_id, error=str(e)[:160])
        return None


# ── 저장 ──────────────────────────────────────────────────────────────────

def record_response(user_id: int, milestone: int, rating: Optional[int],
                    comment: str = "", visit_days: Optional[int] = None) -> bool:
    """응답 또는 '나중에' 를 남긴다. 같은 임계에 이미 행이 있으면 **덮지 않는다**."""
    if int(milestone) not in MILESTONES:
        raise ValueError(f"unknown milestone: {milestone}")
    if rating is not None and int(rating) not in (1, 2, 3, 4, 5):
        raise ValueError(f"rating must be 1..5 or null: {rating}")

    text = (comment or "").strip() or None
    execute(
        f"""INSERT INTO {_TABLE}
                (user_id, milestone, visit_days, rating, comment, status)
            VALUES (%s, %s, %s, %s, %s, 'new')
            ON DUPLICATE KEY UPDATE id = id""",
        (int(user_id), int(milestone), int(visit_days or 0),
         None if rating is None else int(rating), text),
    )
    logger.info("survey_recorded", user_id=user_id, milestone=int(milestone),
                rating=rating, has_comment=bool(text))
    return True


def reset_for_user(user_id: int) -> int:
    """이 사람의 설문 기록을 지운다 — **팝업을 다시 보기 위한 테스트용**.

    ⛔ 조건을 파이썬에서 확인하고 지우지 마라. `user_id` 를 SQL 안에 두어
       호출부가 실수해도 남의 응답이 지워질 수 없게 한다 (FI·보고서 공유와 같은 사상).
    """
    n = execute(f"DELETE FROM {_TABLE} WHERE user_id = %s", (int(user_id),))
    logger.info("survey_reset", user_id=user_id, deleted=n)
    return int(n or 0)


def set_survey_status(survey_id: int, status: str, who: str,
                      note: Optional[str] = None) -> bool:
    """처리 상태 변경 — 붐따와 같은 어휘·같은 검증을 쓴다."""
    from app.core.feedback_inbox import (
        STATUS_DONE, STATUS_WONTFIX, _STATUSES,
        _handled_note_has_encoding_loss,
    )
    if status not in _STATUSES:
        raise ValueError(f"unknown status: {status}")
    if _handled_note_has_encoding_loss(note):
        raise ValueError(
            "처리 메모 인코딩이 손상되었습니다. UTF-8 입력으로 다시 작성해주세요."
        )
    done = status in (STATUS_DONE, STATUS_WONTFIX)
    execute(
        f"UPDATE {_TABLE} SET status = %s, handled_by = %s, handled_note = %s, "
        "handled_at = " + ("NOW()" if done else "NULL") + " WHERE id = %s",
        (status, who, note, int(survey_id)),
    )
    logger.info("survey_status_changed", id=survey_id, status=status, who=who)
    return True


# ── 처리함 목록·집계 ──────────────────────────────────────────────────────

def _attention_sql() -> str:
    return (f"(s.rating IS NOT NULL AND (s.rating <= {LOW_RATING_MAX} "
            "OR (s.comment IS NOT NULL AND s.comment <> '')))")


def list_surveys(status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """처리 대상 응답만 돌려준다. 붐따 목록과 **같은 모양**으로 맞춘다."""
    where = [_attention_sql()]
    params: list = []
    if status:
        where.append("s.status = %s")
        params.append(status)
    rows = fetch_all(
        "SELECT s.id, s.rating, s.comment, s.created_at, s.status, s.handled_at, "
        "       s.handled_by, s.handled_note, s.milestone, s.visit_days, "
        "       u.display_name AS user_name "
        f"FROM {_TABLE} s LEFT JOIN users u ON u.id = s.user_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY (s.comment IS NOT NULL AND s.comment <> '') DESC, "
        "         (s.status = 'new') DESC, s.created_at DESC "
        "LIMIT %s", (*params, int(limit))) or []
    for r in rows:
        r["status"] = r.get("status") or "new"
        r["source"] = "survey"
    return rows


def survey_summary() -> Dict[str, Any]:
    """응답 현황 — **응답률과 대상 인원을 함께 낸다.**

    ⛔ 팝업이 안 뜨고 있는 것은 에러가 아니라 침묵이다. 화면에서 "대상 N명 중
       M명 응답" 이 보여야 사람이 이상을 눈치챈다.
    """
    stats = fetch_one(
        "SELECT COUNT(*) AS asked, "
        "       SUM(rating IS NOT NULL) AS answered, "
        "       SUM(rating IS NULL) AS skipped, "
        "       AVG(rating) AS avg_rating, "
        "       SUM(comment IS NOT NULL AND comment <> '') AS with_comment "
        f"FROM {_TABLE}") or {}
    open_rows = fetch_all(
        f"SELECT status, COUNT(*) n FROM {_TABLE} s "
        f"WHERE {_attention_sql()} GROUP BY status") or []
    by = {(r["status"] or "new"): int(r["n"] or 0) for r in open_rows}
    avg = stats.get("avg_rating")
    return {
        "asked": int(stats.get("asked") or 0),
        "answered": int(stats.get("answered") or 0),
        "skipped": int(stats.get("skipped") or 0),
        "with_comment": int(stats.get("with_comment") or 0),
        "avg_rating": round(float(avg), 2) if avg is not None else None,
        "open": by.get("new", 0) + by.get("ack", 0),
        "by_status": by,
    }


def eligible_users(limit: int = 500) -> Dict[str, Any]:
    """지금 팝업 대상인 사람이 몇 명인가 (Admin 화면용).

    ⚠️ 목록이 아니라 **개수**만 센다. 응답률의 분모를 화면이 말할 수 있어야 한다.
    """
    try:
        rows = fetch_all(
            """SELECT x.user_id, COUNT(DISTINCT x.dt) AS d
                 FROM (
                   SELECT user_id, visit_date AS dt FROM user_visits
                   UNION
                   SELECT user_id, DATE(created_at) AS dt FROM conversations
                    WHERE user_id IS NOT NULL
                 ) x
                GROUP BY x.user_id
                LIMIT %s""", (int(limit),)) or []
        recorded = fetch_all(
            f"SELECT user_id, milestone FROM {_TABLE}") or []
        done: Dict[int, set] = {}
        for r in recorded:
            done.setdefault(int(r["user_id"]), set()).add(int(r["milestone"]))
        counts: Dict[int, int] = {}
        pending = 0
        for r in rows:
            uid, days = int(r["user_id"]), int(r["d"] or 0)
            for m in MILESTONES:
                if days >= m:
                    counts[m] = counts.get(m, 0) + 1
            if select_milestone(days, done.get(uid, set())) is not None:
                pending += 1
        return {"reached": counts, "pending_now": pending, "users": len(rows)}
    except Exception as e:
        logger.warning("survey_eligible_failed", error=str(e)[:160])
        return {"reached": {}, "pending_now": None, "users": 0}
