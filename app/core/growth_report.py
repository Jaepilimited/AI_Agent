"""Weekly growth report — measures how the system is growing autonomously.

Metrics computed:
  - sql_cache_hits     : SQL 캐시 히트 수 (반복 질문 빠른 처리)
  - sql_cache_new      : 새로 저장된 SQL 패턴 수
  - skill_memory_pos   : 새로 저장된 👍 skill memory 수
  - skill_memory_neg   : 새로 저장된 👎 negative pattern 수
  - knowledge_gaps     : CS 지식 갭 감지 수
  - quality_trend      : 품질 추이 (accuracy, speed, context) 7일 평균 vs 이전 7일
  - wiki_node_count    : 현재 knowledge wiki 노드 수

Stored in `growth_snapshots` table (persists report history).
"""

import structlog
from datetime import date, timedelta
from typing import Any, Dict

logger = structlog.get_logger(__name__)

_GROWTH_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS growth_snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    week_start DATE NOT NULL UNIQUE,
    sql_cache_hits INT NOT NULL DEFAULT 0,
    sql_cache_new INT NOT NULL DEFAULT 0,
    skill_memory_pos INT NOT NULL DEFAULT 0,
    skill_memory_neg INT NOT NULL DEFAULT 0,
    knowledge_gaps INT NOT NULL DEFAULT 0,
    accuracy_avg FLOAT DEFAULT NULL,
    speed_avg_ms INT DEFAULT NULL,
    context_avg INT DEFAULT NULL,
    accuracy_prev FLOAT DEFAULT NULL,
    speed_prev_ms INT DEFAULT NULL,
    wiki_node_count INT DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def ensure_growth_snapshots_table():
    from app.db.mariadb import execute
    try:
        execute(_GROWTH_SNAPSHOTS_DDL)
    except Exception as e:
        logger.warning("growth_snapshots_table_error", error=str(e))


