"""도메인이 겹치는 낱말의 라우팅 — 2026-08-19 점검에서 나온 두 자리.

① `일정·스케줄·캘린더` 가 개인 구글 캘린더(GWS)와 사내 프로모션 캘린더(BigQuery)에
   동시에 걸린다. 예전 가드는 "프로모션 낱말이 있으면 bigquery, 아니면 무조건 gws"
   한 줄이라 **"일본 큐텐 8월 일정"** 이 개인 캘린더로 갔다.
   → 낱말을 쌓지 않고 **범위(개인/회사)로 판정**하고, 둘 다 아니면 LLM 에 넘긴다.

② `플래그십·명동·뉴욕` 은 매장 리뷰 트리거이자 **매출 채널 이름**이다 (사용자 지적).
   단독 낱말이면 "플래그십 매출" 에도 리뷰 스키마가 붙어 LLM 이 엉뚱한 테이블을 볼 수 있다.
   → 장소 낱말은 **리뷰 낱말과 함께일 때만** 트리거한다 (튜플 = AND).
"""

from __future__ import annotations

import pytest

from app.agents.orchestrator import OrchestratorAgent
from app.agents.sql_agent import MARKETING_TABLES, _kw_hit


@pytest.fixture(scope="module")
def orch():
    return OrchestratorAgent.__new__(OrchestratorAgent)


# ── ① 일정 — 누구의 일정인가 ─────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "일본 큐텐 8월 일정 알려줘",          # 국가 + 몰 → 회사 축
    "인도네시아 쇼피 프로모션 일정",
    "다음달 프로모션 일정",
    "동남아시아1팀 8월 일정",             # 팀 → 회사 축
    "올리브영 행사 일정 있어?",
])
def test_company_scope_calendar_goes_to_bigquery(orch, q):
    assert orch._keyword_classify_ex(q) == ("bigquery", True)


@pytest.mark.parametrize("q", [
    "내 이번주 일정 알려줘",
    "오늘 회의 일정 뭐 있어",
    "캘린더에 회의 잡아줘",
])
def test_personal_scope_calendar_stays_gws(orch, q):
    assert orch._keyword_classify_ex(q) == ("gws", True)


def test_ambiguous_calendar_defers_to_llm(orch):
    """개인 것도 회사 것도 가리키지 않으면 **확신하지 않는다**.

    확신을 갖고 틀리면 LLM 재판정을 못 타 조용한 오답이 된다 — 이 저장소가
    반복해서 겪은 실패다. 느려지는 대신 틀리지 않는 쪽을 고른다.
    """
    route, confident = orch._keyword_classify_ex("이번주 일정 알려줘")
    assert confident is False


def test_mail_and_drive_unaffected(orch):
    """일정 판정이 다른 GWS 질문을 건드리면 안 된다."""
    assert orch._keyword_classify_ex("메일 확인해줘") == ("gws", True)
    assert orch._keyword_classify_ex("드라이브에서 교안 찾아줘") == ("gws", True)


# ── ② 플래그십 — 리뷰인가 매출인가 ───────────────────────────────────────────

def _tables_for(q: str) -> list:
    return [label for _p, label, kws in MARKETING_TABLES
            if any(_kw_hit(k, q.lower()) for k in kws)]


@pytest.mark.parametrize("q", [
    "플래그십 매출 알려줘",
    "명동 플래그십 8월 매출",
    "뉴욕 매장 매출 추이",
])
def test_flagship_sales_does_not_load_review_schema(q):
    """⛔ 매출을 묻는데 리뷰 테이블 스키마가 붙으면 안 된다 (매장은 판매 채널이기도 하다)."""
    assert "매장(플래그십) 리뷰" not in _tables_for(q)


@pytest.mark.parametrize("q", [
    "플래그십 스토어 리뷰 몇 건이야",
    "플래그십 리뷰 평점",
    "명동 매장 후기 어때",
    "매장 리뷰 최근 추이",
    "구글맵 별점 알려줘",
])
def test_store_review_still_triggers(q):
    assert "매장(플래그십) 리뷰" in _tables_for(q)


def test_and_keyword_semantics():
    """튜플은 AND 다 — 한쪽만 있으면 안 걸린다."""
    assert _kw_hit(("플래그십", "리뷰"), "플래그십 리뷰 평점") is True
    assert _kw_hit(("플래그십", "리뷰"), "플래그십 매출") is False
    assert _kw_hit("매장 리뷰", "매장 리뷰 추이") is True


# ── 손익(FI) 프롬프트 마스킹 — 실제 파일로 검사 ──────────────────────────────

def test_fi_masking_runs_against_the_real_prompt():
    """합성 픽스처만 보던 기존 테스트의 빈틈.

    프롬프트에서 섹션 번호나 제목을 바꾸면 정규식이 빗나가고, **에러 없이**
    권한 없는 사용자 프롬프트에 FI 스키마가 실린다. 서버에서는 같은 함수를
    자가 점검(`static_fi_mask`, CRITICAL)이 매일 부른다.
    """
    from app.core.static_checks import fi_prompt_masking

    ok, detail = fi_prompt_masking()
    assert ok, detail
