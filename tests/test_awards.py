# -*- coding: utf-8 -*-
"""수상/랭킹 시트 파싱 — 조용히 틀릴 자리를 고정한다."""
from datetime import datetime

import pytest

from app.core.awards import (ALLOWED_TABS, SHEET_TAB, column_index,
                             find_header_row, parse_rank, parse_rows)

HEADER = ["", "구분", "브랜드", "주최사", "수상명 / 타이틀", "시작일", "종료일",
          "수상/랭킹 일자", "획득 국가", "제품/브랜드", "상세 내용", "랭킹/점수",
          "유/무료", "금액", "가능 여부", "시작일", " 종료일", "지역", "소스"]
SHEET = [
    [], ["", "스킨1004 수상 및 랭킹 등의 정보 취합"], [],
    ["", "※ 아래 경로에 캡처 이미지 업로드 해주세요"], ["", "", "", "", "", "기간"],
    HEADER,
    ["", "랭킹", "좀비뷰티", "화해", "2021 화해 뷰티 어워드", "2020-11-01", "2021-10-31",
     "2021-11-24", "대한민국", "누에고치 모공팩", "클렌징 비누 부문 1위", "1",
     "무료", "-", "O", "무기한", "무기한", "국내", "https://ex/1"],
]


def test_finds_the_header_row_instead_of_hardcoding_it():
    """안내문이 한 줄 늘어도 찾아야 한다 — 어긋나면 에러가 아니라 0건이다."""
    assert find_header_row(SHEET) == 5
    assert find_header_row([[]] + SHEET) == 6


def test_raises_when_there_is_no_header_row():
    with pytest.raises(ValueError):
        find_header_row([["아무", "관계없는", "행"]])


def test_duplicate_headers_are_split_by_position_not_name():
    """시작일/종료일이 두 쌍이다 — 수상 기간과 마케팅 활용 기간.
    이름으로 찾으면 두 기간이 조용히 섞인다."""
    ci = column_index(HEADER)
    assert ci["award_start"] == 5 and ci["award_end"] == 6
    assert ci["usage_start"] == 15 and ci["usage_end"] == 16
    assert ci["award_start"] != ci["usage_start"]


def test_trailing_space_in_a_header_does_not_break_lookup():
    """실제 시트의 두 번째 종료일은 ' 종료일' 로 앞에 공백이 있다."""
    assert column_index(HEADER)["usage_end"] == 16


@pytest.mark.parametrize("raw,expected", [
    ("1", 1), ("10", 10), (" 2 ", 2),
    ("97%", None), ("-", None), ("", None), ("TOP10", None),
])
def test_rank_is_parsed_only_when_it_is_a_clean_integer(raw, expected):
    """97% 와 - 를 정수로 강제하면 그 행이 조용히 사라지거나 거짓 순위가 된다."""
    assert parse_rank(raw) == expected


def test_rows_keep_the_raw_rank_even_when_it_cannot_be_parsed():
    rows = parse_rows(SHEET + [
        ["", "설문", "스킨1004", "PICKY", "만족도", "", "", "", "대한민국", "센텔라 앰플",
         "재구매 의사", "97%", "유료", "1,000,000", "△", "", "", "", ""]])
    assert rows[-1]["rank_raw"] == "97%"
    assert rows[-1]["rank_value"] is None


def test_blank_rows_are_dropped_but_rows_without_a_rank_are_kept():
    rows = parse_rows(SHEET + [["", "", "", "", "", "", ""], []])
    assert len(rows) == 1


def test_only_the_named_tab_is_allowed():
    assert ALLOWED_TABS == frozenset({SHEET_TAB})
    assert SHEET_TAB == "수상및랭킹"


def test_hidden_tab_names_are_not_present_in_the_source():
    """탭 이름을 코드에 두면 다음 사람이 '읽어도 되나 보다' 한다."""
    src = open("app/core/awards.py", encoding="utf-8").read()
    for hidden in ("매출,손익", "실적공유용", "대표제품 지역별 판매량", "미중일 매출비중"):
        assert hidden not in src


# tests/test_awards.py 에 이어서
from unittest.mock import MagicMock

from app.core import awards


def test_fetch_refuses_a_tab_that_is_not_allowed_and_says_why():
    with pytest.raises(ValueError) as e:
        awards._fetch(MagicMock(), "다른탭")
    msg = str(e.value)
    assert "다른탭" in msg and "허용" in msg


