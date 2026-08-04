"""Quality monitor — daily snapshot of answer accuracy, context length, response speed.

Three metrics per route (computed from the last 24h):
  accuracy_rate  = 👍 / (👍 + 👎)  — from message_feedback
                   threshold < 0.65 → flag_accuracy
  avg_response_ms = mean(total_ms)  — from audit_logs
                   threshold > 12000ms → flag_speed
  avg_context_len = mean(context_len) — from audit_logs
                   threshold < 300 chars → flag_context (too shallow)

Called daily at midnight via APScheduler.  Can also be run manually:
  python scripts/compute_quality_snapshot.py
"""

from __future__ import annotations

import structlog
from datetime import date, datetime, timedelta, timezone

from app.db.mariadb import fetch_all, execute

logger = structlog.get_logger(__name__)

# Thresholds
_ACCURACY_MIN = 0.65
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
    flag_accuracy_global = int(global_accuracy is not None and global_accuracy < _ACCURACY_MIN)
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
        "flag_accuracy": bool(flag_accuracy_global),
    }
    if flag_accuracy_global:
        flags_found.append((_GLOBAL_ROUTE, [f"accuracy={global_accuracy:.2f}<{_ACCURACY_MIN}"]))
        logger.warning(
            "quality_threshold_exceeded",
            date=str(target_date),
            route=_GLOBAL_ROUTE,
            issues=flags_found[-1][1],
            request_count=total_fb,
        )

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
