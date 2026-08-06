"""Admin endpoints: user management, model access control (MariaDB)."""

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth_middleware import get_current_user
from app.db.mariadb import fetch_all, execute
from app.db.models import User

logger = structlog.get_logger(__name__)

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])

_ALL_MODELS = "skin1004-Analysis"


# ── Async DB wrappers ──

async def _db_fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    return await asyncio.to_thread(fetch_all, sql, params)

async def _db_execute(sql: str, params: tuple = ()) -> int:
    return await asyncio.to_thread(execute, sql, params)


def _require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


class UserListItem(BaseModel):
    id: int
    email: str
    name: str
    department: str
    role: str
    allowed_models: list[str]


class UpdateModelsRequest(BaseModel):
    allowed_models: list[str]


@admin_router.get("/users")
async def list_users(
    user: User = Depends(_require_admin),
) -> list[UserListItem]:
    """List all users with their model permissions."""
    users = await _db_fetch_all("""
        SELECT u.id, u.email, u.display_name, u.role, u.allowed_models,
               a.display_name as ad_name, a.email as ad_email, a.department
        FROM users u
        LEFT JOIN ad_users a ON u.ad_user_id = a.id
        ORDER BY u.created_at
    """)
    result = []
    for u in users:
        if u["role"] == "admin":
            models = [m.strip() for m in _ALL_MODELS.split(",") if m.strip()]
        else:
            raw = u.get("allowed_models") or ""
            models = [m.strip() for m in raw.split(",") if m.strip()]
            if not models:
                models = ["skin1004-Analysis"]
        result.append(UserListItem(
            id=u["id"],
            email=u.get("ad_email") or u.get("email") or "",
            name=u.get("ad_name") or u.get("display_name") or "",
            department=u.get("department") or "",
            role=u["role"],
            allowed_models=models,
        ))
    return result


@admin_router.put("/users/{user_id}/models")
async def update_user_models(
    user_id: int,
    req: UpdateModelsRequest,
    admin: User = Depends(_require_admin),
):
    """Update allowed models for a user."""
    from app.db.mariadb import fetch_one
    user = await asyncio.to_thread(
        fetch_one, "SELECT id, role, email FROM users WHERE id = %s", (user_id,)
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["role"] == "admin":
        raise HTTPException(status_code=400, detail="Cannot modify admin model access")

    valid = {m.strip() for m in _ALL_MODELS.split(",")}
    for m in req.allowed_models:
        if m not in valid:
            raise HTTPException(status_code=400, detail=f"Invalid model: {m}")

    await _db_execute(
        "UPDATE users SET allowed_models = %s WHERE id = %s",
        (",".join(req.allowed_models), user_id),
    )

    logger.info("admin_update_models", target=user["email"], models=req.allowed_models, by=admin.email)
    return {"ok": True, "email": user["email"], "allowed_models": req.allowed_models}


@admin_router.get("/quality-flags")
async def get_quality_flags(_: User = Depends(_require_admin)):
    """Return yesterday's quality snapshot flags. Admin only."""
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    rows = await _db_fetch_all(
        """SELECT route, flag_accuracy, flag_speed, flag_context,
                  accuracy_rate, avg_response_ms, avg_context_len, request_count
           FROM quality_snapshots
           WHERE snapshot_date = %s
             AND (flag_accuracy = 1 OR flag_speed = 1 OR flag_context = 1)
           ORDER BY route""",
        (yesterday,),
    )
    return {"date": yesterday, "flags": rows}


@admin_router.get("/knowledge-gaps")
async def get_knowledge_gaps(_: User = Depends(_require_admin)):
    """CS 지식 갭 목록. 최근 30일, 미검토 우선. Admin only."""
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=30)).isoformat()
    rows = await _db_fetch_all(
        """SELECT id, question, created_at, reviewed
           FROM knowledge_gaps
           WHERE created_at >= %s
           ORDER BY reviewed ASC, created_at DESC
           LIMIT 100""",
        (since,),
    )
    unreviewed = sum(1 for r in rows if not r["reviewed"])
    return {"since": since, "total": len(rows), "unreviewed": unreviewed, "gaps": rows}