def test_sync_does_not_wipe_existing_data_when_the_sheet_reads_empty(monkeypatch):
    """0행은 권한 만료·탭 이름 변경으로 온다. 성공으로 적으면 영영 못 잡는다."""
    monkeypatch.setattr(awards, "ensure_awards_table", lambda: None)
    monkeypatch.setattr(awards, "_read_sheet", lambda: [])
    calls = []
    monkeypatch.setattr(awards, "execute", lambda *a, **k: calls.append(a))
    stat = awards.sync_awards()
    assert stat["rows"] == 0 and stat["empty"] is True
    assert calls == []


def test_sync_drops_microseconds_before_comparing(monkeypatch):
    """마이크로초가 남으면 방금 넣은 행이 전부 'synced_at < now' 에 걸려 지워진다."""
    monkeypatch.setattr(awards, "ensure_awards_table", lambda: None)
    monkeypatch.setattr(awards, "_read_sheet", lambda: SHEET)
    seen = {}
    monkeypatch.setattr(awards, "execute",
                        lambda sql, params=None: seen.setdefault("p", params))
    monkeypatch.setattr(awards, "fetch_one", lambda *a, **k: {"n": 0})
    awards.sync_awards()
    stamps = [v for v in seen["p"] if isinstance(v, datetime)]
    assert stamps and all(s.microsecond == 0 for s in stamps)


# 실측(2026-09-07, 라이브 시트): 자연키(브랜드·주최사·타이틀·제품·일자·순위)로는
# 35/206(17%)행이 충돌한다 — country 만 다른 행, detail 만 다른 행이 실제로 있다.
# `+country` 를 더해도 174/206 로 부족해 `_HEADER_ORDER` 전 컬럼 해시로 바꿨다.

def test_row_key_distinguishes_rows_that_differ_only_in_country():
    """실측: 쇼피 Top Item 처럼 같은 제품·같은 순위가 국가만 다른 행이 여러 개 있다.
    country 를 안 보면 말레이시아/글로벌 두 행이 하나로 뭉개져 한 행이 사라진다."""
    base = ["", "랭킹", "스킨천사", "쇼피", "Top Item", "", "", "2026-01-01",
            "말레이시아", "센텔라 앰플", "설명", "10", "무료", "-", "O", "", "", "", ""]
    row_my = list(base)
    row_global = list(base)
    row_global[8] = "글로벌"
    rows = parse_rows(SHEET + [row_my, row_global])
    rec_my, rec_global = rows[-2], rows[-1]
    assert rec_my["country"] != rec_global["country"]
    assert awards._row_key(rec_my) != awards._row_key(rec_global)


def test_row_key_distinguishes_rows_that_differ_only_in_detail():
    """실측: 화해 대한민국 1위가 부문(저자극/비건)만 다른 두 행으로 있다.
    detail 을 안 보면 두 부문이 한 행으로 뭉개진다."""
    base = ["", "랭킹", "스킨천사", "화해", "2023 화해 뷰티 어워드", "", "", "2023-01-01",
            "대한민국", "제품", "", "1", "무료", "-", "O", "", "", "", ""]
    row_a = list(base)
    row_a[10] = "2023 저자극 스킨케어"
    row_b = list(base)
    row_b[10] = "2023 비건 스킨케어"
    rows = parse_rows(SHEET + [row_a, row_b])
    rec_a, rec_b = rows[-2], rows[-1]
    assert rec_a["detail"] != rec_b["detail"]
    assert awards._row_key(rec_a) != awards._row_key(rec_b)


# tests/test_awards.py 에 이어서 — Task 3: 조회와 표시
# ⚠️ 브리프의 search() 본문은 두 가지 controller ruling 으로 대체됐다(task-3-brief.md 참고):
#    Ruling 1 — 데이터에 없는 낱말은 빼고 건다. Ruling 2 — `N위` 는 숫자 필터로 읽는다.

ROW = {"category": "랭킹", "brand": "좀비뷰티", "organizer": "화해",
       "title": "2021 화해 뷰티 어워드", "award_date": "2021-11-24",
       "award_start": "2020-11-01", "country": "대한민국",
       "product": "누에고치 모공팩", "detail": "클렌징 비누 부문 1위",
       "rank_raw": "1", "rank_value": 1, "paid": "무료", "amount_raw": "-",
       "usage_flag": "O", "usage_start": "무기한", "usage_end": "무기한",
       "usage_region": "국내", "source_url": ""}


def test_the_answer_never_asserts_that_an_award_may_be_used():
    """법적 판단이다. 못 쓰는 수상을 '쓸 수 있다' 고 답하면 실제 문제가 된다."""
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "2026-09-07"})
    for banned in ("사용 가능합니다", "사용하실 수 있습니다", "활용 가능합니다", "써도 됩니다"):
        assert banned not in text


