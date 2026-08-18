# -*- coding: utf-8 -*-
"""유가/무가 협업이 조회로 가고, 미상을 무가로 세지 않는지 지킨다.

배경 (2026-08-18): "유가 협업은?" 이 `direct·확신없음` 으로 떨어져 LLM 이 노션으로
보냈고, `유가` 가 사람 이름 **유가연** 에 걸려 엉뚱한 답이 나왔다.
원인은 `유가 협업` 이 `_DATA_KEYWORDS` 에만 있고 `_BIZ_CONTEXT` 에는 없어서다 —
두 목록은 서로 다른 질문("데이터인가" vs "우리 사업 맥락인가")에 답하는데 한쪽에만
넣으면 다른 관문에서 걸린다.

집계 규칙 (사용자 확정): 유가 = Cost_krw > 0 · 무가 = Cost_krw = 0 ·
**NULL(23.4%)은 미상이라 분모에서 제외하고 커버리지를 밝힌다.**
"""
from pathlib import Path

import pytest

from app.agents.orchestrator import OrchestratorAgent

_PROMPT = Path("prompts/sql_generator.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def orc():
    return OrchestratorAgent.__new__(OrchestratorAgent)


class TestRouting:
    @pytest.mark.parametrize("q", [
        "유가 협업은?", "무가협업은?", "유가협업 비용 알려줘",
        "무가 협업 몇 건", "인플루언서 유가 무가 비중",
    ])
    def test_goes_to_bigquery(self, orc, q):
        assert orc._keyword_classify_ex(q)[0] == "bigquery"

    @pytest.mark.parametrize("q", ["협업 툴 뭐 써?", "시딩 가이드라인 알려줘"])
    def test_not_hijacked_to_bigquery(self, orc, q):
        """⚠️ 맨 낱말 '협업' 을 넣지 않은 이유 — 툴·문서 질문이 조회로 새면 안 된다."""
        assert orc._keyword_classify_ex(q)[0] != "bigquery"


class TestBothKeywordListsAgree:
    """⛔ 한쪽 목록에만 넣으면 다른 관문에서 걸려 direct 로 강등된다 (이 사고의 원인)."""

    @pytest.mark.parametrize("term", ["유가 협업", "무가 협업", "유가협업", "무가협업"])
    def test_in_both_lists(self, term):
        assert term in OrchestratorAgent._DATA_KEYWORDS, f"_DATA_KEYWORDS 에 {term} 없음"
        assert term in OrchestratorAgent._BIZ_CONTEXT, f"_BIZ_CONTEXT 에 {term} 없음"

    def test_no_malformed_entry(self):
        """치환 사고로 '무가협업리뷰' 같은 붙은 항목이 생긴 적이 있다."""
        for lst in (OrchestratorAgent._DATA_KEYWORDS, OrchestratorAgent._BIZ_CONTEXT):
            for w in lst:
                assert "협업리뷰" not in w, f"붙어버린 항목: {w}"


class TestNullIsNotFree:
    """⛔ **미상을 무가로 세면 안 된다.**

    비용 미입력(68,056건 · 23.4%)은 *공짜*가 아니라 *모르는* 것이다.
    무가로 세면 무가가 54% → 77% 로 부풀고 유가 비중이 그만큼 깎인다.
    성분에서 "미상"을 "미포함"으로 썼다가 난 오답과 같은 종류다.
    """

    def test_rule_documented(self):
        assert "Cost_krw IS NULL` 을 무가로 세지 마라" in _PROMPT

    def test_exclusion_and_coverage_required(self):
        assert "분모에서 제외하고" in _PROMPT
        assert "커버리지" in _PROMPT or "제외한 건수를 답변에 반드시 밝힌다" in _PROMPT

    def test_example_sql_excludes_null(self):
        assert "WHERE Cost_krw IS NOT NULL" in _PROMPT

    def test_type_column_trap_documented(self):
        """`Type` 은 전 행이 NULL 이라 유가/무가를 여기서 찾으면 0건이 난다."""
        assert "`Type` 컬럼은 **전 행이 NULL 이다**" in _PROMPT

    def test_contact_type_not_confused(self):
        """`Contact_type`(직접·대행·올가닉)은 접촉 방식이지 유가/무가가 아니다."""
        assert "접촉 방식" in _PROMPT
