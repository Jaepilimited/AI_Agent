# -*- coding: utf-8 -*-
"""회사 식별번호 즉답 관문 — 붐따 #154.

⛔ **양방향으로 지킨다.** 잡아야 할 것만 보면 반쪽이다 — 관문이 넓으면 노션이 답해야
   할 질문(서류 위치·절차)까지 삼켜서, 빨라지는 대신 **묻지 않은 것을 답하게** 된다.
"""
from __future__ import annotations

import re

import pytest

from app.core import company_facts


# ── 잡아야 하는 것 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "사업자등록번호",
    "회사 사업자등록번호 알려줘.",
    "우리회사 사업자등록번호",
    "크레이버 사업자번호가 뭐야?",
    "스킨1004 사업자번호는?",
    "사업자 등록번호",
])
def test_business_number_is_answered_instantly(q):
    out = company_facts.answer(q)
    assert out and company_facts.BUSINESS_NUMBER in out


def test_corporate_number_is_answered():
    out = company_facts.answer("법인등록번호 알려줘")
    assert out and company_facts.CORPORATE_NUMBER in out


def test_both_numbers_when_both_asked():
    out = company_facts.answer("사업자등록번호랑 법인등록번호 알려줘")
    assert company_facts.BUSINESS_NUMBER in out
    assert company_facts.CORPORATE_NUMBER in out


def test_answer_names_its_source():
    """⚠️ 어디서 온 값인지 밝힌다 — 틀렸을 때 어디를 고칠지 알 수 있어야 한다."""
    assert company_facts.SOURCE_NOTE in company_facts.answer("사업자등록번호")


# ── ⛔ 가로채면 안 되는 것 ───────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "영문 사업자등록증 어딧냥",            # 서류를 찾는 질문 (실제 로그)
    "사업자등록증 사본 어디 있어",
    "사업자등록증 pdf 다운로드",
    "법인카드 신청 어떻게 해",              # 절차 문서
    "법인서류 발급 절차",
    "거래처 사업자등록번호 확인해줘",        # 남의 회사
    "이번 달 매출 알려줘",
    "",
])
def test_questions_that_must_keep_their_original_route(q):
    assert company_facts.answer(q) is None, f"가로채면 안 되는 질문을 삼켰다: {q!r}"


def test_document_number_is_still_a_number_question():
    """'등록증 번호' 는 서류가 아니라 번호를 묻는 것이다."""
    out = company_facts.answer("사업자등록증 번호 알려줘")
    assert out and company_facts.BUSINESS_NUMBER in out


# ── 값 자체의 무결성 ─────────────────────────────────────────────────────────

def test_numbers_have_the_legal_shape():
    """⛔ 형식이 깨지면 대외 문서에 그대로 쓰인다. 값이 바뀔 땐 노션 원본을 확인할 것."""
    assert re.fullmatch(r"\d{3}-\d{2}-\d{5}", company_facts.BUSINESS_NUMBER)
    assert re.fullmatch(r"\d{6}-\d{7}", company_facts.CORPORATE_NUMBER)


def test_only_immutable_identifiers_live_here():
    """⛔ 바뀌는 값(대표자·주소)을 여기 넣지 마라 — 손으로 적으면 반드시 낡는다.

    실제로 direct 프롬프트의 주소가 노션과 이미 어긋나 있다(층수). 그런 값은 계속
    노션이 답해야 한다.
    """
    import inspect

    source = inspect.getsource(company_facts)
    body = source.split('"""', 2)[-1]          # 머리말 설명은 뺀다
    for banned in ("대표자", "대표이사", "테헤란로", "설립일"):
        assert f'{banned}:' not in body and f'"{banned}"' not in body, (
            f"바뀌는 값 '{banned}' 가 즉답 목록에 들어왔다"
        )


def test_both_orchestrator_paths_use_the_gate():
    """⛔ 한쪽 경로에만 달면 스트리밍/비스트리밍 답이 갈린다 (이 저장소의 단골 사고)."""
    import inspect

    from app.agents import orchestrator

    source = inspect.getsource(orchestrator)
    assert source.count("from app.core.company_facts import answer") == 2
