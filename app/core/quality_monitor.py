"""Quality monitor — daily snapshot of answer accuracy, context length, response speed.

Three metrics per route (computed from the last 24h):
  accuracy_rate  = 👍 / (👍 + 👎)  — from message_feedback
                   **기록만 하고 경고하지 않는다** (아래 ⛔ 참고)
  avg_response_ms = mean(total_ms)  — from audit_logs
                   threshold > 12000ms → flag_speed
  avg_context_len = mean(context_len) — from audit_logs
                   threshold < 300 chars → flag_context (too shallow)

⛔ 정확도 경고는 끈다 (2026-09-17 사용자 결정). 이 비율은 정확도가 아니다:
   사람들은 **틀렸을 때 👎 를 누르고, 괜찮으면 아무것도 안 누른다.** 그래서

       2026-09-16: 요청 56건 · 피드백 3건(전부 👎) · 나머지 53건 무반응 → "정확도 0%"

   표본 하한도 없어 👎 한 건이면 0% 다 (9/9 는 n=1). 9/3 이후 피드백이 있던 **6일
   전부** 경고가 떴다 — 매일 뜨는 경고는 곧 아무도 안 읽고, 그러면 진짜 경고(속도)
   까지 함께 무시당한다. 👍 버튼 고장도 의심해 확인했지만 정상이었다 (👍·👎 가 같은
   경로이고 마지막 👍(8/19) 전후로 코드가 바뀐 적이 없다. 👍 는 원래 주 0~3건이다).
   - 실제 정답률은 **골든셋**이 본다 (매일 05:30). 👎 한 건 한 건은 **붐따 처리함**이 본다
   - ⚠️ 값은 계속 저장한다 — `growth_report.py` 가 이 행을 읽는다. 끈 것은 경고뿐이다
   - ⚠️ 속도 기준 12s 는 **그대로 둔다** (같은 날 사용자 결정). medium 실측 중앙값
     13.3s 보다 낮아 자주 뜨지만, 더 빠르게 만들 여지를 계속 보겠다는 선택이다

Called daily at midnight via APScheduler.  Can also be run manually:
  python scripts/compute_quality_snapshot.py
"""

from __future__ import annotations

import structlog
from datetime import date, datetime, timedelta, timezone

from app.db.mariadb import fetch_all, execute

logger = structlog.get_logger(__name__)

# Thresholds
# ⛔ 정확도에는 경고 임계가 없다 — 모듈 머리말 참고. 되살리지 마라.
_RESPONSE_MAX_MS = 12_000
_CONTEXT_MIN_LEN = 300

# message_feedback / messages / conversations carry no route column, and audit_logs has
# no message_id/conversation_id FK — so a feedback row's route can't be attributed
# reliably. Accuracy is stored globally under this pseudo-route instead of being
# fabricated per-route (see compute_daily_snapshot).
_GLOBAL_ROUTE = "_all"


