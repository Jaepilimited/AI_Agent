# -*- coding: utf-8 -*-
"""후속 발화가 앞 대화의 경로를 잇는지, 주제 전환은 정상으로 갈리는지 지킨다.

사고 재현 (2026-08-18):
    턴1 "2026년 7월 미국 매출 얼마야?"  → bigquery  219.7억
    턴2 "센텔라 앰플은?"                → **CS 로 납치** (제품 소개문이 나왔다)
    턴3 "일본사업팀은?"                 → bigquery 복귀

턴2 에서 기대한 것은 "7월 미국에서 센텔라 앰플 매출" 이다. 맥락이 매출인데 제품명
하나로 경로가 갈렸다 — `_keyword_classify_ex()` 가 **현재 문장만** 받기 때문이다.
⛔ **경로가 틀리면 컨텍스트를 아무리 잘 넘겨도 소용이 없다.** CS 에이전트는 매출을
   답할 수 없다.

⛔ 낱말 목록으로 통과시키지 마라. 이미 `_should_continue_bigquery_for_correction`
   이 "라고했지"·"그거말고" 목록으로 막고 있었지만 "센텔라 앰플은?" 은 못 잡았다.
   판정은 **문장 구조**(서술어·의문사 유무)로 한다.
"""
import pytest

from app.agents.orchestrator import (
    _inherit_route_for_followup,
    _is_followup_utterance,
    _previous_route,
)

_BQ_CTX = ("사용자: 2026년 7월 미국 매출 얼마야?\nAI: 미국 매출은 219.7억원입니다.\n"
           "[직전 실행 SQL — 후속 질문은 이 테이블·지표·필터를 기준으로 해석]\n"
           "SELECT SUM(Sales1_R) FROM `p.d.SALES_ALL_Backup` WHERE Country='미국'")
_NOTION_CTX = ("사용자: 인플루언서 시딩 가이드라인 알려줘\n"
               "AI: 시딩 가이드라인은 …\n*Notion 사내 문서 검색 · 전체 팀 자료*")


class TestFollowupDetection:
    """명사구로 끝나면 앞 조회의 축을 바꾸는 발화다."""

    @pytest.mark.parametrize("q", [
        "인도네시아는?", "센텔라 앰플은?", "그럼 6월은?", "우마는 빼고",
        "일본사업팀은?", "브랜드별로", "거기서 미국만",
    ])
    def test_followup(self, q):
        assert _is_followup_utterance(q) is True

    @pytest.mark.parametrize("q", [
        "센텔라 앰플 성분 알려줘",          # 서술어가 있으면 독립 질문
        "인플루언서 시딩 가이드라인 알려줘",
        "2026년 7월 미국 매출 얼마야?",
        "연차 규정 알려줘",
        "오늘 메일 요약",
        "안녕?",
        "",
    ])
    def test_standalone(self, q):
        assert _is_followup_utterance(q) is False

    def test_long_utterance_is_standalone(self):
        """길면 대개 완결된 문장이다 — 잘못 상속하면 주제 전환이 막힌다."""
        assert _is_followup_utterance("2026년 상반기 국가별 매출을 브랜드별로" * 2) is False


class TestPreviousRoute:
    def test_bigquery_marker(self):
        assert _previous_route(_BQ_CTX) == "bigquery"

    def test_notion_marker(self):
        assert _previous_route(_NOTION_CTX) == "notion"

    def test_no_context(self):
        assert _previous_route("") is None
        assert _previous_route("사용자: 안녕\nAI: 반갑습니다") is None


class TestInheritance:
    def test_inherits_bigquery(self):
        """제품명이 들어가도 매출 맥락이면 조회를 잇는다 — 이 사고의 본체다."""
        assert _inherit_route_for_followup("센텔라 앰플은?", _BQ_CTX) == "bigquery"
        assert _inherit_route_for_followup("인도네시아는?", _BQ_CTX) == "bigquery"

    def test_inherits_notion(self):
        """⚠️ 주제를 노션으로 옮긴 뒤의 후속은 **노션을** 이어야 한다."""
        assert _inherit_route_for_followup("영업1팀은?", _NOTION_CTX) == "notion"

    def test_topic_switch_not_inherited(self):
        """완전한 문장은 상속하지 않는다 — 주제 전환이 막히면 안 된다."""
        assert _inherit_route_for_followup("연차 규정 알려줘", _BQ_CTX) is None
        assert _inherit_route_for_followup("2026년 8월 매출 얼마야?", _NOTION_CTX) is None

    def test_no_previous_route(self):
        assert _inherit_route_for_followup("인도네시아는?", "") is None


