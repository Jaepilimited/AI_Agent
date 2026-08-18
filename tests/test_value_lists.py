# -*- coding: utf-8 -*-
"""프롬프트 값 목록이 데이터에서 오는지 지킨다.

⛔ 손으로 적은 값 목록은 **반드시 낡는다.** 하루에 세 번 겪었다 (2026-08-18):

    에콰도르   191개 중 12개만 나열하고 "등" → LLM 이 전체로 읽고 "없는 국가" 라 답함
    메가와리   2026 Q2 가 표에 없어 날짜를 지어냄 (40.2억 / 실제 62.2억)
    Continent1 `남미`·`중미` 가 **`중남미` 로 통합**됐는데 프롬프트만 옛 값 →
               "남미 매출" 이 0건이 났다

`prompts/sql_generator.txt` 1,573줄 중 23%가 이런 값 목록·스키마 표였다.
"""
from pathlib import Path

import pytest

from app.core import value_lists as vl

_PROMPT = Path("prompts/sql_generator.txt").read_text(encoding="utf-8")


class TestPlaceholdersInPrompt:
    """손으로 적던 목록이 자리표시자로 바뀌었는가."""

    @pytest.mark.parametrize("name", [
        "Country", "Continent1", "Continent2", "Line", "Category", "Team_NEW",
    ])
    def test_placeholder_present(self, name):
        assert "{{VALUES:%s}}" % name in _PROMPT

    def test_stale_continent_values_gone(self):
        """⛔ `남미`·`중미` 는 데이터에 없다 (`중남미` 로 통합). 손으로 적힌 흔적이
        남아 있으면 LLM 이 다시 그 값으로 필터해 0건을 낸다."""
        assert "'남미', '중미'" not in _PROMPT
        assert "**Country 실제 값 (DISTINCT" not in _PROMPT

    def test_every_placeholder_is_registered(self):
        """⚠️ 등록 안 된 이름은 빈 줄로 사라진다 — 목록이 통째로 없으면 LLM 이 지어낸다."""
        import re
        for name in re.findall(r"\{\{VALUES:(\w+)\}\}", _PROMPT):
            assert name in vl.REGISTRY, f"REGISTRY 에 {name} 없음"


class TestRegistry:
    def test_no_high_cardinality(self):
        """⚠️ 제품명·거래처처럼 수천 개인 컬럼을 넣으면 프롬프트가 터진다."""
        for name, (_, col, cap, _d) in vl.REGISTRY.items():
            assert cap <= 300, f"{name} 상한이 너무 크다"
            assert col.lower() not in ("set", "product", "company_name", "id")

    def test_ad_country_shares_korean_names(self):
        """광고 테이블의 country 는 매출과 같은 한글명이다 (조인 축)."""
        assert "AdCountry" in vl.REGISTRY


class TestFill:
    def test_unknown_placeholder_becomes_empty(self):
        assert vl.fill("앞 {{VALUES:없는이름}} 뒤") == "앞  뒤"

    def test_plain_text_untouched(self):
        t = "값 목록이 없는 평범한 문장"
        assert vl.fill(t) == t

    def test_empty_input(self):
        assert vl.fill("") == ""


class TestRegistered:
    def test_job_registered(self):
        from app.core.self_check import EXPECTED_JOBS
        assert "value_lists_daily" in EXPECTED_JOBS

    def test_self_check_registered(self):
        """⛔ 캐시가 비면 자리표시자가 사라진다 — 비었는지 반드시 감시해야 한다."""
        from app.core.self_check import CHECKS
        assert any(c.id == "value_lists" for c in CHECKS)

    def test_scheduled_in_main(self):
        src = Path("app/main.py").read_text(encoding="utf-8")
        assert 'id="value_lists_daily"' in src
