# -*- coding: utf-8 -*-
"""질문의 기능어가 Gmail 검색어로 새면 **에러가 아니라 0건**이 난다.

2026-09-07 실측. 붐따 #148·#149 를 고친 뒤에도 실사용에서 실패했던 질문들을
그대로 넣어 보니, 만들어지는 질의에 아직 기능어가 남아 있었다:

    "내 이메일 중에서 최근 1주일 미팅 관련된 내용 정리해줘."
        → newer_than:7d **1주일 중에서** 미팅        ← 앞 둘은 뜻이 없다
    "최신메일이 뭐지?"
        → **뭐지**                                  ← 검색어가 이것뿐
    "제목에 \"Office Toolkit 회의록\" 포함된 메일 …"
        → toolkit office 회의록 **포함된**

Gmail 은 낱말을 AND 로 묶으므로 이런 말이 하나만 남아도 결과가 0 에 가까워진다.
좁혀 찾기 사다리가 있지만, 사다리는 **신호가 약한 것부터** 떼는데 `1주일` 은
숫자가 섞여 있어 가장 센 낱말로 올라가 끝까지 살아남는다.
"""

import pytest

from app.agents.gws_agent import build_gmail_query


def terms(question: str):
    """연산자를 뺀 순수 검색어만."""
    return [t for t in build_gmail_query(question).split() if ":" not in t]


LEAKS = [
    ("내 이메일 중에서 최근 1주일 미팅 관련된 내용 정리해줘.", {"1주일", "중에서"}, "미팅"),
    ("내 이메일 중에서 최근 1주일 브랜드 부분 관련된 내용만 추려줘.", {"1주일", "중에서"}, "브랜드"),
    ('제목에 "Office Toolkit 회의록" 포함된 메일 전부 찾아서 요약해줘', {"포함된"}, "회의록"),
]


@pytest.mark.parametrize("question,leaked,keep", LEAKS)
def test_기능어가_검색어로_새지_않는다(question, leaked, keep):
    got = terms(question)
    assert not (set(got) & leaked), f"{got} — {leaked & set(got)} 가 남았다"
    assert keep in got, f"{got} — 정작 뜻이 있는 {keep!r} 가 빠졌다"


def test_최신메일이_뭐지는_검색어가_남지_않는다():
    """검색어가 `뭐지` 하나뿐이면 그 질의는 0건을 부른다."""
    got = terms("최신메일이 뭐지?")
    assert "뭐지" not in got, got


def test_기간_낱말은_연산자로만_쓰인다():
    """`1주일` 은 기간이지 본문에 있는 말이 아니다 — 연산자로 번역되고 사라진다."""
    q = build_gmail_query("최근 1주일 미팅 메일 정리해줘")
    assert "newer_than:7d" in q, q
    assert "1주일" not in q, q


def test_원래_잘_되던_것은_그대로다():
    """사람 이름·기간 처리(붐따 #148·#149 수정)를 깨뜨리지 않는다."""
    q = build_gmail_query(
        "최근에 Christopher 로부터 Exolyt 구독 갱신 관련 이메일이 왔어. 내용 뭔지 알려줘.")
    assert "from:Christopher" in q, q
    assert "newer_than:7d" in q, q
    assert "exolyt" in q.lower(), q


def test_최신메일_질문이_빈_질의가_되지_않는다():
    """빈 질의는 아무것도 보내지 않으므로 항상 0건이다 — 0건과 구분이 안 된다."""
    q = build_gmail_query("최신메일이 뭐지?")
    assert q.strip(), "질의가 통째로 비었다"
    assert "newer_than" in q, q
