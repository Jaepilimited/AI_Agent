# -*- coding: utf-8 -*-
"""캐시가 낡은 답을 다시 내주지 않는가 (2026-08-31, 사용자 제보).

실측: 프로덕션 `sql_cache` 762건 중 589건(77%)이 7일을 넘겼고, 가장 오래된
것은 3개월 전이었다. 가장 많이 재사용된 항목(66회)은 "2026년 월별 매출 추이"
-- **매달 정답이 바뀌는 질문**이었다. `last_used_at`은 히트마다 갱신되므로
자주 맞을수록 안 만료되는 역설이 있다 -- 절대 갱신되지 않는 `created_at`으로
판정해야 한다.

두 번째 방어선: 상대 기간("이번 달", 올해 맨 연도 등)을 담은 질문은 애초에
캐시에 쓰지도, 캐시에서 읽지도 않는다.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest

from app.agents import sql_agent as sa
from app.db import mariadb


@pytest.fixture(autouse=True)
def _clean_in_memory_cache():
    sa._sql_cache.clear()
    yield
    sa._sql_cache.clear()


# ── TTL: 낡은 항목은 서빙하지 않는다 ─────────────────────────────────────

def test_stale_in_memory_entry_is_not_served(monkeypatch):
    """8일 된 in-memory 항목은 서빙되지 않고, DB 도 미스면 완전히 미스다."""
    monkeypatch.setattr(mariadb, "fetch_one", lambda *a, **k: None)
    monkeypatch.setattr(mariadb, "execute", lambda *a, **k: 0)

    key = "stale_hash"
    sa._sql_cache[key] = ("SELECT 1 FROM stale", datetime.now() - timedelta(days=8))

    assert sa._cache_lookup(key) is None
    assert key not in sa._sql_cache  # 낡은 항목은 메모리에서도 지운다


def test_fresh_in_memory_entry_is_still_served(monkeypatch):
    """1일 된 항목은 그대로 서빙되고, in-memory 히트라 DB 까지 갈 필요가 없다."""
    called = {"db": False}

    def _fetch_one(*a, **k):
        called["db"] = True
        return None

    monkeypatch.setattr(mariadb, "fetch_one", _fetch_one)
    monkeypatch.setattr(mariadb, "execute", lambda *a, **k: 0)

    key = "fresh_hash"
    sa._sql_cache[key] = ("SELECT 1 FROM fresh", datetime.now() - timedelta(days=1))

    assert sa._cache_lookup(key) == "SELECT 1 FROM fresh"
    assert called["db"] is False


def test_db_backed_lookup_still_works_when_in_memory_misses(monkeypatch):
    """in-memory 에 없으면 DB 로 폴백하고, 찾으면 in-memory 를 데운다."""
    calls = []

    def _fetch_one(query, params):
        calls.append((query, params))
        return {"generated_sql": "SELECT 2 FROM db"}

    monkeypatch.setattr(mariadb, "fetch_one", _fetch_one)
    monkeypatch.setattr(mariadb, "execute", lambda *a, **k: 0)

    assert sa._cache_lookup("db_only_hash") == "SELECT 2 FROM db"
    assert calls and calls[0][1][0] == "db_only_hash"
    assert sa._sql_cache["db_only_hash"][0] == "SELECT 2 FROM db"


def test_db_ttl_filters_by_created_at_not_last_used_at():
    """⛔ `last_used_at` 은 히트마다 갱신돼, 66회 재사용된 낡은 SQL 을 안
    만료시켰다 -- 자주 맞힐수록 신선해 보이는 역설이 실제 사고였다."""
    src = inspect.getsource(sa._cache_lookup)
    assert "created_at > NOW() - INTERVAL" in src
    assert "last_used_at > NOW() - INTERVAL" not in src


def test_regenerating_a_stale_row_resets_its_freshness_clock():
    """⛔ INSERT ... ON DUPLICATE 가 created_at 을 다시 안 채우면, 막 새로
    만든 SQL 도 다음 조회에서 또 "3개월 전 태생"으로 남아 영원히 캐시가
    안 붙고 매번 재생성만 한다."""
    src = inspect.getsource(sa._cache_store)
    assert "created_at = NOW()" in src


# ── 상대 기간 질문은 캐시에 쓰지도, 읽지도 않는다 ────────────────────────

@pytest.mark.parametrize("query", [
    "이번 달 매출 알려줘",
    "2026년 월별 매출 추이 보여줘",  # 실제 66회 재사용된 그 질문
    "최근 3개월 매출 추이",
    "지난달 대비 매출 성장률",
    "올해 매출 얼마야",
    "현재까지 누적 매출",
    "지금까지 총 매출",
    "어제 매출 알려줘",
])
def test_relative_period_questions_are_flagged(query):
    assert sa._is_relative_period_query(query) is True


@pytest.mark.parametrize("query", [
    "2025년 4분기 매출",
    "2025년 3월 매출",
    "2024년 매출 알려줘",
    "2026년 상반기 매출",  # 오늘(2026-08-31) 기준 이미 끝난 반기 -- 절대 기간
    "2026년 3월 매출",     # 오늘 기준 이미 끝난 달
])
def test_absolute_period_questions_are_not_flagged(query):
    assert sa._is_relative_period_query(query) is False


def test_current_half_still_in_progress_is_flagged():
    """⚠️ "2026년 하반기"는 오늘(8/31)이 그 안이라 아직 진행 중이다 --
    끝난 반기(상반기)와 달리 계속 움직이는 상대 기간이어야 한다."""
    assert sa._is_relative_period_query("2026년 하반기 매출") is True


def test_generate_sql_skips_cache_lookup_for_relative_period_questions():
    """⛔ `generate_sql` 의 캐시 조회 관문에 상대 기간 검사가 실제로 걸려
    있는지 소스로 확인한다 -- 조건을 빠뜨리면 조용히 다시 캐시를 탄다."""
    src = inspect.getsource(sa.generate_sql)
    guard_line = next(
        line for line in src.splitlines() if "cache_key = _cache_key(query, brand_filter)" in line
    )
    # 바로 위 if 문에 두 조건이 함께 있어야 한다
    idx = src.splitlines().index(guard_line)
    if_line = src.splitlines()[idx - 1]
    assert "conv_context" in if_line
    assert "_is_relative_period_query(query)" in if_line


def test_generate_sql_skips_cache_store_for_relative_period_questions():
    src = inspect.getsource(sa.generate_sql)
    assert "_is_relative_period_query" in src


def test_validate_sql_node_skips_cache_store_for_relative_period_questions():
    src = inspect.getsource(sa.validate_sql_node)
    assert "_is_relative_period_query(_query)" in src