def compute_daily_snapshot(target_date: date | None = None) -> dict:
    """Compute quality metrics for target_date (defaults to yesterday).

    Returns a dict with per-route results for debugging/logging.
    """
    if target_date is None:
        # messages.created_at / audit_logs.created_at are naive server-local time, which
        # is KST — not UTC. Deriving "yesterday" from UTC-now is wrong for the first 9
        # hours of each KST day (UTC is still on the previous calendar day there).
        kst_now = datetime.now(timezone(timedelta(hours=9)))
        target_date = (kst_now - timedelta(days=1)).date()

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    # ── Accuracy: 👍/👎 totals for the day (global only — see _GLOBAL_ROUTE) ──
    feedback_simple = fetch_all(
        """
        SELECT
            SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) AS thumbs_up,
            SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END) AS thumbs_down
        FROM message_feedback
        WHERE created_at BETWEEN %s AND %s
        """,
        (day_start, day_end),
    )

    # ── Speed + Context: from audit_logs per route ──
    perf_rows = fetch_all(
        """
        SELECT route,
               COUNT(*) AS request_count,
               AVG(total_ms) AS avg_response_ms,
               AVG(context_len) AS avg_context_len
        FROM audit_logs
        WHERE created_at BETWEEN %s AND %s
          AND route IS NOT NULL
        GROUP BY route
        """,
        (day_start, day_end),
    )

    total_up = int((feedback_simple[0].get("thumbs_up") or 0) if feedback_simple else 0)
    total_down = int((feedback_simple[0].get("thumbs_down") or 0) if feedback_simple else 0)
    total_fb = total_up + total_down
    global_accuracy = (total_up / total_fb) if total_fb > 0 else None
    total_requests = sum(int(row.get("request_count") or 0) for row in perf_rows)

    results = {}
    flags_found = []

    for row in perf_rows:
        route = row.get("route") or "unknown"
        req_count = int(row.get("request_count") or 0)
        avg_ms = int(row.get("avg_response_ms") or 0)
        avg_ctx = int(row.get("avg_context_len") or 0)

        flag_speed = int(avg_ms > _RESPONSE_MAX_MS)
        flag_context = int(req_count > 5 and avg_ctx < _CONTEXT_MIN_LEN)

        # accuracy_rate/feedback_count are NULL here — they aren't attributable to this
        # route (see _GLOBAL_ROUTE). Only avg_response_ms/avg_context_len/request_count
        # come from audit_logs, which does carry a real per-route breakdown.
        execute(
            """
            INSERT INTO quality_snapshots
                (snapshot_date, route, accuracy_rate, feedback_count,
                 avg_response_ms, avg_context_len, request_count,
                 flag_accuracy, flag_speed, flag_context)
            VALUES (%s, %s, NULL, 0, %s, %s, %s, 0, %s, %s)
            ON DUPLICATE KEY UPDATE
                accuracy_rate = VALUES(accuracy_rate),
                feedback_count = VALUES(feedback_count),
                avg_response_ms = VALUES(avg_response_ms),
                avg_context_len = VALUES(avg_context_len),
                request_count = VALUES(request_count),
                flag_accuracy = VALUES(flag_accuracy),
                flag_speed = VALUES(flag_speed),
                flag_context = VALUES(flag_context)
            """,
            (
                target_date, route,
                avg_ms, avg_ctx, req_count,
                flag_speed, flag_context,
            ),
        )

        results[route] = {
            "accuracy_rate": None,
            "avg_response_ms": avg_ms,
            "avg_context_len": avg_ctx,
            "request_count": req_count,
            "flag_accuracy": False,
            "flag_speed": bool(flag_speed),
            "flag_context": bool(flag_context),
        }

        flagged = []
        if flag_speed:
            flagged.append(f"avg_ms={avg_ms}>{_RESPONSE_MAX_MS}")
        if flag_context:
            flagged.append(f"ctx_len={avg_ctx}<{_CONTEXT_MIN_LEN}")
        if flagged:
            flags_found.append((route, flagged))
            logger.warning(
                "quality_threshold_exceeded",
                date=str(target_date),
                route=route,
                issues=flagged,
                request_count=req_count,
            )

    # ── Global accuracy row: the only place a real (non-fabricated) accuracy_rate is
    # stored. request_count is set to the day's total across all routes (rather than 0)
    # so growth_report.py's `WHERE request_count > 0` filter still picks this row up.
    # ⛔ 경고하지 않는다 — 값만 남긴다 (모듈 머리말). 0 으로 적어야 경고 막대가 안 읽는다.
    flag_accuracy_global = 0
    execute(
        """
        INSERT INTO quality_snapshots
            (snapshot_date, route, accuracy_rate, feedback_count,
             avg_response_ms, avg_context_len, request_count,
             flag_accuracy, flag_speed, flag_context)
        VALUES (%s, %s, %s, %s, NULL, NULL, %s, %s, 0, 0)
        ON DUPLICATE KEY UPDATE
            accuracy_rate = VALUES(accuracy_rate),
            feedback_count = VALUES(feedback_count),
            request_count = VALUES(request_count),
            flag_accuracy = VALUES(flag_accuracy)
        """,
        (target_date, _GLOBAL_ROUTE, global_accuracy, total_fb, total_requests, flag_accuracy_global),
    )
    results[_GLOBAL_ROUTE] = {
        "accuracy_rate": global_accuracy,
        "feedback_count": total_fb,
        "flag_accuracy": False,
    }

    if flags_found:
        logger.warning(
            "quality_snapshot_flagged",
            date=str(target_date),
            flagged_routes=[r for r, _ in flags_found],
        )
    else:
        logger.info(
            "quality_snapshot_ok",
            date=str(target_date),
            routes=list(results.keys()),
        )

    return {"date": str(target_date), "routes": results, "flags": flags_found}