class TestBothRoutingPathsPatched:
    """⛔ 한쪽만 고치면 스트리밍/비스트리밍에 따라 답이 갈린다 (반복된 실패)."""

    def test_two_call_sites(self):
        from pathlib import Path
        src = Path("app/agents/orchestrator.py").read_text(encoding="utf-8")
        assert src.count("elif _inherit_route_for_followup(") == 2


class TestMetricNounNotInherited:
    """⚠️ **무조건 상속하면 안 된다.**

    실측: 가이드라인(notion) 뒤 "유가 협업은?" 이 notion 을 물려받아 사내 문서를
    뒤졌고, `유가` 가 사람 이름 **유가연** 에 걸려 엉뚱한 답이 나왔다.
    지표 낱말은 문서가 아니라 데이터를 묻는 것이다.
    """

    @pytest.mark.parametrize("q", ["유가 협업은?", "매출은?", "광고비는?", "리뷰는?"])
    def test_metric_followup_routes_normally(self, q):
        assert _inherit_route_for_followup(q, _NOTION_CTX) is None

    @pytest.mark.parametrize("q", ["영업1팀은?", "일본은?", "센텔라 앰플은?"])
    def test_axis_value_still_inherits(self, q):
        """제품명·국가명·팀명은 축 값이라 그대로 상속한다."""
        assert _inherit_route_for_followup(q, _NOTION_CTX) == "notion"

    def test_metric_in_bigquery_context_still_inherits(self):
        """이미 조회 맥락이면 지표 낱말이 있어도 그대로 잇는다."""
        assert _inherit_route_for_followup("매출은?", _BQ_CTX) == "bigquery"


class TestStickyAnchorDoesNotWin:
    """⛔ `[직전 실행 SQL]` 은 컨텍스트 **맨 끝에 고정**으로 붙는다.

    끝부분만 보면 노션으로 옮긴 뒤에도 계속 bigquery 로 읽힌다 (실측으로 겪었다).
    마지막 AI 답변만 봐야 한다.
    """

    def test_notion_after_sql_anchor(self):
        nl = chr(10)
        ctx = (_BQ_CTX.split("[직전 실행 SQL")[0]
               + "사용자: 가이드라인?" + nl
               + "AI: 시딩 가이드는 …" + nl
               + "*Notion 사내 문서 검색 · 전체 팀 자료*" + nl
               + "[직전 실행 SQL — 후속]" + nl + "SELECT 1")
        assert _previous_route(ctx) == "notion"


class TestVisualizationOnlyRequestInheritsRoute:
    """⛔ 2026-08-21 사용자 제보 — "그래프로 그려줘" 가 가짜 ASCII 차트를 낳았다.

    `_PREDICATE_HINT` 에 `"줘"` 가 있어 이 발화가 **독립 질문**으로 분류됐다.
    → 경로를 못 물려받고 direct 로 떨어짐
    → direct 에는 차트 경로가 없다 (`chart-config` 는 `sql_agent` 한 곳)
    → LLM 이 ASCII 막대를 지어냈고, **눈금과 값이 맞지 않는 가짜 시계열**까지 나갔다.
      게다가 "정식 차트로 받으려면 이렇게 다시 보내주세요" 라며 사용자에게 재입력을 요구했다.

    없는 기능은 지어내는 게 아니라 **경로를 제대로 태워야** 한다.
    """

    @pytest.mark.parametrize("q", [
        "그래프로 그려줘", "시계열 그래프로 그려줘", "차트로 보여줘",
        "시계열 그래프로", "시각화해줘", "막대그래프로 다시 그려줘",
    ])
    def test_viz_only_inherits_previous_route(self, q):
        assert _inherit_route_for_followup(q, _BQ_CTX) == "bigquery"

    @pytest.mark.parametrize("q", [
        "일본 매출 그래프 그려줘",
        "남미 중미 2025년 2026년 매출 그래프 그려줘",
    ])
    def test_request_with_its_own_subject_routes_normally(self, q):
        """⚠️ 주제가 남아 있으면 독립 질문이다 — 새로 조회해야 한다.

        이걸 상속으로 삼키면 직전 주제의 차트가 나가고 질문에 답하지 않게 된다.
        """
        assert _inherit_route_for_followup(q, _BQ_CTX) is None

    def test_viz_only_without_previous_route_does_not_guess(self):
        """직전 경로가 없으면 물려받을 것도 없다 (첫 턴에 "그래프로 그려줘")."""
        assert _inherit_route_for_followup("그래프로 그려줘", "") is None
