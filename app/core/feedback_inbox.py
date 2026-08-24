# -*- coding: utf-8 -*-
"""붐따(👎) 처리함 — **읽히지 않던 피드백을 처리 대상으로 올린다.**

⛔ 2026-08-14 실측으로 드러난 구멍이다. 붐따는 이렇게 끝나고 있었다:

      사용자가 👎 + 코멘트 작성 → DB 저장 ✅
        → 개수만 집계 (급증하면 알림)
        → **내용은 아무도 읽지 않음** ❌

   `comment` 컬럼을 읽는 코드가 앱 전체에 하나도 없었다. 코멘트 39건이 넉 달간
   쌓여 있었고, 그중 "구글 워크스페이스쪽은 동작하지 않고 있어요"(08-05)는
   **9일 뒤** 같은 증상을 개발자가 직접 겪고서야 고쳐졌다. 제보가 닿는 경로가
   없었다.

   "매일 개선하는 시스템"(`SKIN1004-Nightly-Debug`)이 있긴 했지만 그건 **서버
   로그의 에러**를 봤지 붐따를 본 적이 없다. 게다가 7/09부터 멈춰 있었고
   `EXPECTED_JOBS` 에 없어서 그 침묵조차 감시되지 않았다.

설계 원칙 (이 프로젝트의 다른 배치와 같다):
  - **처리 상태를 기록할 곳을 만든다.** 없으면 "처리했다"를 남길 수 없어
    "인입은 됐는데 처리가 안 된 건지" 를 영영 답할 수 없다
  - 알림은 **새로 들어온 것만**. 미처리 총량을 매일 보내면 곧 무시당한다
    (자가 점검이 "상태가 바뀐 것만" 알리는 것과 같은 이유)
  - 판정·집계는 규칙이 한다. LLM 은 쓰지 않는다
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog

from app.db.mariadb import execute, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

# 처리 상태 — 노션 AI Tester 공간이 쓰는 어휘(미해결/해결완료)에 맞춘다
STATUS_NEW = "new"          # 아직 아무도 안 봄
STATUS_ACK = "ack"          # 봤고 처리 대상으로 인정
STATUS_DONE = "done"        # 고쳤음
STATUS_WONTFIX = "wontfix"  # 고치지 않기로 함 (사양·오입력·의미 없는 내용)
_STATUSES = (STATUS_NEW, STATUS_ACK, STATUS_DONE, STATUS_WONTFIX)

_COLUMNS = (
    ("status", f"VARCHAR(16) NOT NULL DEFAULT '{STATUS_NEW}'"),
    ("handled_at", "DATETIME NULL"),
    ("handled_by", "VARCHAR(120) NULL"),
    ("handled_note", "TEXT NULL"),
    # 제보자가 회신을 읽었는지. ⛔ 회신이 **닿았는지 모르면** 안 한 것과 같다
    ("reply_seen_at", "DATETIME NULL"),
)


def ensure_feedback_status_columns() -> None:
    """처리 상태 컬럼을 붙인다 (앱 기동 시 idempotent — FI 권한 컬럼과 같은 방식)."""
    for col, definition in _COLUMNS:
        try:
            if not fetch_one(
                "SELECT 1 AS ok FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'message_feedback' "
                "AND COLUMN_NAME = %s", (col,)):
                execute(f"ALTER TABLE message_feedback ADD COLUMN {col} {definition}")
        except Exception as e:
            logger.warning("feedback_status_column_error", col=col, error=str(e)[:160])
    try:
        execute("ALTER TABLE message_feedback ADD INDEX idx_mf_status (status)")
    except Exception:
        pass  # 이미 있음


def list_feedback(status: Optional[str] = None, only_down: bool = True,
                  limit: int = 200) -> List[Dict[str, Any]]:
    """처리함 목록. **코멘트가 있는 것을 먼저** 보여준다 — 읽을 게 있는 쪽이 값이 크다."""
    where = ["1=1"]
    params: list = []
    if only_down:
        where.append("f.rating < 0")
    if status:
        where.append("f.status = %s")
        params.append(status)
    rows = fetch_all(
        "SELECT f.id, f.rating, f.comment, f.created_at, f.status, f.handled_at, "
        "       f.handled_by, f.handled_note, f.conversation_id, f.message_id, "
        "       u.display_name AS user_name "
        "FROM message_feedback f LEFT JOIN users u ON u.id = f.user_id "
        f"WHERE {' AND '.join(where)} "
        # 코멘트 있는 것 우선 → 미처리 우선 → 최신순
        "ORDER BY (f.comment IS NOT NULL AND f.comment <> '') DESC, "
        "         (f.status = 'new') DESC, f.created_at DESC "
        "LIMIT %s", (*params, int(limit))) or []
    for r in rows:
        # ⚠️ 오래된 행은 status 가 기본값이라 NULL 이 아니지만, 컬럼 추가 직후를 대비
        r["status"] = r.get("status") or STATUS_NEW
    return rows


def set_status(feedback_id: int, status: str, who: str,
               note: Optional[str] = None) -> bool:
    """처리 상태를 바꾼다. 알 수 없는 상태는 거절한다 (오타로 조용히 사라지지 않게)."""
    if status not in _STATUSES:
        raise ValueError(f"unknown status: {status}")
    done = status in (STATUS_DONE, STATUS_WONTFIX)
    execute(
        "UPDATE message_feedback SET status = %s, handled_by = %s, handled_note = %s, "
        "handled_at = " + ("NOW()" if done else "NULL") + " WHERE id = %s",
        (status, who, note, int(feedback_id)))
    logger.info("feedback_status_changed", id=feedback_id, status=status, who=who)

    # 처리를 끝냈으면 제보자에게 메일로도 알린다 — **길이 열려 있을 때만** (기본 꺼짐).
    # 앱 알림(사이드바)은 이미 뜬다. 메일은 "답이 돌아온다"를 확실히 하는 보조 경로다.
    # ⚠️ 메일 실패가 상태 변경을 되돌리면 안 된다 — 여기서 예외를 밖으로 내지 않는다
    if done:
        try:
            from app.core import mailer
            if mailer.is_enabled():
                row = fetch_one(
                    "SELECT COALESCE(a.email, u.email) AS email, f.comment "
                    "FROM message_feedback f JOIN users u ON u.id = f.user_id "
                    "LEFT JOIN ad_users a ON a.id = u.ad_user_id WHERE f.id = %s",
                    (int(feedback_id),))
                if row and row.get("email"):
                    label = {STATUS_DONE: "해결됨", STATUS_WONTFIX: "고치지 않음"}[status]
                    mailer.feedback_handled(
                        row["email"], (row.get("comment") or "(내용 없음)")[:80], label, note or "")
        except Exception as e:
            logger.warning("feedback_mail_failed", id=feedback_id,
                           error=f"{type(e).__name__}: {str(e)[:200]}")
    return True


def summary() -> Dict[str, Any]:
    """상태별 집계 + 처리 지연 — Admin 배지와 다이제스트가 함께 쓴다."""
    rows = fetch_all(
        "SELECT status, COUNT(*) n, SUM(comment IS NOT NULL AND comment <> '') c "
        "FROM message_feedback WHERE rating < 0 GROUP BY status") or []
    by = {r["status"] or STATUS_NEW: {"n": int(r["n"] or 0), "with_comment": int(r["c"] or 0)}
          for r in rows}
    oldest = fetch_one(
        "SELECT MIN(created_at) t FROM message_feedback "
        "WHERE rating < 0 AND status = %s AND comment IS NOT NULL AND comment <> ''",
        (STATUS_NEW,)) or {}
    return {
        "by_status": by,
        "open": sum(by.get(s, {}).get("n", 0) for s in (STATUS_NEW, STATUS_ACK)),
        "open_with_comment": sum(by.get(s, {}).get("with_comment", 0)
                                 for s in (STATUS_NEW, STATUS_ACK)),
        "oldest_unread": oldest.get("t"),
    }


def run_daily_digest(hours: int = 24) -> Dict[str, Any]:
    """매일: **새로 들어온 붐따를 읽어 로그로 올린다.**

    ⛔ 미처리 총량을 매일 알리지 않는다 — 매일 같은 알림은 곧 무시당한다
       (자가 점검이 상태 변화만 알리는 것과 같은 판단). 새로 들어온 것만 본다.

    ⚠️ 잔디는 WAS 에서 403 이라 여기서 못 보낸다 (프록시 허용 범위가 서버마다
       다르다). 그래서 **WARNING 로그 + Admin 화면**이 전달 경로다 — 프로덕션은
       INFO 를 버리므로 반드시 WARNING 이어야 한다.
    """
    fresh = fetch_all(
        "SELECT f.id, f.comment, f.created_at, u.display_name AS user_name "
        "FROM message_feedback f LEFT JOIN users u ON u.id = f.user_id "
        "WHERE f.rating < 0 AND f.created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR) "
        "ORDER BY f.created_at", (int(hours),)) or []
    with_comment = [r for r in fresh if (r.get("comment") or "").strip()]
    stats = summary()
    result = {
        "new": len(fresh),
        "new_with_comment": len(with_comment),
        "open": stats["open"],
        "open_with_comment": stats["open_with_comment"],
        "oldest_unread": str(stats.get("oldest_unread") or ""),
    }
    if with_comment:
        # 내용을 로그에 실어야 "읽히지 않는" 상태가 끝난다. 길이는 잘라 둔다
        logger.warning(
            "feedback_digest_new", **result,
            items=[{"id": r["id"], "who": r.get("user_name"),
                    "comment": (r.get("comment") or "")[:200]} for r in with_comment[:15]])
    else:
        logger.info("feedback_digest_quiet", **result)
    return result


# ── 제보자 회신 ────────────────────────────────────────────────────────────
# ⛔ **회신 경로가 없던 것이 인입량 부진의 가장 유력한 원인이다** (2026-08-18 실측).
#    노션 채널은 제보마다 답글이 달려 8월 처리·회신 100% 인데, 앱은 회신 0건이었다.
#    전휘빈 님은 5~7월에 수치 오류를 4번 제보했고 **그중 3건이 같은 두 원인**이었다 —
#    답을 못 받으니 같은 것을 계속 겪으며 계속 신고한 것이다.
#    고친 사실을 돌려주지 않으면, 제보는 "밑 빠진 독"이 되고 곧 아무도 안 쓴다.


def replies_for_user(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """내 붐따 중 **처리되고 메모가 달린 것**. 안 읽은 것이 먼저 온다."""
    return fetch_all(
        "SELECT id, comment, created_at, status, handled_at, handled_note, "
        "       (reply_seen_at IS NULL) AS unseen "
        "FROM message_feedback "
        "WHERE user_id = %s AND status IN (%s, %s) "
        "  AND handled_note IS NOT NULL AND handled_note <> '' "
        "ORDER BY unseen DESC, handled_at DESC LIMIT %s",
        (int(user_id), STATUS_DONE, STATUS_WONTFIX, int(limit))) or []


def mark_replies_seen(user_id: int) -> int:
    """읽음 처리. ⚠️ 본인 것만 — user_id 를 **SQL 안에서** 건다
    (파이썬에서 먼저 확인하고 나중에 UPDATE 하면 확인을 빠뜨린 호출부가 언젠가 생긴다)."""
    return execute(
        "UPDATE message_feedback SET reply_seen_at = NOW() "
        "WHERE user_id = %s AND reply_seen_at IS NULL "
        "  AND handled_note IS NOT NULL AND handled_note <> ''",
        (int(user_id),))


def my_feedback(user_id: int, limit: int = 10, days: int = 90) -> List[Dict[str, Any]]:
    """내가 낸 붐따 — **처리 전 것도 포함**한다.

    회신(`replies_for_user`)은 메모가 달린 것만 돌려준다. 그것만 보여주면 사용자는
    "내가 신고한 게 접수는 됐나" 를 알 수 없다 — 답이 없는 구간이 길수록 제보가 끊긴다
    (2026-08-18 회신 0건 분석). 상태를 그대로 보여준다.

    ⚠️ 붐따는 **코멘트 없이 누르는 경우가 대부분**이라(실측: 최근 것 대부분 NULL)
       제목에 코멘트만 쓰면 "(내용 없음)" 이 줄줄이 뜬다. 그때는 **내가 물었던 질문**을
       제목으로 쓴다 — 무엇에 대한 신고인지 알아야 알림이 뜻을 갖는다.
    ⚠️ 오래된 것은 빼야 한다. 100일 전 **미처리** 건이 목록을 채우면 새 소식이 묻힌다.
    ⛔ 단 **회신은 나이와 무관하게 닿아야 한다.** 창을 그대로 두면 뒤늦게 처리 표시한
       건이 `handled_at` 만 채운 채 화면에 뜨지 않는다 — 에러 없는 고장이다
       (2026-08-24 실측: 묵은 붐따 16건을 처리했는데 7건이 창 밖이라 안 보였다).
       그래서 "최근이거나, 처리됐는데 아직 안 읽은 것" 으로 넓힌다. 읽으면 다시
       빠지므로 목록이 무한히 자라지 않는다.
    ⚠️ 안 읽음 판정은 `reply_seen_at < handled_at` 도 본다. 컬럼이 하나뿐이라
       "확인함" 단계에서 읽고 나중에 "해결" 로 바뀌면 다시 안 읽음이 돼야 한다.
    """
    return fetch_all(
        "SELECT f.id, f.comment, f.created_at, f.status, f.handled_at, f.handled_note, "
        "       LEFT(COALESCE(c.title, ''), 80) AS question, "
        "       (f.handled_at IS NOT NULL AND "
        "        (f.reply_seen_at IS NULL OR f.reply_seen_at < f.handled_at)) AS unseen "
        "FROM message_feedback f "
        # ⛔ `message_feedback.conversation_id` 만 collation 이 다르게 만들어져 있다
        #    (uca1400_ai_ci vs unicode_ci — 표를 만든 시기가 달라서다). 명시하지 않으면
        #    "Illegal mix of collations" 로 **조인 자체가 터진다**. 서버마다 기본값이
        #    다를 수 있으므로 스키마를 고치는 대신 조건에 못 박는다.
        "LEFT JOIN conversations c "
        "       ON c.id = f.conversation_id COLLATE utf8mb4_unicode_ci "
        "WHERE f.user_id = %s AND f.rating = -1 "
        "  AND (f.created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "       OR (f.handled_at IS NOT NULL "
        "           AND (f.reply_seen_at IS NULL OR f.reply_seen_at < f.handled_at))) "
        "ORDER BY unseen DESC, f.created_at DESC LIMIT %s",
        (int(user_id), int(days), int(limit))) or []


def mark_my_feedback_seen(user_id: int) -> int:
    """내 붐따 알림을 읽음으로. ⚠️ 본인 것만 — user_id 를 SQL 안에서 건다."""
    return execute(
        "UPDATE message_feedback SET reply_seen_at = NOW() "
        "WHERE user_id = %s AND rating = -1 AND handled_at IS NOT NULL "
        "  AND (reply_seen_at IS NULL OR reply_seen_at < handled_at)",
        (int(user_id),))
