# -*- coding: utf-8 -*-
"""리뷰 데이터가 통합 테이블로 조회되는지, 그리고 라우팅이 새지 않는지 지킨다.

이주훈 님 제보 2건 (노션 AI Tester 공간, 2026-08-14):
  1. 플래그십 스토어 리뷰 DB 인식오류 — `Store_Review` 를 몰라 조회조차 못 했다.
     게다가 `플래그십` 이 팀 문서 낱말이라 notion 으로 새고 있었다
  2. 국내몰 리뷰 → 스마트스토어만 가져옴 — 몰별 테이블을 국내/해외로 통합했는데
     프롬프트·화이트리스트가 옛 4개에 머물러 있었다 (2026년 4,140건 → 실제 42,427건)

⛔ 둘 다 **에러가 나지 않는다.** 숫자가 그럴듯하게 작게 나올 뿐이라, 제보가 없었으면
   계속 틀린 채로 답했다.
"""
from pathlib import Path

import pytest

from app.agents.orchestrator import OrchestratorAgent
from app.config import get_settings

_PROMPT = Path("prompts/sql_generator.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def orc():
    return OrchestratorAgent.__new__(OrchestratorAgent)


class TestConsolidatedTablesAreAllowed:
    """화이트리스트에 없으면 `@@` 로는 되는데 일반 질문만 막힌다 (문서화된 함정)."""

    @pytest.mark.parametrize("table", [
        "Korea_mall_Review", "Oversea_mall_Review", "Store_Review",
    ])
    def test_whitelisted(self, table):
        allowed = get_settings().allowed_tables
        assert any(t.endswith("." + table) for t in allowed), f"{table} 누락"

    def test_legacy_kept(self):
        """통합 전 데이터를 콕 집어 묻는 질문이 있어 구 테이블도 남긴다."""
        allowed = get_settings().allowed_tables
        assert any(t.endswith(".New_Smartstore_Review") for t in allowed)


class TestPromptPointsAtConsolidated:
    def test_lists_consolidated_tables(self):
        for t in ("Korea_mall_Review", "Oversea_mall_Review", "Store_Review"):
            assert t in _PROMPT, f"프롬프트에 {t} 없음"

    def test_warns_against_legacy_first(self):
        """구 몰별 테이블을 기본으로 쓰지 말라는 경고가 남아 있어야 한다."""
        assert "몰별 테이블을 직접 쓰지 마라" in _PROMPT

    def test_store_review_is_not_product_review(self):
        """⚠️ 매장 리뷰에는 product_name 이 없다 — 제품 리뷰 집계에 섞이면 안 된다."""
        assert "매장 리뷰는 제품 리뷰가 아니다" in _PROMPT


class TestRoutingNoLongerLeaksToNotion:
    """`플래그십` 하나에 걸려 조회 질문이 문서 검색으로 새던 것."""

    @pytest.mark.parametrize("q", [
        "플래그십 스토어 리뷰 몇 건이야? 매장별로 알려줘",
        "플래그십 스토어 매출 알려줘",
        "국내몰 제품 리뷰 2026년 몇 개",
    ])
    def test_goes_to_bigquery(self, orc, q):
        assert orc._keyword_classify_ex(q)[0] == "bigquery"


class TestDocumentQuestionsStillNotion:
    """⛔ 통째로 뒤집으면 진짜 문서 질문이 조회로 샌다. 양방향을 함께 지킨다."""

    @pytest.mark.parametrize("q", [
        "연차 몇 개 남았어?",
        "조직도 알려줘",
        "플래그십 스토어 운영 매뉴얼 줘",
        "VPN 설정 방법",
        "리뷰 작성 가이드라인 알려줘",
    ])
    def test_goes_to_notion(self, orc, q):
        assert orc._keyword_classify_ex(q)[0] == "notion"