@admin_router.patch("/knowledge-gaps/{gap_id}/review")
async def mark_gap_reviewed(gap_id: int, _: User = Depends(_require_admin)):
    """CS 지식 갭을 검토 완료로 표시."""
    await _db_execute(
        "UPDATE knowledge_gaps SET reviewed = 1 WHERE id = %s", (gap_id,)
    )
    return {"ok": True, "id": gap_id}


@admin_router.get("/growth-report")
async def get_growth_report(_: User = Depends(_require_admin)):
    """최신 주간 성장 리포트. 없으면 즉석 계산. Admin only."""
    from app.core.growth_report import get_latest_growth_report, compute_weekly_growth
    report = await asyncio.to_thread(get_latest_growth_report)
    if not report:
        report = await asyncio.to_thread(compute_weekly_growth)
    return report


@admin_router.post("/growth-report/refresh")
async def refresh_growth_report(_: User = Depends(_require_admin)):
    """주간 성장 리포트 수동 재계산. Admin only."""
    from app.core.growth_report import compute_weekly_growth
    report = await asyncio.to_thread(compute_weekly_growth)
    return report


@admin_router.get("/metrics")
async def get_metrics(admin: User = Depends(_require_admin)) -> dict:
    """Operational metrics: latency p50/p95, concurrency gates, DB pool, recent activity.

    Driven by the audit_logs table and live semaphore/pool state. Admin only.
    """
    from app.db.mariadb import _get_pool
    from app.core.llm import _GEMINI_SEM, _CLAUDE_SEM
    from app.core.bigquery import _BQ_SEM

    # Latency/p95/slow-query/active-user queries are independent — run concurrently.
    latency_1h, latency_24h, p95_rows, slow, active_rows = await asyncio.gather(
        _db_fetch_all("""
            SELECT route,
                   COUNT(*) AS cnt,
                   AVG(total_ms) AS avg_ms,
                   MAX(total_ms) AS max_ms
            FROM audit_logs
            WHERE created_at >= NOW() - INTERVAL 1 HOUR
            GROUP BY route
            ORDER BY cnt DESC
        """),
        _db_fetch_all("""
            SELECT COUNT(*) AS cnt,
                   AVG(total_ms) AS avg_ms,
                   MAX(total_ms) AS max_ms
            FROM audit_logs
            WHERE created_at >= NOW() - INTERVAL 24 HOUR
        """),
        # p95 computed in Python (MariaDB 10.x lacks PERCENTILE_CONT)
        _db_fetch_all("""
            SELECT total_ms FROM audit_logs
            WHERE created_at >= NOW() - INTERVAL 1 HOUR AND total_ms IS NOT NULL
            ORDER BY total_ms
        """),
        # Top slow queries (last 1h)
        _db_fetch_all("""
            SELECT user_email, route, query, total_ms, created_at
            FROM audit_logs
            WHERE created_at >= NOW() - INTERVAL 1 HOUR
            ORDER BY total_ms DESC
            LIMIT 10
        """),
        # Active users (last 15 min)
        _db_fetch_all("""
            SELECT COUNT(DISTINCT user_email) AS cnt
            FROM audit_logs
            WHERE created_at >= NOW() - INTERVAL 15 MINUTE
        """),
    )
    samples = [int(r["total_ms"]) for r in p95_rows if r["total_ms"] is not None]
    if samples:
        p50 = samples[len(samples) // 2]
        p95 = samples[int(len(samples) * 0.95)]
        p99 = samples[int(len(samples) * 0.99)]
    else:
        p50 = p95 = p99 = 0

    # DB pool state (DBUtils PooledDB internal)
    pool = _get_pool()
    pool_state = {
        "max_connections": getattr(pool, "_maxconnections", None),
        "connections_in_use": getattr(pool, "_connections", None),
        "idle_cached": len(getattr(pool, "_idle_cache", []) or []),
    }

    # Semaphore gates (available slots)
    gates = {
        "gemini_free": _GEMINI_SEM._value,
        "gemini_max": 30,
        "claude_free": _CLAUDE_SEM._value,
        "claude_max": 20,
        "bigquery_free": _BQ_SEM._value,
        "bigquery_max": 15,
    }

    active_users = int(active_rows[0]["cnt"]) if active_rows else 0

    return {
        "latency_1h_by_route": [
            {
                "route": r["route"],
                "cnt": int(r["cnt"]),
                "avg_ms": int(r["avg_ms"] or 0),
                "max_ms": int(r["max_ms"] or 0),
            }
            for r in latency_1h
        ],
        "latency_24h": {
            "cnt": int(latency_24h[0]["cnt"]) if latency_24h else 0,
            "avg_ms": int(latency_24h[0]["avg_ms"] or 0) if latency_24h else 0,
            "max_ms": int(latency_24h[0]["max_ms"] or 0) if latency_24h else 0,
        },
        "percentiles_1h": {"p50": p50, "p95": p95, "p99": p99, "sample_count": len(samples)},
        "slow_queries": [
            {
                "user": r["user_email"],
                "route": r["route"],
                "query": (r["query"] or "")[:80],
                "ms": int(r["total_ms"] or 0),
                "at": r["created_at"].isoformat() if r["created_at"] else "",
            }
            for r in slow
        ],
        "db_pool": pool_state,
        "concurrency_gates": gates,
        "active_users_15m": active_users,
    }


@admin_router.get("/wiki")
async def get_wiki_status(admin: User = Depends(_require_admin)) -> dict:
    """Knowledge wiki adoption dashboard — counts, freshness, samples."""
    totals = await _db_fetch_all(
        "SELECT status, COUNT(*) AS cnt FROM knowledge_wiki GROUP BY status"
    )
    by_domain = await _db_fetch_all(
        "SELECT domain, COUNT(*) AS cnt FROM knowledge_wiki "
        "GROUP BY domain ORDER BY cnt DESC"
    )
    recent = await _db_fetch_all("""
        SELECT id, domain, entity, period, metric, value, summary,
               source_route, confidence, status, extracted_at
        FROM knowledge_wiki
        ORDER BY id DESC LIMIT 20
    """)
    latest_extract = await _db_fetch_all(
        "SELECT MAX(extracted_at) AS last_at FROM knowledge_wiki"
    )

    return {
        "counts_by_status": {r["status"]: int(r["cnt"]) for r in totals},
        "counts_by_domain": [
            {"domain": r["domain"], "cnt": int(r["cnt"])} for r in by_domain
        ],
        "last_extracted_at": (
            latest_extract[0]["last_at"].isoformat()
            if latest_extract and latest_extract[0]["last_at"] else None
        ),
        "recent": [
            {
                "id": r["id"],
                "domain": r["domain"],
                "entity": r["entity"],
                "period": r["period"],
                "metric": r["metric"],
                "value": r["value"],
                "summary": r["summary"],
                "route": r["source_route"],
                "confidence": float(r["confidence"] or 0),
                "status": r["status"],
                "at": r["extracted_at"].isoformat() if r["extracted_at"] else "",
            }
            for r in recent
        ],
    }


class WikiFeedbackRequest(BaseModel):
    vote: str  # "up" | "down" | "resolve" | "restore"


@admin_router.post("/wiki/{wiki_id}/feedback")
async def wiki_feedback(
    wiki_id: int,
    req: WikiFeedbackRequest,
    admin: User = Depends(_require_admin),
) -> dict:
    """Adjust confidence, auto-archive on repeated downvotes, or resolve/restore."""
    vote = req.vote
    if vote not in ("up", "down", "resolve", "restore"):
        raise HTTPException(status_code=400, detail="invalid vote")

    if vote == "up":
        await _db_execute(
            "UPDATE knowledge_wiki "
            "SET thumbs_up = thumbs_up + 1, "
            "    confidence = LEAST(1.0, confidence + 0.1), "
            "    status = CASE WHEN status = 'pending' THEN 'active' ELSE status END, "
            "    validated_at = NOW() "
            "WHERE id = %s",
            (wiki_id,),
        )
    elif vote == "down":
        await _db_execute(
            "UPDATE knowledge_wiki "
            "SET thumbs_down = thumbs_down + 1, "
            "    confidence = GREATEST(0.0, confidence - 0.2), "
            "    review_status = 'needs_review', "
            # MySQL/MariaDB evaluate SET assignments left-to-right, so
            # `thumbs_down` here already reads the post-increment value set
            # above — do not add +1 again or this archives on the 1st vote.
            "    status = CASE WHEN thumbs_down >= 2 THEN 'archived' ELSE status END, "
            "    validated_at = NOW() "
            "WHERE id = %s",
            (wiki_id,),
        )
    elif vote == "resolve":
        # Admin confirms the problem is fixed. Clear the downvote counter,
        # lift the auto-archive if applied, and mark as resolved.
        await _db_execute(
            "UPDATE knowledge_wiki "
            "SET thumbs_down = 0, "
            "    review_status = 'resolved', "
            "    status = CASE WHEN status = 'archived' THEN 'active' ELSE status END, "
            "    confidence = GREATEST(0.5, confidence), "
            "    validated_at = NOW() "
            "WHERE id = %s",
            (wiki_id,),
        )
    else:  # restore — undo the archive, keep review_status as-is
        await _db_execute(
            "UPDATE knowledge_wiki "
            "SET status = 'active', validated_at = NOW() "
            "WHERE id = %s",
            (wiki_id,),
        )

    row = await _db_fetch_all(
        "SELECT id, status, review_status, confidence, thumbs_up, thumbs_down "
        "FROM knowledge_wiki WHERE id = %s",
        (wiki_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="wiki row not found")
    r = row[0]
    return {
        "ok": True,
        "id": r["id"],
        "status": r["status"],
        "review_status": r["review_status"],
        "confidence": float(r["confidence"] or 0),
        "thumbs_up": int(r["thumbs_up"]),
        "thumbs_down": int(r["thumbs_down"]),
    }


@admin_router.delete("/wiki/{wiki_id}")
async def wiki_delete(
    wiki_id: int,
    admin: User = Depends(_require_admin),
) -> dict:
    """Permanently delete a wiki fact."""
    rows = await _db_fetch_all(
        "SELECT id FROM knowledge_wiki WHERE id = %s", (wiki_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="wiki row not found")
    await _db_execute("DELETE FROM wiki_graph_edges "
                      "WHERE JSON_CONTAINS(source_wiki_ids, CAST(%s AS JSON))",
                      (wiki_id,))
    await _db_execute("DELETE FROM knowledge_wiki WHERE id = %s", (wiki_id,))
    logger.info("wiki_deleted", id=wiki_id, by=admin.email)
    return {"ok": True, "deleted_id": wiki_id}


@admin_router.get("/wiki/reports")
async def get_wiki_reports(admin: User = Depends(_require_admin)) -> dict:
    """Flagged facts — split into needs-review and resolved buckets."""
    needs = await _db_fetch_all("""
        SELECT id, domain, entity, period, metric, value, summary,
               confidence, thumbs_up, thumbs_down, status, review_status,
               source_route, extracted_at, validated_at
        FROM knowledge_wiki
        WHERE review_status = 'needs_review'
        ORDER BY thumbs_down DESC, validated_at DESC
        LIMIT 200
    """)
    resolved = await _db_fetch_all("""
        SELECT id, domain, entity, period, metric, value, summary,
               confidence, thumbs_up, thumbs_down, status, review_status,
               source_route, extracted_at, validated_at
        FROM knowledge_wiki
        WHERE review_status = 'resolved'
           OR (status = 'archived' AND validated_at >= NOW() - INTERVAL 30 DAY)
        ORDER BY validated_at DESC
        LIMIT 200
    """)

    def _fmt(row: dict) -> dict:
        return {
            "id": row["id"],
            "domain": row["domain"],
            "entity": row["entity"],
            "period": row["period"],
            "metric": row["metric"],
            "value": row["value"],
            "summary": row["summary"],
            "status": row["status"],
            "review_status": row["review_status"],
            "confidence": float(row["confidence"] or 0),
            "thumbs_up": int(row["thumbs_up"]),
            "thumbs_down": int(row["thumbs_down"]),
            "route": row["source_route"],
            "extracted_at": row["extracted_at"].isoformat() if row["extracted_at"] else "",
            "validated_at": row["validated_at"].isoformat() if row["validated_at"] else "",
        }

    return {
        "needs_review": [_fmt(r) for r in needs],
        "resolved": [_fmt(r) for r in resolved],
        "counts": {"needs_review": len(needs), "resolved": len(resolved)},
    }


@admin_router.get("/wiki/entity/{name}")
async def get_wiki_entity(name: str, admin: User = Depends(_require_admin)) -> dict:
    """Return the compiled entity page + raw fact list."""
    from app.knowledge.entity_pages import get_entity_page

    page = await asyncio.to_thread(get_entity_page, name)
    facts = await _db_fetch_all(
        """
        SELECT id, domain, entity, period, metric, value, summary,
               confidence, thumbs_up, thumbs_down, status, review_status,
               source_route, extracted_at
        FROM knowledge_wiki
        WHERE (canonical_entity = %s OR entity = %s) AND status <> 'archived'
        ORDER BY extracted_at DESC
        """,
        (name, name),
    )
    return {
        "entity": name,
        "page": {
            "markdown": page["markdown"] if page else None,
            "domain": page["domain"] if page else None,
            "fact_count": page["fact_count"] if page else 0,
            "period_span": page["period_span"] if page else None,
            "community_label": page["community_label"] if page else None,
            "compiled_at": page["compiled_at"].isoformat() if page and page["compiled_at"] else None,
        } if page else None,
        "facts": [
            {
                "id": r["id"], "domain": r["domain"], "period": r["period"],
                "metric": r["metric"], "value": r["value"], "summary": r["summary"],
                "confidence": float(r["confidence"] or 0),
                "status": r["status"], "review_status": r["review_status"],
                "thumbs_up": int(r["thumbs_up"]), "thumbs_down": int(r["thumbs_down"]),
                "extracted_at": r["extracted_at"].isoformat() if r["extracted_at"] else "",
            }
            for r in facts
        ],
    }


@admin_router.get("/wiki/insights")
async def get_wiki_insights(admin: User = Depends(_require_admin)) -> dict:
    from app.knowledge.wiki_insights import full_report

    report = await asyncio.to_thread(full_report)
    # Normalize datetimes/Decimals in SQL rows so JSON serializer is happy
    def _norm(row):
        out = dict(row)
        for k, v in list(out.items()):
            if hasattr(v, "isoformat"):
                out[k] = v.isoformat()
            elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
                out[k] = float(v)
        return out

    return {
        "god_nodes": [_norm(r) for r in report["god_nodes"]],
        "orphans": [_norm(r) for r in report["orphans"]],
        "surprising": [_norm(r) for r in report["surprising"]],
        "stale": [_norm(r) for r in report["stale"]],
        "contradictions": [_norm(r) for r in report["contradictions"]],
        "communities": [_norm(r) for r in report["communities"]],
        "suggested_queries": report["suggested_queries"],
    }


@admin_router.get("/wiki/graph")
async def get_wiki_graph(
    admin: User = Depends(_require_admin),
    limit: int = 200,
    full: bool = False,
) -> dict:
    """Return the top-N heaviest edges from the knowledge graph.

    When ``full=true`` the response also includes community colouring data
    for vis.js — each node gets a community_id and each edge gets an
    edge_type + confidence.
    """
    try:
        select = (
            "SELECT src_entity, dst_entity, relation, weight, community_id, "
            "edge_type, source_confidence, updated_at "
            "FROM wiki_graph_edges "
            "ORDER BY weight DESC LIMIT %s"
        )
        rows = await _db_fetch_all(select, (int(limit),))
    except Exception:
        return {"nodes": [], "edges": [], "total_edges": 0}

    nodes_map: dict[str, dict] = {}
    edges = []
    for r in rows:
        src = r["src_entity"]
        dst = r["dst_entity"]
        cid = r.get("community_id")
        nodes_map.setdefault(src, {"id": src, "community_id": cid})
        nodes_map.setdefault(dst, {"id": dst, "community_id": cid})
        edges.append({
            "src": src,
            "dst": dst,
            "relation": r["relation"],
            "weight": float(r["weight"] or 0),
            "edge_type": r.get("edge_type") or "inferred",
            "confidence": float(r.get("source_confidence") or 0),
            "community_id": cid,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else "",
        })

    payload = {
        "nodes": sorted(list(nodes_map.keys())) if not full else list(nodes_map.values()),
        "edges": edges,
        "total_edges": len(edges),
    }
    if full:
        communities = await _db_fetch_all(
            "SELECT id, label, size FROM wiki_communities ORDER BY size DESC LIMIT 30"
        )
        payload["communities"] = [
            {"id": c["id"], "label": c["label"], "size": int(c["size"] or 0)}
            for c in communities
        ]
    return payload


@admin_router.get("/wiki/map")
async def get_wiki_map(admin: User = Depends(_require_admin)) -> dict:
    """Hierarchical map of what the wiki knows.

    Returned as: domain → entity → {periods, metrics, fact_count, last_seen}

    This is the "지도" — the discovery surface the orchestrator will consult
    before routing a question (Week 2). For now it's admin-only, read-only.
    """
    rows = await _db_fetch_all("""
        SELECT domain, entity, period, metric, COUNT(*) AS cnt,
               MAX(extracted_at) AS last_at
        FROM knowledge_wiki
        WHERE status IN ('pending', 'active')
        GROUP BY domain, entity, period, metric
        ORDER BY domain, entity, period
    """)

    tree: dict[str, dict] = {}
    for r in rows:
        d = r["domain"] or "기타"
        e = r["entity"] or ""
        dom = tree.setdefault(d, {"entity_count": 0, "entities": {}})
        ent = dom["entities"].setdefault(
            e, {"periods": set(), "metrics": set(), "fact_count": 0, "last_seen": None}
        )
        if r["period"]:
            ent["periods"].add(r["period"])
        if r["metric"]:
            ent["metrics"].add(r["metric"])
        ent["fact_count"] += int(r["cnt"])
        if r["last_at"]:
            iso = r["last_at"].isoformat()
            if not ent["last_seen"] or iso > ent["last_seen"]:
                ent["last_seen"] = iso

    for d in tree.values():
        d["entity_count"] = len(d["entities"])
        d["entities"] = {
            name: {
                "periods": sorted(ent["periods"]),
                "metrics": sorted(ent["metrics"]),
                "fact_count": ent["fact_count"],
                "last_seen": ent["last_seen"],
            }
            for name, ent in d["entities"].items()
        }

    return {
        "total_domains": len(tree),
        "total_entities": sum(d["entity_count"] for d in tree.values()),
        "total_facts": sum(
            ent["fact_count"] for d in tree.values() for ent in d["entities"].values()
        ),
        "tree": tree,
    }


# ── 자가 점검 (self-check) ──────────────────────────────────────────────────
# 배치가 조용히 죽거나 데이터가 썩는 것을 사람이 눈치채기 전에 잡는다.
# 상세는 app/core/self_check.py 참조.


@admin_router.get("/self-check")
async def get_self_check(_: User = Depends(_require_admin)) -> dict:
    """최근 자가 점검 결과 + 추세."""
    from app.core.self_check import get_latest_self_check

    return await asyncio.to_thread(get_latest_self_check)


@admin_router.post("/self-check/run")
async def run_self_check_now(
    admin: User = Depends(_require_admin),
    auto_repair: bool = True,
    notify: bool = False,
) -> dict:
    """자가 점검 즉시 실행. 수동 실행은 기본적으로 알림을 보내지 않는다
    (사람이 화면을 보고 있으므로 잔디 알림은 소음이다)."""
    from app.core.self_check import run_self_check

    logger.info("self_check_manual_run", by=admin.email, auto_repair=auto_repair)
    return await asyncio.to_thread(run_self_check, auto_repair, notify)


@admin_router.get("/self-check/trend/{check_id}")
async def get_self_check_trend(check_id: str, _: User = Depends(_require_admin)) -> dict:
    """특정 검사의 이력 — '언제부터 깨졌나'를 답한다."""
    from app.core.self_check import get_check_trend

    return {"check_id": check_id, "history": await asyncio.to_thread(get_check_trend, check_id)}


# ── 사내 용어 사전 (term aliases) ────────────────────────────────────────────
# "센앰→센텔라 앰플" 같은 은어 치환 사전. 상세는 app/core/term_aliases.py.


@admin_router.get("/aliases")
async def list_aliases(_: User = Depends(_require_admin)) -> list:
    """용어 사전 전체. note 에 '추측' 이 있는 항목은 AI 유추라 검수가 필요하다."""
    return await asyncio.to_thread(
        fetch_all,
        "SELECT id, alias, canonical, category, note, created_at "
        "FROM term_aliases ORDER BY category, alias",
    )


class AliasCreate(BaseModel):
    alias: str
    canonical: str
    category: str = "product"
    note: str = ""


@admin_router.post("/aliases")
async def create_alias(req: AliasCreate, admin: User = Depends(_require_admin)) -> dict:
    from app.core.term_aliases import invalidate_cache

    alias = req.alias.strip()
    canonical = req.canonical.strip()
    if not alias or not canonical:
        raise HTTPException(status_code=400, detail="alias/canonical 이 비어 있습니다")
    await asyncio.to_thread(
        execute,
        "INSERT INTO term_aliases (alias, canonical, category, note) VALUES (%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE canonical=VALUES(canonical), category=VALUES(category), note=VALUES(note)",
        (alias[:100], canonical[:200], req.category[:30], req.note[:200]),
    )
    _purged = await asyncio.to_thread(
        execute, "DELETE FROM sql_cache WHERE query_text LIKE %s", (f"%{alias}%",)
    )
    invalidate_cache()
    logger.info("alias_upserted", alias=alias, canonical=canonical,
                purged_sql_cache=_purged, by=admin.email)
    return {"ok": True, "alias": alias, "canonical": canonical}


@admin_router.delete("/aliases/{alias_id}")
async def delete_alias(alias_id: int, admin: User = Depends(_require_admin)) -> dict:
    from app.core.term_aliases import invalidate_cache

    n = await asyncio.to_thread(execute, "DELETE FROM term_aliases WHERE id = %s", (alias_id,))
    invalidate_cache()
    logger.info("alias_deleted", alias_id=alias_id, by=admin.email)
    return {"ok": True, "deleted": n}


# ── 용어 후보 (미인식 용어 자동 수집) ────────────────────────────────────────
# 0건 답변이 나온 질문에서 수집된 후보. 승인해야 사전에 들어간다.


@admin_router.get("/aliases/candidates")
async def list_alias_candidates(_: User = Depends(_require_admin)) -> list:
    """대기 중인 용어 후보 — 자주 나온 순."""
    return await asyncio.to_thread(
        fetch_all,
        "SELECT id, term, occurrences, suggested_canonical, suggested_score, "
        "first_query, created_at, last_seen_at "
        "FROM term_alias_candidates WHERE status = 'pending' "
        "ORDER BY occurrences DESC, last_seen_at DESC LIMIT 100",
    )


class CandidateApprove(BaseModel):
    canonical: str = ""          # 비우면 suggested_canonical 사용
    category: str = "product"


@admin_router.post("/aliases/candidates/{cand_id}/approve")
async def approve_alias_candidate(
    cand_id: int, req: CandidateApprove, admin: User = Depends(_require_admin)
) -> dict:
    from app.db.mariadb import fetch_one as _fetch_one
    from app.core.term_aliases import invalidate_cache

    row = await asyncio.to_thread(
        _fetch_one, "SELECT term, suggested_canonical FROM term_alias_candidates WHERE id = %s",
        (cand_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="후보가 없습니다")
    canonical = (req.canonical or row.get("suggested_canonical") or "").strip()
    if not canonical:
        raise HTTPException(status_code=400, detail="canonical 을 지정해 주세요 (제안값 없음)")
    await asyncio.to_thread(
        execute,
        "INSERT INTO term_aliases (alias, canonical, category, note) VALUES (%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE canonical=VALUES(canonical)",
        (row["term"][:100], canonical[:200], req.category[:30], f"후보 승인 by {admin.email}"[:200]),
    )
    await asyncio.to_thread(
        execute, "UPDATE term_alias_candidates SET status='approved' WHERE id = %s", (cand_id,)
    )
    # 이 용어가 들어간 질문의 캐시된 SQL 은 보정 전에 생성된 것 — 지워야 새 규칙이 적용된다.
    # (프롬프트를 고쳐도 캐시가 옛 SQL 을 돌려주던 브랜드 건과 같은 함정)
    _purged = await asyncio.to_thread(
        execute, "DELETE FROM sql_cache WHERE query_text LIKE %s", (f"%{row['term']}%",)
    )
    invalidate_cache()
    logger.info("alias_candidate_approved", term=row["term"], canonical=canonical,
                purged_sql_cache=_purged, by=admin.email)
    return {"ok": True, "alias": row["term"], "canonical": canonical}


@admin_router.post("/aliases/candidates/{cand_id}/reject")
async def reject_alias_candidate(cand_id: int, admin: User = Depends(_require_admin)) -> dict:
    n = await asyncio.to_thread(
        execute, "UPDATE term_alias_candidates SET status='rejected' WHERE id = %s", (cand_id,)
    )
    logger.info("alias_candidate_rejected", cand_id=cand_id, by=admin.email)
    return {"ok": True, "updated": n}
