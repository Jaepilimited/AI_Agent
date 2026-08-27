# -*- coding: utf-8 -*-
"""브랜드를 지목한 질문에 전사 매출이 나가지 않는가.

⛔ 실측(2026-08-27, 붐따 #100): "SKIN1004 상반기 매출액" 에 `WHERE Date BETWEEN ...`
   만 걸린 SQL 이 나가 **5,577.5억**을 답했다. 스킨천사 브랜드는 **4,322.0억** —
   **1,255억(29%) 부풀려진 오답**이다. 사용자가 든 기준값(4,282.6억)과 0.9% 차이로
   브랜드 해석이 맞았다.

⚠️ `SKIN1004` 는 **회사명이자 브랜드명**이라 LLM 이 "우리 회사 전체" 로 읽는다.
   프롬프트에 브랜드 표가 있어도 확률이라 새어 나간다 — 보증은 코드다.
"""
import inspect

from app.agents import sql_agent as sa


def test_brand_words_are_detected():
    assert sa._brand_named_in("SKIN1004 상반기 매출액 얼마임?") == "skin1004"
    assert sa._brand_named_in("우마 7월 매출") == "우마"
    assert sa._brand_named_in("좀비뷰티 매출") == "좀비뷰티"


def test_company_wide_questions_are_left_alone():
    """⚠️ 전사를 물었을 때 브랜드를 끼우면 반대 방향의 오답이 된다."""
    assert sa._brand_named_in("전사 매출 알려줘") == ""
    assert sa._brand_named_in("일본 매출 알려줘") == ""


def test_brand_vocabulary_is_not_copied():
    """⛔ 같은 규칙을 두 곳에서 따로 적으면 언젠가 한쪽만 고쳐진다 —
       보고서가 쓰는 `_BRAND_FILTERS` 를 그대로 쓴다."""
    src = inspect.getsource(sa._brand_named_in)
    assert "from app.reports.registry import _BRAND_FILTERS" in src


def test_a_select_only_mention_does_not_count_as_filtering():
    """`SELECT Brand` 만 있고 거르지 않으면 여전히 전사다."""
    assert not sa._sql_filters_brand("SELECT SUM(x) FROM t WHERE Date > '1'")
    assert sa._sql_filters_brand("SELECT SUM(x) FROM t WHERE Brand IN ('SK','CBT')")
    assert sa._sql_filters_brand("SELECT CASE WHEN Brand='UM' THEN 1 END FROM t")


def test_the_cache_cannot_bypass_the_guard():
    """⛔ **캐시가 보증을 통째로 건너뛴다.** 실측: 코드를 고쳐 배포했는데 답이 그대로
       5,577.5억이었다 — 캐시에 브랜드 없는 SQL 이 남아 있었다.
       "고쳤는데 그대로면 캐시를 의심하라" 는 규칙이 그대로 재현됐다.
       ⚠️ 나쁜 행은 지운다 — 남겨 두면 다음 사람에게 또 나간다."""
    src = inspect.getsource(sa.generate_sql)
    guard = src.split("cached_sql = _cache_lookup", 1)[1].split("return {", 1)[0]
    assert "_brand_named_in(query)" in guard
    assert "_sql_filters_brand(cached_sql)" in guard
    assert "_cache_forget" in guard


def test_the_generation_path_retries_once():
    src = inspect.getsource(sa.generate_sql)
    assert "sql_brand_filter_missing" in src
    assert "sql_brand_filter_recovered" in src
    # 되살리지 못했으면 조용히 넘기지 않는다
    assert "sql_brand_filter_unrecovered" in src
