# -*- coding: utf-8 -*-
"""정의서 → BigQuery 컬럼 설명 동기화가 안전한지 지킨다.

이 모듈은 **BigQuery 스키마를 수정한다**. 데이터는 안 건드리지만, SQL 을 문자열로
조립하므로 이스케이프와 식별자 검증이 무너지면 조용히 위험해진다.

배경: 컬럼의 **뜻**이 앱에 닿을 경로가 없었다. 앱은 이름·타입만 봤고 뜻은 프롬프트에
사람이 손으로 적어야 했다 — 그건 반드시 낡는다. 실제로 `Store_Review.shopname` 이
매장명인 줄 몰라 `channel` 로 찾아 "뉴욕 플래그십 0건"(실제 95건)이라고 답했다
(이주훈 님 제보 2026-08-14). 뜻은 노션 정의서에 이미 정확히 적혀 있었다.
"""
import re

import pytest

from app.core import schema_docs as sd


class TestLiteralEscaping:
    """⛔ 파라미터 바인딩이 없어 직접 이스케이프한다 — 여기가 뚫리면 DDL 이 깨진다."""

    def test_plain(self):
        assert sd._lit("매장명(명동 플래그십)") == "'매장명(명동 플래그십)'"

    def test_single_quote_escaped(self):
        out = sd._lit("it's")
        assert out.startswith("'") and out.endswith("'")
        assert "\\'" in out

    def test_backslash_escaped(self):
        assert sd._lit("a\\b") == "'a\\\\b'"

    def test_newlines_flattened(self):
        """⚠️ 개행이 남으면 DDL 이 여러 줄로 쪼개진다."""
        assert "\n" not in sd._lit("첫줄\n둘째줄")
        assert "\r" not in sd._lit("첫줄\r\n둘째줄")

    def test_none_safe(self):
        assert sd._lit(None) == "''"

    def test_quote_injection_stays_inside_literal(self):
        """따옴표를 닫고 DDL 을 이어붙이려는 값이 리터럴을 벗어나지 않는다."""
        out = sd._lit("x', description='y")
        assert out.count("'") - out.count("\\'") == 2   # 바깥 따옴표 한 쌍만 남는다


class TestIdentifierGuard:
    """⚠️ 식별자는 `_lit` 로 감싸지 않는다 — 정규식이 유일한 방어선이다."""

    @pytest.mark.parametrize("ok", ["Store_Review", "Korea_mall_Review", "_x", "a1"])
    def test_accepts_valid(self, ok):
        assert sd._IDENT.match(ok)

    @pytest.mark.parametrize("bad", [
        "Store Review", "a-b", "1abc", "a;DROP", "`x`", "a.b", "", "a'b",
    ])
    def test_rejects_invalid(self, bad):
        assert not sd._IDENT.match(bad)


class TestPropertyExtraction:
    def test_title(self):
        p = {"type": "title", "title": [{"plain_text": "Store_Review"}]}
        assert sd._plain(p) == "Store_Review"

    def test_rich_text_joined(self):
        p = {"type": "rich_text", "rich_text": [{"plain_text": "매장명"}, {"plain_text": "(명동)"}]}
        assert sd._plain(p) == "매장명(명동)"

    def test_select(self):
        assert sd._plain({"type": "select", "select": {"name": "Review_Data"}}) == "Review_Data"

    def test_missing(self):
        assert sd._plain(None) == ""
        assert sd._plain({"type": "select", "select": None}) == ""


class TestDescriptionLength:
    def test_cap_is_modest(self):
        """설명이 길면 스키마 프롬프트가 부풀어 조회가 느려진다."""
        assert 100 <= sd._MAX_DESC <= 500


class TestJobRegistered:
    """⛔ 등록 안 하면 잡이 죽어도 아무도 모른다 (Nightly-Debug 가 그렇게 36일 조용했다)."""

    def test_expected_jobs(self):
        from app.core.self_check import EXPECTED_JOBS
        assert "schema_docs_daily" in EXPECTED_JOBS

    def test_scheduled_in_main(self):
        from pathlib import Path
        src = Path("app/main.py").read_text(encoding="utf-8")
        assert 'id="schema_docs_daily"' in src
        assert re.search(r"_schema_docs_job.*hour=3", src)
