# -*- coding: utf-8 -*-
"""메일 검색이 **오타 하나로 0건**이 되지 않는지 지킨다.

사고 원문 (2026-08-14 사용자 제보): `오늘 메일 요야ㅐㄱ` → "검색 결과가 없습니다".
바로 다시 친 `오늘 메일 요약` 은 정상 동작했다. 불용어를 **부분 문자열로** 지운
탓에 꼬리 `요야` 가 검색어로 남아 Gmail 이 0건을 돌려줬다.

⛔ 이 테스트는 **불용어를 더 쌓는 방식으로 통과시키면 안 된다.** 오타는 목록으로
   끝이 없다 — 드라이브 동의어 사전을 걷어낸 것과 같은 판단이다.
"""
from datetime import datetime

import pytest

from app.agents.gws_agent import build_gmail_query

_NOW = datetime(2026, 8, 14, 15, 0)


def _kw(question: str):
    """연산자를 뺀 **검색어만** 돌려준다."""
    return [t for t in build_gmail_query(question, now=_NOW).split() if ":" not in t]


class TestTypoLeavesNoKeyword:
    @pytest.mark.parametrize("question", [
        "오늘 메일 요야ㅐㄱ",     # 제보된 원문 (낱자 섞임)
        "오늘 메일 요약해조",      # 어미 오타
        "어제 메일 정리해줭",
        "오늘 메일 요약해죠",
        "오늘 메일 정리해ㅜ",
    ])
    def test_typo_behaves_like_correct_spelling(self, question):
        assert _kw(question) == [], f"오타 꼬리가 검색어로 남았다: {_kw(question)}"

    def test_correct_spelling_baseline(self):
        assert _kw("오늘 메일 요약") == []

    def test_date_range_still_built(self):
        q = build_gmail_query("오늘 메일 요야ㅐㄱ", now=_NOW)
        assert "after:2026/08/14" in q and "before:2026/08/15" in q


class TestRealTermsSurvive:
    """⚠️ 오타를 지우려다 **진짜 검색어까지 지우면** 더 나쁘다."""

    @pytest.mark.parametrize("question,expected", [
        ("오늘 메일 중 환율 관련", "환율"),          # 2글자여도 뗀 게 없으면 남는다
        ("이번주 메일에서 면세 찾아줘", "면세"),
        ("McKinsey 메일 요약", "mckinsey"),
        ("크리스비 메일 정리해줘", "크리스비"),
        ("오늘 메일 중 스프레드시트 공유 건", "스프레드시트"),
    ])
    def test_keyword_kept(self, question, expected):
        assert expected in _kw(question)

    def test_operator_is_preserved(self):
        q = build_gmail_query("from:boss 오늘 메일 요약", now=_NOW)
        assert "from:boss" in q
