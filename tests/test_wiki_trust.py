"""Regression tests for trust-aware wiki context retrieval."""

from datetime import datetime, timedelta, timezone

from app.knowledge.entity_pages import _compile_markdown, search_entity_pages
from app.knowledge.trust import (
    DISPUTED,
    PARTLY_TRUSTED,
    STALE_RISK,
    TRUSTED,
    classify_fact_trust,
)
from app.knowledge.wiki_search import _build_candidate_query, format_facts_for_prompt


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


def _fact(**overrides):
    row = {
        "id": 1,
        "domain": "매출",
        "entity": "센텔라 앰플",
        "period": "2026-07",
        "metric": "sales",
        "value": "100",
        "summary": "2026년 7월 매출은 100원이다.",
        "confidence": 0.9,
        "status": "active",
        "review_status": "none",
        "conflict_with_id": None,
        "validated_at": NOW - timedelta(days=1),
        "extracted_at": NOW - timedelta(days=2),
        "source_route": "bigquery",
        "thumbs_up": 1,
        "thumbs_down": 0,
    }
    row.update(overrides)
    return row


def test_trust_state_prioritizes_unresolved_conflict():
    assert classify_fact_trust(
        _fact(review_status="needs_review", conflict_with_id=2), now=NOW
    ) == DISPUTED


def test_pending_fact_is_not_treated_as_trusted():
    assert classify_fact_trust(_fact(status="pending", validated_at=None), now=NOW) == PARTLY_TRUSTED


def test_reviewed_historical_fact_stays_trusted_when_old():
    fact = _fact(
        period="2024-Q1",
        validated_at=NOW - timedelta(days=400),
    )
    assert classify_fact_trust(fact, now=NOW) == TRUSTED


def test_old_permanent_fact_gets_stale_warning():
    fact = _fact(
        period="permanent",
        validated_at=NOW - timedelta(days=181),
    )
    assert classify_fact_trust(fact, now=NOW) == STALE_RISK


def test_prompt_format_exposes_trust_conflict_and_derived_provenance():
    text = format_facts_for_prompt([
        _fact(),
        _fact(
            id=2,
            status="pending",
            validated_at=None,
            summary="검토 중인 주장",
        ),
        _fact(
            id=3,
            review_status="needs_review",
            conflict_with_id=4,
            summary="서로 충돌하는 주장",
        ),
    ])
    assert "검증됨**만 확정 사실" in text
    assert "[검증 대기] 검토 중인 주장" in text
    assert "[충돌/검토 필요] 서로 충돌하는 주장" in text
    assert "conflict=#4" in text
    assert "이전 답변 추출" in text


def test_candidate_query_loads_review_and_conflict_metadata():
    sql, _ = _build_candidate_query(["센텔라"])
    assert "review_status" in sql
    assert "conflict_with_id" in sql
    assert "validated_at" in sql


def test_entity_tldr_never_promotes_disputed_fact():
    markdown = _compile_markdown(
        "센텔라 앰플",
        "매출",
        [
            _fact(summary="관리자가 확인한 사실"),
            _fact(
                id=2,
                summary="충돌 중인 사실",
                confidence=1.0,
                review_status="needs_review",
                conflict_with_id=1,
            ),
        ],
    )
    tldr = markdown.split("## TL;DR", 1)[1].split("## Timeline", 1)[0]
    assert "관리자가 확인한 사실" in tldr
    assert "충돌 중인 사실" not in tldr
    assert "[충돌/검토 필요] 충돌 중인 사실" in markdown


def test_page_search_rejects_stale_compiled_pages(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr("app.knowledge.entity_pages.fetch_all", fake_fetch_all)
    assert search_entity_pages("센텔라", 3) == []
    assert "p.fact_count =" in captured["sql"]
    assert "k.validated_at > p.compiled_at" in captured["sql"]
    assert "DATE_ADD(k.validated_at, INTERVAL 181 DAY)" in captured["sql"]
