# -*- coding: utf-8 -*-
"""붙여넣은 데이터를 무시하고 DB 를 조회하던 것을 지킨다.

사고 (2026-08-18 실측): 권역별 마케팅 비용 표 60행을 붙여넣고 "이 표를 정리해줘"
라고 하니, 표는 쳐다보지도 않고 BigQuery 에서 **전혀 다른 권역 ROAS** 를 가져와
답했다. 숫자는 그럴듯한데 묻지 않은 데이터고, 붙여넣은 사람은 자기 표가 반영된
줄 안다 — 이 저장소가 반복해서 겪는 "조용히 틀린" 실패다.

⛔ 판정은 **구조**로 한다. "표"·"붙여넣" 같은 낱말을 세는 방식으로 통과시키면
   목록 밖 표현에서 그대로 재발한다.
"""
import pytest

from app.agents.orchestrator import OrchestratorAgent

_PAD = "이 데이터를 기준으로 어느 쪽 효율이 좋고 나쁜지 정리해줘. 근거도 적어줘. " * 4


def _md_table(rows: int) -> str:
    head = "| 권역 | 캠페인 | 비용 | CPM |\n|---|---|---|---|\n"
    body = "\n".join(f"| 권역{i%9} | 캠페인{i} | {12000+i*137} | {3.2+i*0.01:.2f} |"
                     for i in range(rows))
    return _PAD + "\n\n" + head + body


def _tsv(rows: int) -> str:
    return _PAD + "\n" + "\n".join(f"A{i}\tB{i}\tC{i}" for i in range(rows))


@pytest.fixture(scope="module")
def orc():
    return OrchestratorAgent.__new__(OrchestratorAgent)


class TestPastedDataGoesDirect:
    """붙여넣은 데이터는 **그 데이터로** 답해야 한다 — 조회하면 다른 숫자가 나온다."""

    @pytest.mark.parametrize("rows", [6, 20, 60])
    def test_markdown_table(self, orc, rows):
        assert orc._has_pasted_data(_md_table(rows)) is True
        assert orc._keyword_classify_ex(_md_table(rows)) == ("direct", True)

    def test_tsv_paste(self, orc):
        assert orc._has_pasted_data(_tsv(9)) is True

    def test_beats_data_keywords(self, orc):
        """⚠️ 표 안에 '매출'·'비용' 이 있어도 조회로 새면 안 된다."""
        q = _md_table(30).replace("비용", "매출")
        assert orc._keyword_classify_ex(q) == ("direct", True)


class TestOrdinaryQuestionsUnaffected:
    """⛔ 넓히면 평범한 조회가 direct 로 새서 SQL 을 안 돈다. 양방향을 함께 지킨다."""

    @pytest.mark.parametrize("q", [
        "2026년 7월 미국 매출 알려줘",
        "국가별 매출 top 5",
        "인도네시아 쇼피 매출 얼마야",
    ])
    def test_still_bigquery(self, orc, q):
        assert orc._keyword_classify_ex(q)[0] == "bigquery"

    def test_short_example_table_not_treated_as_data(self, orc):
        """3행 이하 예시 표는 '이런 형식으로 줘' 라는 뜻일 수 있다 — 막지 않는다."""
        q = "이런 형식으로 줘\n| a | b |\n|---|---|\n| 1 | 2 |\n" + "설명" * 120
        assert orc._has_pasted_data(q) is False

    def test_short_query_ignored(self, orc):
        assert orc._has_pasted_data("| a | b |") is False
