"""Entity page compiler — Karpathy's "LLM wiki" applied to SKIN1004 facts.

Each entity gets one compiled markdown "page" aggregating every fact we know
about it, grouped by period and metric. The compiled page replaces scattered
fact rows as the primary retrieval unit for orchestrator context injection —
instead of pasting 10 disconnected lines, we paste one coherent summary.

Design:

- Input: all ``knowledge_wiki`` rows for a given canonical entity (status !=
  archived), including their review/conflict/validation metadata.
- Output: a markdown body stored in ``wiki_entity_pages.markdown``, plus
  metadata (fact_count, period_span, last_fact_at).
- Idempotent: compiling the same entity twice produces the same page; we
  overwrite on each compile.
- Incremental: ``ensure_entity_pages`` only touches entities whose facts
  were modified since the last compile.

Compilation steps for one entity:

1. Pull all facts. Split into ``permanent`` (period is NULL/"permanent")
   and ``timeline`` (period has a value).
2. Timeline: group by (year, metric) → bullet lines sorted chronologically.
3. Permanent: bullet the summary verbatim.
4. Prepend a 2-3 line TL;DR synthesized from the top-confidence facts.
   TL;DR is keyword-based, not LLM-generated, so the hot path stays cheap.
5. Append a source footer: total fact count, earliest/latest period, last
   updated timestamp.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

import structlog

from app.db.mariadb import execute, fetch_all, fetch_one
from app.knowledge.trust import (
    PARTLY_TRUSTED,
    TRUSTED,
    TRUST_LABELS,
    TRUST_ORDER,
    annotate_fact_trust,
)

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_YEAR_RE = re.compile(r"(\d{4})")


def _canonical_key(entity: str) -> str:
    return (entity or "").strip()


def _year_of(period: str | None) -> str:
    if not period:
        return "permanent"
    m = _YEAR_RE.search(period)
    return m.group(1) if m else period


def _period_sort(period: str | None) -> tuple:
    """Sort key that roughly chronologizes Korean period strings."""
    if not period:
        return (0, "")
    year_match = _YEAR_RE.search(period)
    year = int(year_match.group(1)) if year_match else 0
    return (year, period)


def _compute_period_span(facts: list[dict]) -> str | None:
    years = {_year_of(f.get("period")) for f in facts}
    years.discard("permanent")
    if not years:
        return None
    numeric = sorted(int(y) for y in years if y.isdigit())
    if not numeric:
        return None
    return f"{numeric[0]}~{numeric[-1]}" if numeric[0] != numeric[-1] else str(numeric[0])


# ------------------------------------------------------------------
# Page compilation
# ------------------------------------------------------------------

def _compile_markdown(entity: str, domain: str, facts: list[dict]) -> str:
    if not facts:
        return f"# {entity}\n\n_아직 기록된 팩트가 없습니다._\n"

    facts = [annotate_fact_trust(f) for f in facts]

    # Split
    permanent: list[dict] = []
    timeline: list[dict] = []
    for f in facts:
        period = (f.get("period") or "").strip()
        if not period or period.lower() == "permanent":
            permanent.append(f)
        else:
            timeline.append(f)

    # TL;DR — only reviewed facts are safe to summarize as assertions. If no
    # reviewed fact exists, keep the page useful but label the fallback.
    assertable = [f for f in facts if f["trust_state"] == TRUSTED]
    tldr_source = assertable or [
        f for f in facts if f["trust_state"] == PARTLY_TRUSTED
    ]
    ranked = sorted(
        tldr_source,
        key=lambda r: (
            float(r.get("confidence") or 0),
            _period_sort(r.get("period")),
            int(r.get("thumbs_up") or 0),
        ),
        reverse=True,
    )
    tldr_lines = []
    seen_summaries: set[str] = set()
    for r in ranked:
        summary = (r.get("summary") or "").strip()
        if not summary or summary in seen_summaries:
            continue
        seen_summaries.add(summary)
        tldr_lines.append(f"- [{TRUST_LABELS[r['trust_state']]}] {summary}")
        if len(tldr_lines) >= 3:
            break

    # Timeline grouped by year, then by metric
    year_groups: dict[str, list[dict]] = defaultdict(list)
    for f in timeline:
        year_groups[_year_of(f.get("period"))].append(f)

    # Permanent facts sorted by confidence
    permanent_sorted = sorted(
        permanent,
        key=lambda r: float(r.get("confidence") or 0),
        reverse=True,
    )

    # Build markdown
    out: list[str] = []
    out.append(f"# {entity}")
    if domain:
        out.append(f"**domain**: `{domain}`  ·  **facts**: {len(facts)}")
    out.append("")

    state_counts = {
        state: sum(1 for f in facts if f["trust_state"] == state)
        for state in TRUST_ORDER
    }
    out.append("## Trust status")
    out.append(" · ".join(
        f"{TRUST_LABELS[state]} {state_counts[state]}건"
        for state in TRUST_ORDER if state_counts[state]
    ))
    out.append("")
    out.append(
        "> 검증됨만 확정 사실로 사용하세요. 검증 대기는 원 데이터 재확인이 필요하고, "
        "충돌 항목은 어느 한쪽을 임의로 선택하면 안 됩니다."
    )
    out.append("")

    if tldr_lines:
        out.append("## TL;DR")
        out.extend(tldr_lines)
        out.append("")

    if year_groups:
        out.append("## Timeline")
        for year in sorted(year_groups.keys(), reverse=True):
            rows = sorted(year_groups[year], key=lambda r: _period_sort(r.get("period")))
            out.append(f"### {year}")
            for r in rows:
                period = r.get("period") or ""
                metric = r.get("metric") or ""
                value = r.get("value") or ""
                summary = (r.get("summary") or "").strip()
                tag_bits = []
                if period:
                    tag_bits.append(f"`{period}`")
                if metric:
                    tag_bits.append(f"`{metric}`")
                if value and value != summary:
                    tag_bits.append(f"**{value}**")
                tag = " · ".join(tag_bits)
                trust = TRUST_LABELS[r["trust_state"]]
                if tag:
                    out.append(f"- [{trust}] {summary}  _({tag})_")
                else:
                    out.append(f"- [{trust}] {summary}")
            out.append("")

    if permanent_sorted:
        out.append("## Permanent Facts")
        for r in permanent_sorted:
            summary = (r.get("summary") or "").strip()
            metric = r.get("metric") or ""
            if metric:
                out.append(f"- [{TRUST_LABELS[r['trust_state']]}] {summary}  _(`{metric}`)_")
            else:
                out.append(f"- [{TRUST_LABELS[r['trust_state']]}] {summary}")
        out.append("")

    # Footer
    last_at = max(
        (f.get("extracted_at") for f in facts if f.get("extracted_at")),
        default=None,
    )
    last_str = last_at.isoformat() if isinstance(last_at, datetime) else ""
    out.append("---")
    out.append(f"_compiled at {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
               f"{len(facts)} facts · last fact {last_str[:19]}_")
    return "\n".join(out)


def compile_entity_page(canonical_entity: str) -> dict:
    """Pull facts for one entity, compile markdown, upsert into wiki_entity_pages."""
    entity = _canonical_key(canonical_entity)
    if not entity:
        return {"ok": False, "reason": "empty_entity"}

    facts = fetch_all(
        """
        SELECT id, domain, entity, period, metric, value, summary,
               confidence, thumbs_up, thumbs_down, extracted_at, status,
               review_status, conflict_with_id, conflict_reason, validated_at,
               source_route, source_conversation_id, source_message_id
        FROM knowledge_wiki
        WHERE (canonical_entity = %s OR entity = %s)
          AND status <> 'archived'
        ORDER BY extracted_at DESC
        """,
        (entity, entity),
    )
    if not facts:
        # Prune a page that's now empty
        execute("DELETE FROM wiki_entity_pages WHERE canonical_entity = %s", (entity,))
        return {"ok": True, "fact_count": 0, "pruned": True}

    # Domain = most common across facts
    domain_counts: dict[str, int] = defaultdict(int)
    for f in facts:
        domain_counts[f.get("domain") or "기타"] += 1
    domain = max(domain_counts.items(), key=lambda x: x[1])[0]

    markdown = _compile_markdown(entity, domain, facts)
    period_span = _compute_period_span(facts)
    last_fact_at = max(
        (f.get("extracted_at") for f in facts if f.get("extracted_at")),
        default=None,
    )

    execute(
        """
        INSERT INTO wiki_entity_pages
            (canonical_entity, domain, markdown, fact_count, period_span, last_fact_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            domain = VALUES(domain),
            markdown = VALUES(markdown),
            fact_count = VALUES(fact_count),
            period_span = VALUES(period_span),
            last_fact_at = VALUES(last_fact_at)
        """,
        (entity, domain, markdown, len(facts), period_span, last_fact_at),
    )
    return {"ok": True, "fact_count": len(facts), "domain": domain}


# ------------------------------------------------------------------
# Batch compilation
# ------------------------------------------------------------------

def ensure_entity_pages(limit: int = 500, only_stale: bool = True) -> dict:
    """Compile pages for entities whose facts were updated after the page.

    Args:
        limit: safety cap — number of entities to process per call.
        only_stale: if True, skip entities whose page is fresher than any fact.
    """
    if only_stale:
        candidates = fetch_all(
            """
            SELECT COALESCE(k.canonical_entity, k.entity) AS entity
            FROM knowledge_wiki k
            LEFT JOIN wiki_entity_pages p
              ON p.canonical_entity = COALESCE(k.canonical_entity, k.entity)
            WHERE k.status <> 'archived'
              AND (p.compiled_at IS NULL OR k.extracted_at > p.compiled_at)
            GROUP BY entity
            LIMIT %s
            """,
            (int(limit),),
        )
    else:
        candidates = fetch_all(
            """
            SELECT DISTINCT COALESCE(canonical_entity, entity) AS entity
            FROM knowledge_wiki
            WHERE status <> 'archived'
            LIMIT %s
            """,
            (int(limit),),
        )

    compiled = 0
    pruned = 0
    for row in candidates:
        result = compile_entity_page(row["entity"])
        if result.get("pruned"):
            pruned += 1
        elif result.get("ok") and result.get("fact_count", 0) > 0:
            compiled += 1

    logger.info("entity_pages_ensured", compiled=compiled, pruned=pruned, seen=len(candidates))
    return {"compiled": compiled, "pruned": pruned, "seen": len(candidates)}


def get_entity_page(canonical_entity: str) -> dict | None:
    row = fetch_one(
        """
        SELECT canonical_entity, domain, markdown, fact_count, period_span,
               community_id, community_label, last_fact_at, compiled_at
        FROM wiki_entity_pages
        WHERE canonical_entity = %s
        """,
        (canonical_entity,),
    )
    return row


def search_entity_pages(query: str, limit: int = 5) -> list[dict]:
    """Simple LIKE-based entity lookup. Used by wiki_search to find candidate
    pages when the query mentions specific entity names.
    """
    tokens = [t for t in re.findall(r"[가-힣A-Za-z0-9]+", query) if len(t) >= 2]
    if not tokens:
        return []
    clauses = []
    params: list[str] = []
    for t in tokens[:6]:
        clauses.append("p.canonical_entity LIKE %s")
        params.append(f"%{t}%")
    where = " OR ".join(clauses)
    sql = f"""
        SELECT p.canonical_entity, p.domain, p.markdown, p.fact_count,
               p.period_span, p.last_fact_at
        FROM wiki_entity_pages p
        WHERE ({where})
          AND p.fact_count = (
              SELECT COUNT(*) FROM knowledge_wiki k
              WHERE (k.canonical_entity = p.canonical_entity OR k.entity = p.canonical_entity)
                AND k.status <> 'archived'
          )
          AND NOT EXISTS (
              SELECT 1 FROM knowledge_wiki k
              WHERE (k.canonical_entity = p.canonical_entity OR k.entity = p.canonical_entity)
                AND k.status <> 'archived'
                AND (
                    k.extracted_at > p.compiled_at
                    OR (k.validated_at IS NOT NULL AND k.validated_at > p.compiled_at)
                )
          )
          AND NOT EXISTS (
              SELECT 1 FROM knowledge_wiki k
              WHERE (k.canonical_entity = p.canonical_entity OR k.entity = p.canonical_entity)
                AND k.status = 'active'
                AND k.validated_at IS NOT NULL
                AND k.confidence >= 0.6
                AND k.review_status <> 'needs_review'
                AND (k.conflict_with_id IS NULL OR k.review_status = 'resolved')
                AND (k.period IS NULL OR k.period = '' OR LOWER(k.period) = 'permanent')
                AND DATE_ADD(k.validated_at, INTERVAL 181 DAY) > p.compiled_at
                AND DATE_ADD(k.validated_at, INTERVAL 181 DAY) <= NOW()
          )
        ORDER BY p.fact_count DESC
        LIMIT {int(limit)}
    """
    return fetch_all(sql, tuple(params))
