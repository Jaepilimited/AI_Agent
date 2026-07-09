"""Knowledge wiki extractor — turns past conversation answers into reusable facts.

Week 1 scope: shadow-mode extraction only. Reads user Q + assistant A from the
`messages` table, asks Gemini Flash to pull structured atomic facts, writes to
`knowledge_wiki`. No retrieval integration yet — that lands in Week 2.

Only conversations whose answers were rooted in hard data sources are mined,
to avoid extracting opinions/chat from general-knowledge routes:
    ALLOWED_ROUTES = {"bigquery", "notion", "multi"}

The extraction prompt demands strict JSON so we can round-trip without
post-processing. On JSON failure the batch is logged and skipped.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from app.core.llm import get_flash_client
from app.db.mariadb import fetch_all, execute, execute_lastid

logger = structlog.get_logger(__name__)


ALLOWED_ROUTES: set[str] = {"bigquery", "notion", "multi"}

# Domain labels — the LLM is told to pick one of these. Anything else → "기타".
ALLOWED_DOMAINS: set[str] = {"매출", "마케팅", "제품", "팀", "노션", "기타"}


_EXTRACTOR_PROMPT = """다음은 SKIN1004 사내 AI 에이전트의 질문과 답변입니다.
이 답변에서 **나중에 다른 동료에게도 유용할 재사용 가능한 원자 팩트**를 추출하세요.

## 질문
{query}

## 답변
{answer}

## 추출 규칙
1. **구체적**이어야 함 — 이름/숫자/기간/출처가 포함되는 사실만.
2. **재사용 가능**해야 함 — "오늘", "이 대화" 같은 일회성 언급 제외.
3. 답변에 실제로 나온 사실만. 없는 걸 지어내지 말 것.
4. 같은 제품·지표·기간 조합은 **한 번만**.
5. 최대 10개. 없으면 빈 배열.

## 필드 정의
- `domain`: 다음 중 하나 — 매출 | 마케팅 | 제품 | 팀 | 노션 | 기타
- `entity`: 주체 (예: "마다가스카 센텔라 토너 앰플", "JBT 팀", "Amazon US")
- `period`: 시점 (예: "2026-03", "2026-Q1", "2025-11-22~12-03", "permanent")
- `metric`: 측정 차원 (예: "sales_usd", "mom_growth", "category_rank", "campaign_period")
- `value`: 값 (예: "184000", "+12%", "3", "11/21~12/3")
- `summary`: 한두 문장, 동료가 읽고 바로 이해할 수 있게

## 출력 형식
JSON 배열만 출력하세요. 다른 텍스트·마크다운·코드블록 금지.