def test_the_answer_shows_the_raw_usage_symbol_and_its_legend():
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "-"})
    assert "O" in text and awards.USAGE_LEGEND["O"] in text
    assert "담당자 확인" in text


def test_a_conditional_usage_note_is_carried_into_the_answer():
    row = dict(ROW, usage_flag="△", usage_region="국문 엠블럼만, 화해 검수 필요")
    text = awards.format_answer({"rows": [row], "total": 1, "synced_at": "-"})
    assert "화해 검수 필요" in text


def test_unparsed_rank_is_shown_as_raw_not_dropped():
    row = dict(ROW, rank_raw="97%", rank_value=None)
    text = awards.format_answer({"rows": [row], "total": 1, "synced_at": "-"})
    assert "97%" in text


def test_answer_says_how_many_were_omitted_when_the_list_is_cut():
    text = awards.format_answer({"rows": [ROW], "total": 25, "synced_at": "-"})
    assert "25" in text


def test_empty_result_says_so_instead_of_returning_an_empty_table():
    assert "찾지 못했" in awards.format_answer({"rows": [], "total": 0, "synced_at": "-"})


# Ruling 2 — `N위` 를 숫자 필터로 뽑는다 (텍스트 검색 대상에서는 뗀다)

def test_extract_rank_filter_pulls_the_digit_and_drops_the_token_from_the_text():
    rank, remaining = awards._extract_rank_filter("화해 1위 제품")
    assert rank == 1
    assert "1위" not in remaining
    assert "화해" in remaining and "제품" in remaining


def test_extract_rank_filter_is_none_when_there_is_no_rank_token():
    rank, remaining = awards._extract_rank_filter("화해 제품")
    assert rank is None
    assert remaining == "화해 제품"


def test_a_rank_number_in_the_question_becomes_a_numeric_filter_not_a_text_term(monkeypatch):
    """실측: '1위' 를 텍스트로 걸면 표기가 갈려(1위 선정/TOP10 진입) 화해 10행 중
    1행만 걸린다. rank_value 숫자 필터라야 표기와 무관하게 맞는다."""
    monkeypatch.setattr(awards, "_word_exists", lambda w: True)
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [ROW]

    monkeypatch.setattr(awards, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        awards, "fetch_one",
        lambda sql, *a, **k: {"n": 1} if "COUNT" in sql else {"s": "2026-09-07"})

    result = awards.search("화해 1위")

    assert result["rank_filter"] == 1
    assert "rank_value = %s" in captured["sql"]
    assert 1 in captured["params"]
    assert not any("1위" in str(p) for p in captured["params"])


# Ruling 1 — 데이터에 없는 낱말은 빼고 건다

def test_a_word_missing_from_every_row_does_not_zero_out_the_results(monkeypatch):
    """OP 재고와 같은 실패: 통째로 AND 로 걸면 한 낱말 때문에 0건이 된다."""
    monkeypatch.setattr(awards, "_word_exists", lambda w: w == "화해")
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [ROW]

    monkeypatch.setattr(awards, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        awards, "fetch_one",
        lambda sql, *a, **k: {"n": 1} if "COUNT" in sql else {"s": "2026-09-07"})

    result = awards.search("화해 어워드에서")

    assert result["rows"] == [ROW]
    assert result["dropped"] == ["어워드에서"]
    # 남은(=있는) 낱말만 필터에 실제로 걸린다
    assert captured["params"].count("%화해%") == len(awards._SEARCH_COLS)
    assert "%어워드에서%" not in captured["params"]


def test_when_no_word_is_usable_the_default_listing_is_returned_instead_of_empty(monkeypatch):
    """쓸 낱말이 하나도 안 남으면 빈 결과가 아니라 기본 목록(조건 없음)을 돌려준다."""
    monkeypatch.setattr(awards, "_word_exists", lambda w: False)
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [ROW]

    monkeypatch.setattr(awards, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        awards, "fetch_one",
        lambda sql, *a, **k: {"n": 1} if "COUNT" in sql else {"s": "2026-09-07"})

    result = awards.search("전혀없는말 아무말")

    assert result["dropped"] == ["전혀없는말", "아무말"]
    assert result["rows"] == [ROW]
    # 조건 없는 조회 — WHERE 에 거는 파라미터가 없다 (남는 것은 LIMIT 뿐)
    assert captured["params"] == (40,)


def test_dropped_words_are_disclosed_in_the_answer_body():
    """안 쓴 말로 찾은 결과를 그대로 주면 그 조건까지 맞는 줄 읽는다."""
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "-",
                                 "dropped": ["어워드에서"]})
    assert "어워드에서" in text
