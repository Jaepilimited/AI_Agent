# -*- coding: utf-8 -*-
"""「엑셀로 뽑아줘」에 파일을 준다 · 못 준다는 거짓말을 지운다 (붐따 #161).

두 가지가 함께 났다:
- 8행짜리 결과는 링크 조건(빠진 행 있음 / 20행 이상) 둘 다에 안 걸려 **아무것도
  안 붙었다** — 사용자가 명시적으로 "엑셀로 뽑아줘" 라고 했는데도.
- 다른 턴에서는 "엑셀(.xlsx) 파일 생성 | 파일 첨부·다운로드 형태의 출력은
  지원하지 않습니다" 라고 **있는 기능을 없다고** 답했다.
"""
from app.core import file_request as FR


# ── 파일을 달라고 한 것인가 ─────────────────────────────────────────────

def test_the_actual_request_that_got_nothing():
    assert FR.wants_file("세일즈 운영팀 정다운이 진행항 9월 etd 수출건 엑셀로 뽑아줘!")


def test_common_ways_to_ask_for_a_file():
    for q in ("csv로 줘", "CSV 파일 주세요", "xlsx로 받고싶어", "엑셀로 정리해줘",
              "다운로드 해줘", "내려받고 싶어", "파일로 줘", "스프레드시트로 만들어줘"):
        assert FR.wants_file(q), q


def test_a_korean_particle_does_not_break_the_match():
    """⛔ `\\bcsv\\b` 로 두면 `csv로` 에서 매치가 사라진다 — 한글이 단어 문자라
    경계가 생기지 않는다 (검색 키워드에서 조사를 먼저 떼야 했던 그 함정)."""
    assert FR.wants_file("csv로 줘")
    assert FR.wants_file("엑셀로 뽑아줘")


def test_an_ordinary_query_is_not_a_file_request():
    """⛔ `뽑아줘`·`정리해줘` 까지 파일 요청으로 보면 모든 답변에 링크가 붙는다."""
    for q in ("9월 수출건 알려줘", "매출 정리해줘", "제품 목록 뽑아줘",
              "팀별로 집계해줘", "일본 매출 추이 보여줘"):
        assert not FR.wants_file(q), q


# ── 있는 기능을 없다고 쓴 문장을 지운다 ─────────────────────────────────

def test_the_exact_denial_that_went_out_is_removed():
    answer = ("| 항목 | 상태 |\n"
              "| 엑셀(.xlsx) 파일 생성 | 파일 첨부·다운로드 형태의 출력은 "
              "지원하지 않습니다 (조회 결과 표/차트까지만 가능) |\n"
              "| 다른 줄 | 남아야 한다 |\n")
    out = FR.strip_denials(answer)
    assert "지원하지 않습니다" not in out
    assert "남아야 한다" in out


def test_other_denial_phrasings_are_removed():
    for line in ("엑셀 파일로는 제공하지 않습니다.",
                 "CSV 다운로드는 불가능합니다.",
                 "파일로 드릴 수 없습니다."):
        assert FR.strip_denials(line).strip() == ""


def test_unrelated_sentences_survive():
    """⛔ 너무 넓게 지우면 멀쩡한 문장이 사라지고, 그건 조용한 손실이다."""
    for line in ("엑셀에서 바로 열리는 CSV 입니다.",
                 "이 데이터는 파일로 관리됩니다.",
                 "일본 매출은 지원하지 않습니다."):
        assert FR.strip_denials(line) == line


# ── 배선 ────────────────────────────────────────────────────────────────

def _agent_src():
    with open("app/agents/sql_agent.py", encoding="utf-8") as fh:
        return fh.read()


def test_the_download_helper_receives_the_question():
    """질문을 안 넘기면 '달라고 했는지' 를 알 수 없다 — 두 경로 모두."""
    src = _agent_src()
    assert src.count("_attach_full_data_download(") == 3   # 정의 1 + 호출 2
    assert "wants_file" in src and "strip_denials" in src


def test_a_requested_file_ignores_the_row_threshold():
    """⛔ 8행이라 안 붙던 그 조건 — 요청이 있으면 통과해야 한다."""
    src = _agent_src()
    assert "if not asked and not rows_withheld" in src


def test_the_answer_does_not_call_the_csv_an_xlsx():
    """⚠️ 우리가 주는 것은 CSV 다 — 확장자가 다르면 사용자는 그걸 알아야 한다."""
    assert ".xlsx 파일 자체를 " in _agent_src()