def compute_weekly_growth(days: int = 7) -> Dict[str, Any]:
    """Compute growth metrics for the past `days` days and persist to DB.

    Sync — call via asyncio.to_thread from async context.
    Returns the computed metrics dict.
    """
    from app.db.mariadb import fetch_one, fetch_all, execute

    today = date.today()
    week_start = today - timedelta(days=days)
    prev_start = week_start - timedelta(days=days)

    result: Dict[str, Any] = {"week_start": week_start.isoformat(), "week_end": today.isoformat()}

    # ── SQL 캐시 히트 (audit_logs에서 직접 집계 불가 — hit_count sum 사용) ──
    try:
        row = fetch_one(
            "SELECT COALESCE(SUM(hit_count), 0) AS hits, COUNT(*) AS total "
            "FROM sql_cache WHERE last_used_at >= %s",
            (week_start,),
        )
        result["sql_cache_hits"] = int(row["hits"]) if row else 0
        result["sql_cache_total"] = int(row["total"]) if row else 0
    except Exception as e:
        logger.warning("growth_sql_cache_error", error=str(e))
        result["sql_cache_hits"] = 0
        result["sql_cache_total"] = 0

    # 이번 주 새로 저장된 SQL 패턴 수
    try:
        row = fetch_one(
            "SELECT COUNT(*) AS cnt FROM sql_cache WHERE created_at >= %s",
            (week_start,),
        )
        result["sql_cache_new"] = int(row["cnt"]) if row else 0
    except Exception:
        result["sql_cache_new"] = 0

    # ── Skill memory: 이번 주 추가된 👍 / 👎 수 ──
    try:
        row = fetch_one(
            "SELECT "
            "  SUM(CASE WHEN is_negative = 0 THEN 1 ELSE 0 END) AS pos, "
            "  SUM(CASE WHEN is_negative = 1 THEN 1 ELSE 0 END) AS neg "
            "FROM agent_skills WHERE created_at >= %s",
            (week_start,),
        )
        result["skill_memory_pos"] = int(row["pos"] or 0) if row else 0
        result["skill_memory_neg"] = int(row["neg"] or 0) if row else 0
    except Exception as e:
        logger.warning("growth_skills_error", error=str(e))
        result["skill_memory_pos"] = 0
        result["skill_memory_neg"] = 0

    # ── CS 지식 갭 이번 주 감지 수 ──
    try:
        row = fetch_one(
            "SELECT COUNT(*) AS cnt FROM knowledge_gaps WHERE created_at >= %s",
            (week_start,),
        )
        result["knowledge_gaps"] = int(row["cnt"]) if row else 0
    except Exception:
        result["knowledge_gaps"] = 0

    # ── 품질 추이: 이번 주 vs 이전 주 ──
    def _quality_avg(from_date, to_date):
        try:
            rows = fetch_all(
                "SELECT accuracy_rate, avg_response_ms, avg_context_len "
                "FROM quality_snapshots "
                "WHERE snapshot_date >= %s AND snapshot_date < %s "
                "AND request_count > 0",
                (from_date, to_date),
            )
            if not rows:
                return None, None, None
            acc_vals = [r["accuracy_rate"] for r in rows if r["accuracy_rate"] is not None]
            spd_vals = [r["avg_response_ms"] for r in rows if r["avg_response_ms"] is not None]
            ctx_vals = [r["avg_context_len"] for r in rows if r["avg_context_len"] is not None]
            acc = round(sum(acc_vals) / len(acc_vals), 3) if acc_vals else None
            spd = round(sum(spd_vals) / len(spd_vals)) if spd_vals else None
            ctx = round(sum(ctx_vals) / len(ctx_vals)) if ctx_vals else None
            return acc, spd, ctx
        except Exception:
            return None, None, None

    acc, spd, ctx = _quality_avg(week_start, today)
    acc_prev, spd_prev, _ = _quality_avg(prev_start, week_start)

    result["quality_trend"] = {
        "accuracy_avg": acc,
        "speed_avg_ms": spd,
        "context_avg": ctx,
        "accuracy_prev": acc_prev,
        "speed_prev_ms": spd_prev,
        "accuracy_delta": round(acc - acc_prev, 3) if (acc is not None and acc_prev is not None) else None,
        "speed_delta_ms": round(spd - spd_prev) if (spd is not None and spd_prev is not None) else None,
    }

    # ── Wiki 노드 수 ──
    try:
        row = fetch_one("SELECT COUNT(*) AS cnt FROM knowledge_wiki")
        result["wiki_node_count"] = int(row["cnt"]) if row else 0
    except Exception:
        result["wiki_node_count"] = 0

    # ── Persist to growth_snapshots ──
    try:
        ensure_growth_snapshots_table()
        execute(
            """INSERT INTO growth_snapshots
               (week_start, sql_cache_hits, sql_cache_new, skill_memory_pos, skill_memory_neg,
                knowledge_gaps, accuracy_avg, speed_avg_ms, context_avg,
                accuracy_prev, speed_prev_ms, wiki_node_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                 sql_cache_hits=VALUES(sql_cache_hits), sql_cache_new=VALUES(sql_cache_new),
                 skill_memory_pos=VALUES(skill_memory_pos), skill_memory_neg=VALUES(skill_memory_neg),
                 knowledge_gaps=VALUES(knowledge_gaps), accuracy_avg=VALUES(accuracy_avg),
                 speed_avg_ms=VALUES(speed_avg_ms), context_avg=VALUES(context_avg),
                 accuracy_prev=VALUES(accuracy_prev), speed_prev_ms=VALUES(speed_prev_ms),
                 wiki_node_count=VALUES(wiki_node_count)""",
            (
                week_start,
                result["sql_cache_hits"], result["sql_cache_new"],
                result["skill_memory_pos"], result["skill_memory_neg"],
                result["knowledge_gaps"],
                acc, spd, ctx, acc_prev, spd_prev,
                result["wiki_node_count"],
            ),
        )
        logger.info("growth_snapshot_saved", week_start=week_start.isoformat())
    except Exception as e:
        logger.warning("growth_snapshot_save_failed", error=str(e))

    return result


def get_latest_growth_report() -> Dict[str, Any]:
    """Return the most recent growth report from growth_snapshots. Sync."""
    from app.db.mariadb import fetch_one
    try:
        ensure_growth_snapshots_table()
        row = fetch_one(
            "SELECT * FROM growth_snapshots ORDER BY week_start DESC LIMIT 1"
        )
        if not row:
            return {}
        return dict(row)
    except Exception as e:
        logger.warning("growth_report_load_failed", error=str(e))
        return {}