예시:
[
  {{"domain":"매출","entity":"마다가스카 센텔라 토너 앰플","period":"2026-03","metric":"amazon_sales_usd","value":"184000","summary":"마다가스카 센텔라 토너 앰플의 2026년 3월 아마존 매출은 $184K였다."}},
  {{"domain":"마케팅","entity":"큐텐 메가와리","period":"2026-Q1","metric":"campaign_period","value":"2026-02-27~03-11","summary":"2026년 Q1 큐텐 메가와리 캠페인은 2/27~3/11 진행되었다."}}
]
"""


@dataclass
class WikiFact:
    domain: str
    entity: str
    period: str | None
    metric: str | None
    value: str | None
    summary: str

    def normalize(self) -> "WikiFact":
        return WikiFact(
            domain=self.domain if self.domain in ALLOWED_DOMAINS else "기타",
            entity=(self.entity or "").strip()[:255],
            period=(self.period or None) and self.period.strip()[:64],
            metric=(self.metric or None) and self.metric.strip()[:128],
            value=(self.value or None) and str(self.value)[:2000],
            summary=(self.summary or "").strip(),
        )


def _canonicalize_entity_sync(entity: str) -> str:
    """Return the existing canonical entity name (case-insensitive lookup).

    If knowledge_wiki already has an entry whose name differs only in case,
    reuse that stored form so we never create a new case-variant entity.
    Falls back to the original string when no match exists.
    """
    try:
        rows = fetch_all(
            "SELECT entity FROM knowledge_wiki WHERE entity = %s LIMIT 1",
            (entity,),
        )
        return rows[0]["entity"] if rows else entity
    except Exception:
        return entity


# ------------------------------------------------------------------
# Core LLM call
# ------------------------------------------------------------------

def _clean_json_output(raw: str) -> str:
    """Strip accidental markdown fences from LLM output."""
    raw = raw.strip()
    if raw.startswith("```"):
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
        if match:
            return match.group(1).strip()
    return raw


def _salvage_partial_json_array(raw: str) -> list | None:
    """Best-effort recovery when Flash output is truncated mid-array.

    If the JSON array got cut off while emitting an element, peel the
    broken tail and close the array so we still capture the complete
    elements that came before.
    """
    raw = raw.strip()
    if not raw.startswith("["):
        return None
    # Walk backward looking for the last complete element boundary.
    last_complete = raw.rfind("},")
    if last_complete == -1:
        last_complete = raw.rfind("}")
        if last_complete == -1:
            return None
    candidate = raw[: last_complete + 1] + "]"
    try:
        data = json.loads(candidate)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


def _extract_facts_sync(query: str, answer: str) -> list[WikiFact]:
    """Blocking LLM call. Use via asyncio.to_thread from async contexts."""
    if not query.strip() or not answer.strip():
        return []

    prompt = _EXTRACTOR_PROMPT.format(query=query[:4000], answer=answer[:12000])

    # Use generate() (not generate_json) so we can pass max_output_tokens.
    # generate_json caps at 4096 which truncates Korean output mid-array.
    client = get_flash_client()
    try:
        raw = client.generate(
            prompt,
            temperature=0.0,
            max_output_tokens=12000,
        )
    except Exception as e:
        logger.warning("wiki_extractor_llm_failed", error=str(e)[:200])
        return []

    raw = _clean_json_output(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = _salvage_partial_json_array(raw)
        if data is None:
            logger.warning("wiki_extractor_json_parse_failed", preview=raw[:200])
            return []

    if not isinstance(data, list):
        return []

    facts: list[WikiFact] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            fact = WikiFact(
                domain=str(item.get("domain", "기타")),
                entity=str(item.get("entity", "")),
                period=item.get("period"),
                metric=item.get("metric"),
                value=item.get("value"),
                summary=str(item.get("summary", "")),
            ).normalize()
        except Exception:
            continue
        if fact.entity and fact.summary:
            facts.append(fact)
    return facts


# ------------------------------------------------------------------
# DB persistence
# ------------------------------------------------------------------

def _insert_facts_sync(
    facts: list[WikiFact],
    conversation_id: str | None,
    message_id: int | None,
    route: str | None,
) -> int:
    """Write extracted facts to knowledge_wiki, flagging contradictions.

    Contradiction rule: if a row already exists with the same
    (entity, period, metric) but a different ``value``, the new row and
    the existing row both get ``review_status='needs_review'`` and a
    ``conflict_reason`` note pointing at each other's id.
    """
    if not facts:
        return 0
    insert_sql = (
        "INSERT INTO knowledge_wiki "
        "(domain, entity, period, metric, value, summary, "
        "source_conversation_id, source_message_id, source_route, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')"
    )
    count = 0
    for f in facts:
        try:
            canonical_entity = _canonicalize_entity_sync(f.entity)
            # Skip exact duplicates (same entity+period+metric+value already stored).
            if f.period and f.metric and f.value:
                dup = fetch_all(
                    "SELECT id FROM knowledge_wiki "
                    "WHERE entity = %s AND period = %s "
                    "  AND metric = %s AND value = %s AND status <> 'archived' LIMIT 1",
                    (canonical_entity, f.period, f.metric, f.value),
                )
                if dup:
                    continue
            new_id = execute_lastid(
                insert_sql,
                (
                    f.domain, canonical_entity, f.period, f.metric, f.value, f.summary,
                    conversation_id, message_id, route,
                ),
            )
            count += 1
            # Best-effort conflict detection — only when all three keys are present.
            if new_id and f.entity and f.period and f.metric and f.value:
                _flag_conflict_sync(new_id, canonical_entity, f.period, f.metric, f.value)
        except Exception as e:
            logger.warning("wiki_insert_failed", error=str(e)[:200], entity=f.entity)
    return count


def _flag_conflict_sync(new_id: int, entity: str, period: str, metric: str, value: str) -> None:
    """If a sibling row exists with different value, flag both."""
    try:
        siblings = fetch_all(
            """
            SELECT id, value FROM knowledge_wiki
            WHERE entity = %s AND period = %s AND metric = %s
              AND id <> %s AND status <> 'archived'
            LIMIT 5
            """,
            (entity, period, metric, new_id),
        )
        mismatched = [s for s in siblings if (s.get("value") or "") != value]
        if not mismatched:
            return

        # Flag the new row
        sibling_ids = ",".join(str(s["id"]) for s in mismatched)
        execute(
            "UPDATE knowledge_wiki "
            "SET review_status = 'needs_review', "
            "    conflict_with_id = %s, "
            "    conflict_reason = %s "
            "WHERE id = %s",
            (mismatched[0]["id"], f"conflict with #{sibling_ids}", new_id),
        )
        # Flag each sibling
        for s in mismatched:
            execute(
                "UPDATE knowledge_wiki "
                "SET review_status = 'needs_review', "
                "    conflict_with_id = %s, "
                "    conflict_reason = %s "
                "WHERE id = %s",
                (new_id, f"conflict with #{new_id}", s["id"]),
            )
        logger.info(
            "wiki_conflict_flagged",
            new_id=new_id, siblings=[s["id"] for s in mismatched],
            entity=entity[:50], period=period, metric=metric,
        )
    except Exception as e:
        logger.warning("wiki_conflict_flag_failed", error=str(e)[:200])


# ------------------------------------------------------------------
# High-level API
# ------------------------------------------------------------------

async def extract_from_message(
    query: str,
    answer: str,
    *,
    conversation_id: str | None = None,
    message_id: int | None = None,
    route: str | None = None,
) -> tuple[int, str | None]:
    """Extract facts from a single Q/A pair and persist them.

    Returns ``(inserted_count, skip_reason)``. ``skip_reason`` is ``None`` on
    success. The caller is expected to record the outcome in
    ``wiki_extraction_log`` regardless of result — the extractor only owns
    knowledge_wiki writes.
    """
    if route and route not in ALLOWED_ROUTES:
        return 0, "route_filter"
    if not query.strip() or not answer.strip():
        return 0, "empty"

    facts = await asyncio.to_thread(_extract_facts_sync, query, answer)
    if not facts:
        return 0, "no_facts"

    inserted = await asyncio.to_thread(
        _insert_facts_sync, facts, conversation_id, message_id, route
    )
    return inserted, None


# ------------------------------------------------------------------
# Batch helpers — pull Q/A pairs out of messages table
# ------------------------------------------------------------------

def _fetch_pending_pairs_sync(since_minutes: int | None, limit: int) -> list[dict[str, Any]]:
    """Find user+assistant message pairs that haven't been wiki-extracted yet.

    A pair is "processed" when a row exists in wiki_extraction_log keyed by
    the assistant message_id — regardless of whether facts were actually
    extracted. Tracking skipped pairs is essential; otherwise the backfill
    loops forever on the same direct/cs-route messages.
    """
    time_filter = ""
    params: tuple = ()
    if since_minutes is not None:
        time_filter = "AND a.created_at >= NOW() - INTERVAL %s MINUTE"
        params = (since_minutes,)

    sql = f"""
        SELECT
            a.id AS assistant_message_id,
            a.conversation_id,
            a.content AS answer,
            c.model,
            (SELECT u.content FROM messages u
             WHERE u.conversation_id = a.conversation_id
               AND u.role = 'user'
               AND u.id < a.id
             ORDER BY u.id DESC LIMIT 1) AS query
        FROM messages a
        JOIN conversations c ON c.id = a.conversation_id
        LEFT JOIN wiki_extraction_log log ON log.message_id = a.id
        WHERE a.role = 'assistant'
          AND log.message_id IS NULL
          {time_filter}
        ORDER BY a.id DESC
        LIMIT {int(limit)}
    """
    return fetch_all(sql, params)


def _mark_processed_sync(message_id: int, extracted_count: int, reason: str | None) -> None:
    """Record that we've looked at this message so we don't re-fetch it."""
    try:
        execute(
            "INSERT INTO wiki_extraction_log (message_id, extracted_count, skipped_reason) "
            "VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE extracted_count = VALUES(extracted_count), "
            "    skipped_reason = VALUES(skipped_reason), processed_at = NOW()",
            (message_id, extracted_count, reason),
        )
    except Exception as e:
        logger.warning("wiki_log_insert_failed", error=str(e)[:200], message_id=message_id)


async def extract_batch(
    *,
    since_minutes: int | None = 60,
    limit: int = 100,
    max_concurrent: int = 4,
) -> dict[str, int]:
    """Pull recent unprocessed Q/A pairs and extract facts from each.

    Args:
        since_minutes: time window (None = all history — used by backfill).
        limit: safety cap on number of pairs per invocation.
        max_concurrent: how many Flash calls to run in parallel.

    Returns:
        Dict with counts: {pairs_seen, pairs_skipped, facts_written}
    """
    rows = await asyncio.to_thread(_fetch_pending_pairs_sync, since_minutes, limit)
    if not rows:
        return {"pairs_seen": 0, "pairs_skipped": 0, "facts_written": 0}

    sem = asyncio.Semaphore(max_concurrent)
    results = {"pairs_seen": len(rows), "pairs_skipped": 0, "facts_written": 0}

    async def _one(row: dict[str, Any]) -> None:
        msg_id = row["assistant_message_id"]
        q = (row.get("query") or "").strip()
        a = (row.get("answer") or "").strip()
        if not q or not a:
            results["pairs_skipped"] += 1
            await asyncio.to_thread(_mark_processed_sync, msg_id, 0, "empty")
            return
        route = _guess_route_sync(row["conversation_id"], msg_id)
        if route and route not in ALLOWED_ROUTES:
            results["pairs_skipped"] += 1
            await asyncio.to_thread(_mark_processed_sync, msg_id, 0, "route_filter")
            return
        async with sem:
            written, reason = await extract_from_message(
                q,
                a,
                conversation_id=row["conversation_id"],
                message_id=msg_id,
                route=route,
            )
        results["facts_written"] += written
        if reason:
            results["pairs_skipped"] += 1
        await asyncio.to_thread(_mark_processed_sync, msg_id, written, reason)

    await asyncio.gather(*(_one(r) for r in rows))
    logger.info("wiki_batch_extracted", **results)
    return results


def _guess_route_sync(conversation_id: str, message_id: int) -> str | None:
    """Look up the route of the nearest audit_log entry for this message.

    audit_logs doesn't store message_id so we correlate by conversation and
    timestamp proximity. Returns None when nothing is found, which makes the
    caller fall through without filtering — safer than guessing wrong.
    """
    try:
        rows = fetch_all(
            """
            SELECT al.route
            FROM audit_logs al
            JOIN messages m ON m.id = %s
            WHERE al.created_at BETWEEN m.created_at - INTERVAL 30 SECOND
                                    AND m.created_at + INTERVAL 30 SECOND
            ORDER BY ABS(TIMESTAMPDIFF(SECOND, al.created_at, m.created_at))
            LIMIT 1
            """,
            (message_id,),
        )
        return rows[0]["route"] if rows else None
    except Exception:
        return None


# ------------------------------------------------------------------
# Real-time capture: call after BQ/multi answer (fire-and-forget)
# ------------------------------------------------------------------

def extract_and_save_from_qa(query: str, answer: str, route: str = "bigquery") -> int:
    """BQ 답변에서 팩트를 즉시 추출해 knowledge_wiki에 저장한다.

    orchestrator._capture_bq_facts() → asyncio.to_thread(this) 로 호출.
    Sync함수: to_thread 래퍼 안에서 실행된다.
    중복·빈 답변은 조용히 스킵. 삽입 건수 반환.
    """
    if not query.strip() or not answer.strip():
        return 0
    # 답변이 너무 짧으면 팩트 없음 (오류 메시지 등)
    if len(answer.strip()) < 80:
        return 0
    # 오류 답변 스킵
    if answer.strip().startswith("⚠️") or "SQL" in answer[:50]:
        return 0

    facts = _extract_facts_sync(query, answer)
    if not facts:
        return 0

    inserted = _insert_facts_sync(facts, None, None, route)
    if inserted:
        logger.info("realtime_facts_captured", count=inserted, query=query[:60])
    return inserted
