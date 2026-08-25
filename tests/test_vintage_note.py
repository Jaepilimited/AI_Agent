# -*- coding: utf-8 -*-
"""근거 문서 연식 공시 — 붐따 #105 (전휘빈, 2026-07-08).

    질문: "야근 식대 지원한도 얼마야?"
    답변: "1인당 10,000원" — 근거는 **2023-03-31** 자 FAQ 하나뿐
    제보: "옛날 데이터를 바라보고 있음 (노션 2021년 데이터)"

`_vintage_note()` 는 2026-08-18 에 만들어졌는데 **이 질문에서 안 붙는다**
(2026-08-25 프로덕션 실측). 판정이 "상위 3건 **모두** 오래됐을 때"라,
상위에 최신 청크가 하나만 섞여도 경고가 사라진다. 실제로 답변은 2023년 문서
하나만 인용했는데도 경고가 없었다.

⛔ 판정 기준은 **답변이 실제로 인용한 문서**다. 검색 상위에 무엇이 걸렸는지가
   아니라, 답을 만든 근거가 낡았는지가 사용자에게 중요하다.
"""
from app.agents.qdrant_agent import _vintage_note

URL_OLD = "https://app.notion.com/p/2b928ccf8b9a4e7ba51db7fb2a2e2d57"
URL_NEW = "https://app.notion.com/p/99998ccf8b9a4e7ba51db7fb2a2e2d57"


def _r(date, url, title="문서"):
    return {"score": 0.8, "payload": {"last_edited_time": date, "page_url": url,
                                      "page_title": title}}


def test_warns_when_the_cited_document_is_old():
    """#105 그 자체 — 답변이 2023년 문서만 인용했으면 경고해야 한다."""
    results = [_r("2023-03-31", URL_OLD), _r("2026-08-01", URL_NEW),
               _r("2026-08-10", URL_NEW)]
    answer = f"야근 식대는 10,000원입니다.\n출처: [FAQ]({URL_OLD})"
    note = _vintage_note(results, answer)
    assert note and "2023-03-31" in note, note


def test_no_warning_when_a_recent_document_is_also_cited():
    """최신 문서도 함께 근거로 썼으면 굳이 겁주지 않는다."""
    results = [_r("2023-03-31", URL_OLD), _r("2026-08-01", URL_NEW)]
    answer = f"...\n출처: [FAQ]({URL_OLD}) · [복리후생]({URL_NEW})"
    assert _vintage_note(results, answer) == ""


def test_recent_only_says_nothing():
    results = [_r("2026-08-01", URL_NEW)]
    assert _vintage_note(results, f"...[문서]({URL_NEW})") == ""


def test_falls_back_to_top_hit_when_nothing_is_cited():
    """출처 링크가 없는 답변도 있다 — 그때는 1순위 문서로 판정한다."""
    results = [_r("2023-03-31", URL_OLD), _r("2026-08-01", URL_NEW)]
    assert _vintage_note(results, "출처 없이 쓴 답변") != ""


def test_empty_results_say_nothing():
    assert _vintage_note([], "아무거나") == ""


# ── 수정 시점을 알 수 없는 문서 (2026-08-25 규명) ────────────────────────────
# `last_edited_time == ""` 는 버그가 아니라 **표식**이다 (`ingest_page.py`):
#   "공개 notion.site 는 last_edited_time 을 알 수 없으므로 존재 여부만 확인"
# 노션 인테그레이션에 **공유되지 않은** 페이지라 API 대신 공개 링크를 긁어 넣은 것.
# 클라우드 색인 실측 (2026-08-25): 1,854 청크 중 388개(20.9%)가 수정일 없음.
# 그 22개 페이지에 **복리후생·근태/휴가·보상·채용·퇴사** 같은 인사 규정이 몰려 있다.
#
# ⛔ 그래서 "값이 다르면 최신 문서를 따르라"(2026-08-18)가 **그 문서들에서는 작동할 수
#    없다.** 붐따 #105 가 정확히 그 경우다: 15,000원이 적힌 `복리후생` 은 수정일이 없고,
#    10,000원이 적힌 FAQ 는 2023-03-31 이라 LLM 이 날짜 있는 쪽을 골랐다.
# 값을 코드가 고를 수는 없다. 대신 **모른다는 사실을 보이게** 한다.

def test_undated_source_is_disclosed():
    results = [_r("", URL_OLD, "복리후생")]
    note = _vintage_note(results, f"...[복리후생]({URL_OLD})")
    assert note, "수정 시점을 모르는데 아무 말이 없다"
    assert "알 수 없" in note or "미상" in note, note


def test_dated_recent_source_wins_over_undated():
    """최신 문서를 함께 근거로 썼으면 겁주지 않는다."""
    results = [_r("", URL_OLD, "복리후생"), _r("2026-08-01", URL_NEW, "최신")]
    answer = f"...[복리후생]({URL_OLD}) · [최신]({URL_NEW})"
    assert _vintage_note(results, answer) == ""
