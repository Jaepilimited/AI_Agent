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
