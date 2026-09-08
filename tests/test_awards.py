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


# tests/test_awards.py 에 이어서 — fix round 1
# Finding 1 (Critical): 일반명사가 _word_exists 를 통과해 정답 10행을 1행으로 줄인다.
# 실측: "화해 뷰티 어워드에서 1위 한 우리 제품 알려줘" 에서 `제품` 은 206행 중
# 6행에만("신제품" 안에) 있어 데이터 확인은 통과하지만, 필터로 쓰면 화해 rank1
# 10행 중 9행이 사라진다. 데이터 확인 전에 일반명사를 먼저 뗀다.

def test_a_generic_noun_is_not_used_as_a_filter_even_though_the_data_contains_it(monkeypatch):
    """`제품` 은 '신제품' 안에 있어 _word_exists 를 통과하지만 필터로 쓰면 안 된다."""
    monkeypatch.setattr(awards, "_word_exists", lambda w: True)  # 데이터엔 다 있다고 가정
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [ROW]

    monkeypatch.setattr(awards, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        awards, "fetch_one",
        lambda sql, *a, **k: {"n": 1} if "COUNT" in sql else {"s": "2026-09-07"})

    result = awards.search("화해 제품")

    assert "제품" in result["dropped"]
    assert not any("%제품%" in str(p) for p in captured["params"])


def test_meaningful_category_words_are_still_used_as_filters(monkeypatch):
    """`수상`·`랭킹` 은 `구분` 을 고르는 뜻 있는 낱말이라 빼면 안 된다."""
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

    result = awards.search("수상 랭킹")

    assert result["dropped"] == []
    assert any("%수상%" in str(p) for p in captured["params"])
    assert any("%랭킹%" in str(p) for p in captured["params"])


def test_generic_nouns_are_never_checked_against_the_data(monkeypatch):
    """데이터 확인(_word_exists) 이전에 뗀다 — 통과 여부와 무관하게 무조건 뺀다."""
    checked = []
    monkeypatch.setattr(awards, "_word_exists", lambda w: checked.append(w) or True)
    monkeypatch.setattr(awards, "fetch_all", lambda sql, params: [ROW])
    monkeypatch.setattr(
        awards, "fetch_one",
        lambda sql, *a, **k: {"n": 1} if "COUNT" in sql else {"s": "-"})

    awards.search("브랜드 화해")

    assert "브랜드" not in checked
    assert "화해" in checked


# Finding 2 (Minor): rank_filter 가 걸린 사실도 dropped 처럼 답변에 공시한다.

def test_the_answer_discloses_a_rank_filter_when_one_was_applied():
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "-",
                                 "rank_filter": 1})
    assert "1위" in text and "좁혔습니다" in text


def test_the_answer_says_nothing_about_rank_when_no_rank_filter_was_applied():
    """매번 뜨는 안내는 곧 아무도 안 읽는다 — 필터가 없으면 문구도 없다."""
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "-",
                                 "rank_filter": None})
    assert "좁혔습니다" not in text


# ── Task 4: 라우팅 ─────────────────────────────────────────────────────
import re as _re

from app.core.awards import awards_intent


@pytest.mark.parametrize("q", [
    "화해 어워드에서 1위 한 제품 알려줘",
    "우리 수상 이력 알려줘",
    "글로우픽 랭킹 알려줘",
    "쇼피 Top Item 랭킹 뭐 있어?",
])
def test_award_questions_reach_this_route(q):
    assert awards_intent(q) is not None


@pytest.mark.parametrize("q", [
    "제품별 판매 순위 알려줘",
    "매출 랭킹 보여줘",
    "8월 국가별 매출 순위",
    "인도네시아 재고 얼마나 있어",
])
def test_sales_and_stock_questions_do_not_reach_this_route(q):
    """⛔ `랭킹` 두 글자를 단독으로 켜면 매출 질문을 가로챈다."""
    assert awards_intent(q) is None


# ── Fix round 1, Finding 1: `_STRONG` 이 `_BLOCK` 을 이겨야 한다 ──────────────
# 실측 사고: "판매 1위 수상 이력 알려줘" 가 `_BLOCK`(판매) 에 먼저 걸려 None 이
# 됐고, 그 질문은 BigQuery 로 새서 **엉뚱한 매출 숫자로 자신 있게** 답했다.
# `수상`·`어워드` 라는 말 자체가 주제를 결정적으로 밝히므로 매출 낱말이 같이
# 있어도 이 경로를 켜야 한다.

@pytest.mark.parametrize("q", [
    "판매 1위 수상 이력 알려줘",
    "매출 1위 어워드 받았어?",
])
def test_strong_award_words_win_over_block_words(q):
    assert awards_intent(q) is not None


@pytest.mark.parametrize("q", [
    "쇼피 매출 순위 알려줘",
    "올리브영 매출 랭킹",
    "제품별 판매 순위 알려줘",
])
def test_block_still_wins_when_strong_words_are_absent(q):
    """⚠️ 비대칭이 핵심이다 — `_WEAK`+`_AXIS` 만으로 `_BLOCK` 을 이기게 하면
    `쇼피 매출 순위`(축 낱말 `쇼피` 가 판매 채널이기도 하다) 가 조용히 샌다."""
    assert awards_intent(q) is None


# ── Fix round 1, Finding 2: `top` 라틴 세 글자 부분일치 금지 ──────────────────
# `_WEAK` 에 `"top"` 이 들어 있으면 `desktop`·`laptop`·`stop` 안에 그대로 걸린다
# (`eta` 가 `meta`·`beta` 안에 걸린 사고와 같은 패턴).

def test_top_does_not_partial_match_inside_a_latin_word():
    assert awards_intent("데스크톱 판매 1위 매장") is None
    assert awards_intent("노트북 laptop 판매량") is None


def test_shopee_top_item_ranking_still_reaches_this_route_without_the_word_top():
    """`top` 을 뺐어도 한글 낱말(`랭킹`)+축 낱말(`쇼피`)만으로 계속 잡혀야 한다."""
    assert awards_intent("쇼피 Top Item 랭킹 뭐 있어?") is not None


def test_explicit_source_selection_always_reaches_this_route():
    assert awards_intent("아무거나", explicit=True) is not None


def test_explicit_source_selection_with_no_words_returns_empty_string_not_none():
    """⛔ Correction A — 빈 문자열은 falsy 라 `if _awd_term:` 로 받으면
    `@@수상` 만 찍은 사용자가 경로를 못 탄다. 호출부는 `is not None` 으로 봐야 한다."""
    result = awards_intent("", explicit=True)
    assert result is not None
    assert result == ""


def test_the_registry_has_the_awards_entry_and_the_front_knows_its_group():
    """⛔ '그룹 라벨이 chat.js 어딘가에 있다' 만으로는 부족하다 — `SOURCE_GROUPS`
    의 `label` 에만 있고 `GROUP_BY_NAME` 에 없어도 통과해 버린다. 그런데 그게 바로
    이 테스트가 막으려던 조용한 실패다 (`fillSourceGroups()` 의 `if (!gid) return;`
    가 매핑이 없는 그룹의 소스를 화면에서 통째로 건너뛴다). 그래서 **`GROUP_BY_NAME`
    객체 리터럴 안에서만** 라벨을 찾는다."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert '"key": "수상"' in src and '"route": "awards"' in src
    group = _re.search(r'"key": "수상".*?"group": "([^"]+)"', src, _re.S).group(1)
    js = open("app/frontend/chat.js", encoding="utf-8").read()
    m = _re.search(r"var GROUP_BY_NAME = \{(.*?)\};", js, _re.S)
    assert m, "chat.js 에서 GROUP_BY_NAME 을 찾지 못했다"
    body = m.group(1)
    assert f'"{group}"' in body, (
        f"'{group}' 이 GROUP_BY_NAME 매핑 안에 없다 — SOURCE_GROUPS 의 label 에만 "
        "있으면 fillSourceGroups() 가 if (!gid) return; 로 건너뛰어 "
        "이 그룹의 소스가 화면에서 통째로 사라진다"
    )


def test_both_routing_paths_are_wired():
    """⚠️ 한쪽만 고치면 경로에 따라 답이 갈린다 — 이 저장소에서 이미 겪은 사고다."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert src.count("_awards_term(") >= 3   # 정의 1 + 호출 2(비스트리밍·스트리밍)


# ── Correction C: db_entry 정규화 (팀 리더 피드백, `_inventory_term` 과 같은 방식) ──

def test_awards_term_normalizes_a_list_db_entry_without_crashing():
    """⛔ `db_entry` 는 `@@` 를 여러 개 붙이면 **리스트**로 온다.
    `.get()` 을 그냥 부르면 리스트에서 터진다."""
    from app.agents.orchestrator import OrchestratorAgent as O

    o = O.__new__(O)
    db_entry = [{"route": "awards"}, {"route": "bigquery"}]
    term = o._awards_term("아무거나", "아무거나", db_entry, None)
    assert term is not None


def test_awards_term_handles_a_none_db_entry_without_crashing():
    """`@@` 를 하나도 안 붙였을 때 `db_entry` 는 `None` 으로 온다."""
    from app.agents.orchestrator import OrchestratorAgent as O

    o = O.__new__(O)
    term = o._awards_term("화해 어워드 1위", "화해 어워드 1위", None, None)
    assert term is not None


def test_enabled_sources_pinned_to_awards_alone_counts_as_explicit():
    """소스 선택기에서 `수상` 하나만 남기는 것은 `@@수상` 과 같은 뜻이다
    (재고가 `["OP"]` 에 대해 이미 그렇게 한다)."""
    from app.agents.orchestrator import OrchestratorAgent as O

    o = O.__new__(O)
    term = o._awards_term("아무 낱말도 없음", "아무 낱말도 없음", None, ["수상"])
    assert term is not None


# ── Task 5: 잡과 자가 점검 ────────────────────────────────────────────────────


def test_the_job_is_registered_and_watched():
    main = open("app/main.py", encoding="utf-8").read()
    assert "awards_sync_daily" in main and 'track_job("awards_sync_daily")' in main
    sc = open("app/core/self_check.py", encoding="utf-8").read()
    assert "awards_sync_daily" in sc


def test_both_self_checks_are_registered_in_the_checks_list():
    """함수만 만들고 CHECKS 에 안 넣으면 아무 일도 일어나지 않는다."""
    sc = open("app/core/self_check.py", encoding="utf-8").read()
    for cid in ("awards_unknown_usage", "awards_sheet_freshness"):
        assert f'Check("{cid}"' in sc, f"{cid} 가 CHECKS 에 등록되지 않았다"


def test_zero_rows_is_recorded_as_a_failure_not_a_success():
    """0행을 성공으로 적으면 자가 점검이 영영 못 잡는다."""
    main = open("app/main.py", encoding="utf-8").read()
    i = main.index('track_job("awards_sync_daily")')
    body = main[i:i + 800]
    assert "empty" in body and "raise" in body


def test_both_checks_are_actually_registered_in_the_checks_list_object():
    """문자열 존재만이 아니라 실제 `self_check.CHECKS` 리스트에 id 가 들어 있는지 본다."""
    from app.core.self_check import CHECKS

    ids = {c.id for c in CHECKS}
    assert "awards_unknown_usage" in ids
    assert "awards_sheet_freshness" in ids


def test_awards_checks_return_a_check_result_not_a_dict(monkeypatch):
    """`Check.fn` 은 `CheckResult` 를 돌려줘야 한다 — dict 를 주면 자가 점검
    실행기가 `.ok`/`.detail` 속성 접근에서 `AttributeError` 를 낸다."""
    from app.core import self_check as sc
    from app.core.self_check import CheckResult

    monkeypatch.setattr(sc, "fetch_all", lambda *a, **kw: [{"f": "O"}])
    monkeypatch.setattr(sc, "fetch_one",
                        lambda *a, **kw: {"s": datetime.now(), "n": 3})

    r1 = sc._check_awards_unknown_usage()
    r2 = sc._check_awards_sheet_freshness()
    assert isinstance(r1, CheckResult)
    assert isinstance(r2, CheckResult)
    assert r1.ok is True  # "O" 는 USAGE_LEGEND 에 등록돼 있다
    assert r2.ok is True


def test_awards_unknown_usage_catches_a_new_symbol(monkeypatch):
    """새 표기가 조용히 늘어나는 것을 사람이 보게 한다."""
    from app.core import self_check as sc

    monkeypatch.setattr(sc, "fetch_all",
                        lambda *a, **kw: [{"f": "O"}, {"f": "★신규표기★"}])

    result = sc._check_awards_unknown_usage()
    assert result.ok is False
    assert "★신규표기★" in result.detail


def test_awards_sheet_freshness_fails_on_zero_rows_and_says_why(monkeypatch):
    """0행이면 먼저 그것부터 말한다 — 권한·탭 이름이 원인일 가능성이 크다."""
    from app.core import self_check as sc

    monkeypatch.setattr(sc, "fetch_one", lambda *a, **kw: {"s": None, "n": 0})

    result = sc._check_awards_sheet_freshness()
    assert result.ok is False
    assert "권한" in result.detail and "탭" in result.detail


def test_awards_sheet_freshness_fails_when_stale(monkeypatch):
    from datetime import timedelta

    from app.core import self_check as sc

    old = datetime.now() - timedelta(hours=40)
    monkeypatch.setattr(sc, "fetch_one", lambda *a, **kw: {"s": old, "n": 120})

    result = sc._check_awards_sheet_freshness()
    assert result.ok is False


@pytest.mark.asyncio
async def test_the_job_raises_on_empty_and_is_recorded_as_failed(monkeypatch):
    """0행이면 `track_job` 블록 안에서 예외가 나야 실패로 기록된다.

    ⛔ `stat.get("empty")` 를 보고도 조용히 return 하면 `job_runs` 에는 '성공' 이
    남아 자가 점검이 영영 못 잡는다 — 그래서 여기서는 예외가 **밖으로 전파**되는지
    (즉 `track_job` 이 실패로 기록할 기회를 얻는지)까지 확인한다.
    """
    from contextlib import contextmanager

    # ⚠️ 이 워크트리는 커밋되지 않은 `app.core.visitor_access` 부재로 `app.main`
    #    import 자체가 기준선부터 깨져 있다(Task 5 와 무관) — 그 상태에서는
    #    실패가 아니라 건너뛴다. 고쳐지면 이 테스트가 자동으로 돈다.
    main = pytest.importorskip("app.main")
    from app.core import awards as awards_mod
    from app.core import self_check as sc

    events: list[str] = []

    class JobRun:
        def set_note(self, _note):
            events.append("note_set")

    @contextmanager
    def fake_track_job(job_id):
        events.append("start:" + job_id)
        try:
            yield JobRun()
        except Exception:
            events.append("failed")
            raise
        else:
            events.append("succeeded")

    monkeypatch.setattr(sc, "track_job", fake_track_job)
    monkeypatch.setattr(awards_mod, "sync_awards", lambda: {"rows": 0, "written": 0, "empty": True})

    # 잡 자체는 예외를 삼켜 로그로만 남긴다 (스케줄러가 죽지 않도록) — 그래도
    # `track_job` 블록 안에서는 실패로 기록됐어야 한다.
    await main._awards_sync_job()

    assert "start:awards_sync_daily" in events
    assert "failed" in events
    assert "succeeded" not in events
    assert "note_set" not in events  # 예외가 jr.set_note() 줄보다 먼저 났다


@pytest.mark.asyncio
async def test_the_job_succeeds_and_notes_the_stat_on_nonempty_rows(monkeypatch):
    from contextlib import contextmanager

    # ⚠️ 이 워크트리는 커밋되지 않은 `app.core.visitor_access` 부재로 `app.main`
    #    import 자체가 기준선부터 깨져 있다(Task 5 와 무관) — 그 상태에서는
    #    실패가 아니라 건너뛴다. 고쳐지면 이 테스트가 자동으로 돈다.
    main = pytest.importorskip("app.main")
    from app.core import awards as awards_mod
    from app.core import self_check as sc

    events: list[str] = []

    class JobRun:
        def set_note(self, note):
            events.append("note:" + note)

    @contextmanager
    def fake_track_job(job_id):
        events.append("start:" + job_id)
        yield JobRun()
        events.append("succeeded")

    monkeypatch.setattr(sc, "track_job", fake_track_job)
    monkeypatch.setattr(awards_mod, "sync_awards",
                        lambda: {"rows": 5, "written": 5, "empty": False})

    await main._awards_sync_job()

    assert "succeeded" in events
    assert any(e.startswith("note:") and "written" in e for e in events)


# ── Task 6: 골든셋 문항 3개 — expect 가 실제로 통과·실패를 가르는지 시험한다 ──
# ⛔ 통과만 시키는 문항은 아무것도 지키지 못한다. 여기서는 그럴듯한 정답과
#    그럴듯한 오답을 각각 만들어 `_evaluate()` 에 넣고 정답은 통과·오답은
#    실패하는지 직접 확인한다 (golden_set.json 자체를 읽어서 검증 — 사본을
#    새로 만들지 않는다. 사본은 반드시 낡는다).
from app.core.golden_runner import _evaluate, load_golden_set


def _golden_item(item_id):
    items = {it["id"]: it for it in load_golden_set()}
    return items[item_id]


# ── awards_reaches_awards ────────────────────────────────────────────────

def test_golden_awards_reaches_awards_question_routes_to_this_module():
    q = _golden_item("awards_reaches_awards")["question"]
    assert awards_intent(q) is not None


def test_golden_awards_reaches_awards_passes_on_a_plausible_correct_answer():
    item = _golden_item("awards_reaches_awards")
    good = awards.format_answer({
        "rows": [ROW], "total": 1, "synced_at": "2026-09-07",
        "dropped": [], "rank_filter": 1,
    })
    assert len(good) >= item["expect"]["min_len"]
    assert _evaluate(item, good, 5.0) == []


def test_golden_awards_reaches_awards_fails_when_the_organizer_is_missing():
    """라우팅이 새서 무관한 문서 검색 결과가 나오면 '화해' 가 답변에 없다."""
    item = _golden_item("awards_reaches_awards")
    bad = ("요청하신 수상 정보를 사내 문서에서 찾지 못했습니다. "
           "노션 문서에서 관련 페이지를 다시 검색해 보시거나, "
           "다른 키워드로 질문을 바꿔 다시 시도해 주시기 바랍니다.")
    reasons = _evaluate(item, bad, 5.0)
    assert f"필수 누락: {'화해'!r}" in reasons


def test_golden_awards_reaches_awards_fails_when_usage_is_asserted_as_ok():
    """활용 가부를 단정하면(법적 위험) 실패해야 한다 — '화해' 가 있어도 막는다."""
    item = _golden_item("awards_reaches_awards")
    bad = ("화해 뷰티 어워드에서 1위를 한 우리 제품은 누에고치 모공팩입니다. "
           "이 수상 내역은 마케팅에 사용 가능합니다. 별도 확인 없이 바로 쓰셔도 됩니다.")
    reasons = _evaluate(item, bad, 5.0)
    assert f"금지 문구 등장: {'사용 가능합니다'!r}" in reasons


# ── awards_sales_rank_stays_bigquery ─────────────────────────────────────

def test_golden_awards_sales_rank_stays_bigquery_question_does_not_route_here():
    q = _golden_item("awards_sales_rank_stays_bigquery")["question"]
    assert awards_intent(q) is None


def test_golden_awards_sales_rank_stays_bigquery_passes_on_a_plausible_correct_answer():
    item = _golden_item("awards_sales_rank_stays_bigquery")
    good = (
        "2026년 상반기(1~6월) 제품별 판매수량 순위는 다음과 같습니다.\n\n"
        "| 순위 | 제품 | 판매수량 |\n|---|---|---:|\n"
        "| 1 | 히알루-테카 퍼밍 크림 | 361,315 |\n"
        "| 2 | 마다가스카르 센텔라 앰플 | 210,442 |\n"
        "| 3 | 라이트 클렌징 오일 | 158,904 |\n\n"
        "판매수량은 세트 분해 기준 `Product.Total_Qty` 로 집계했습니다.\n\n"
        "<details>\n<summary>실행된 쿼리</summary>\n\n"
        "```sql\n"
        "SELECT SET AS product, SUM(Total_Qty) AS qty\n"
        "FROM `Product`\n"
        "WHERE Date BETWEEN '2026-01-01' AND '2026-06-30'\n"
        "GROUP BY SET ORDER BY qty DESC LIMIT 20\n"
        "```\n</details>"
    )
    assert len(good) >= item["expect"]["min_len"]
    assert _evaluate(item, good, 8.0) == []


def test_golden_awards_sales_rank_stays_bigquery_fails_when_awards_route_hijacks_it():
    """`순위`·`랭킹` 이 매출 질문을 가로채 awards 표가 나오면 실패해야 한다."""
    item = _golden_item("awards_sales_rank_stays_bigquery")
    bad = awards.format_answer({
        "rows": [ROW], "total": 1, "synced_at": "2026-09-07",
        "dropped": [], "rank_filter": None,
    })
    reasons = _evaluate(item, bad, 5.0)
    assert f"금지 문구 등장: {'주최사'!r}" in reasons
    assert any(r.startswith("SQL 규칙 위반") for r in reasons)


# ── awards_usage_not_asserted ────────────────────────────────────────────

def test_golden_awards_usage_not_asserted_question_routes_to_this_module():
    q = _golden_item("awards_usage_not_asserted")["question"]
    assert awards_intent(q) is not None


def test_golden_awards_usage_not_asserted_passes_on_a_plausible_correct_answer():
    """`format_answer` 가 실제로 내는 문구로 통과를 확인한다 — 지어낸 문장이 아니다."""
    item = _golden_item("awards_usage_not_asserted")
    good = awards.format_answer({
        "rows": [ROW], "total": 1, "synced_at": "2026-09-07",
        "dropped": [], "rank_filter": None,
    })
    assert "담당자 확인" in good  # format_answer 의 실제 문구와 기대어가 맞는지 대조
    assert len(good) >= item["expect"]["min_len"]
    assert _evaluate(item, good, 5.0) == []


def test_golden_awards_usage_not_asserted_fails_when_usage_is_asserted_as_ok():
    item = _golden_item("awards_usage_not_asserted")
    bad = ("수상 이력 중 마케팅에 쓸 수 있는 것은 2021 화해 뷰티 어워드 1위 "
           "누에고치 모공팩입니다. 이 수상 표기는 마케팅에 사용 가능합니다, "
           "별도 확인 없이 바로 활용하시면 됩니다.")
    reasons = _evaluate(item, bad, 5.0)
    assert f"금지 문구 등장: {'사용 가능합니다'!r}" in reasons


def test_golden_awards_usage_not_asserted_fails_when_the_disclaimer_is_missing():
    """0건이면 `format_answer` 가 '담당자 확인' 문구를 내지 않는다 — 그 상태를
    실패로 잡는지 확인한다 (note 에 적어 둔 0건 케이스에 대한 실제 회귀)."""
    item = _golden_item("awards_usage_not_asserted")
    bad = awards.format_answer({"rows": [], "total": 0, "synced_at": "2026-09-07"})
    reasons = _evaluate(item, bad, 5.0)
    assert f"다음 중 하나 필요: {item['expect']['contains_any']}" in reasons


# ── 2026-09-07 최종 리뷰 (final-fix-report) — Fix 1~4 + Minor ───────────────
# 아래 문항이 그 리포트의 실행 근거다.

# ── Fix 1 (Important): 활용 가부 방어선이 첫 턴에만 있었다 ───────────────────
# `_ROUTE_MARKERS`·`_INHERITED_ROUTES` 에 awards 가 없어 수상 표를 받은 다음
# 후속 발화("화해는?")가 direct 로 떨어졌다 — direct 는 full history 를 받으므로
# LLM 이 조건부 표를 보고 "사용 가능합니다" 를 만들 수 있었다.

_AWD_MARKER_CTX = (
    "사용자: 화해 어워드에서 1위 한 제품 알려줘\n"
    "AI: | 구분 | 브랜드 | 주최사 | 수상명 | 제품 | 상세 | 순위 | 국가 | 일자 | 활용 표기 |\n"
    "|---|---|---|---|---|---|---:|---|---|---|\n"
    "| 랭킹 | 좀비뷰티 | 화해 | 2021 화해 뷰티 어워드 | 누에고치 모공팩 | 클렌징 비누 부문 1위 "
    "| 1 | 대한민국 | 2021-11-24 | X |\n\n"
    "활용 표기: X 표기\n"
    "위 표기는 시트에 적힌 원문입니다. 실제 사용 가부는 담당자 확인이 필요합니다."
)


def test_awards_route_marker_is_registered_and_matches_the_real_answer_text():
    """표지 문자열이 `format_answer` 가 실제로 내는 문구와 일치해야 한다."""
    from app.agents.orchestrator import _ROUTE_MARKERS

    markers_by_route = dict(_ROUTE_MARKERS)
    assert "awards" in markers_by_route
    awd_markers = markers_by_route["awards"]
    assert awd_markers

    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "-"})
    assert any(m in text for m in awd_markers)


def test_awards_marker_does_not_swallow_or_get_swallowed_by_other_route_markers():
    """CS 답변 꼬리의 '출처:' 가 notion 표지에 걸려 새던 사고와 같은 함정을
    awards 도 피해야 한다 — 어느 방향으로도 다른 경로 표지와 부분 일치하면 안 된다."""
    from app.agents.orchestrator import _ROUTE_MARKERS

    markers_by_route = dict(_ROUTE_MARKERS)
    awd_markers = markers_by_route["awards"]
    for route, markers in markers_by_route.items():
        if route == "awards":
            continue
        for other in markers:
            for mine in awd_markers:
                assert other not in mine, (
                    f"{route} marker {other!r} is contained in awards marker {mine!r}")
                assert mine not in other, (
                    f"awards marker {mine!r} is contained in {route} marker {other!r}")


def test_previous_route_recognizes_an_awards_answer():
    from app.agents.orchestrator import _previous_route

    assert _previous_route(_AWD_MARKER_CTX) == "awards"


def test_a_followup_after_an_awards_answer_inherits_awards_not_direct():
    """이게 없으면 후속 발화가 direct 로 떨어져 LLM 이 스스로 활용 가부를 판단한다."""
    from app.agents.orchestrator import _inherit_route_for_followup

    assert _inherit_route_for_followup("화해는?", _AWD_MARKER_CTX) == "awards"


def test_awards_is_registered_as_an_inheritable_route_in_the_architecture_canvas():
    from app.flow.spec import _INHERITED_ROUTES

    assert "route.awards" in _INHERITED_ROUTES


def test_inherited_awards_route_is_actually_executed_not_silently_demoted_to_direct():
    """`awards` 는 `HANDLER_ROUTES` 에 없다 — 그냥 두면 `_resolve_handler` 가
    조용히 `_handle_direct` 로 떨어뜨려 `_ROUTE_MARKERS` 를 추가한 의미가
    사라진다 (`route.report` 가 겪은 것과 같은 함정). 두 디스패치 경로
    (route_and_execute, route_and_stream) 모두에 실행 분기가 있어야 한다."""
    from pathlib import Path

    src = Path("app/agents/orchestrator.py").read_text(encoding="utf-8")
    assert src.count('route == "awards"') == 2
    assert 'elif route == "awards":' in src
    assert 'if route == "awards":' in src


def test_awards_bypasses_the_streaming_default_source_filter():
    """`enabled_sources=None` 기본 허용 목록엔 awards 가 없다 — 이 예외가
    없으면 스트리밍에서 상속된 awards 후속이 매번 direct 로 되돌아간다."""
    from pathlib import Path

    src = Path("app/agents/orchestrator.py").read_text(encoding="utf-8")
    assert 'route in ("notion", "cs", "awards")' in src


# ── Fix 2 (Important): 조건부 행의 이유가 화면에서 사라진다 ──────────────────
# 실측: 조건부 65행 중 86%가 usage_region 빈칸/'-' 지만 detail 은 대부분 값이
# 있다 (예: "클렌징 비누 부문 1위 선정"). 표에도 조건에도 안 보이면 사용자는
# 조건부 이유를 알 방법이 없다.

def test_the_answer_table_has_a_detail_column():
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "-"})
    assert "상세" in text.splitlines()[0]
    assert ROW["detail"] in text


def test_a_conditional_row_with_no_usage_region_falls_back_to_detail():
    """조건부이고 usage_region(및 시작/종료일)이 비어 있으면 조건 사유를 detail
    에서 채운다 — 판단: 승인/불가/논의중은 확정 판정이라 이 보완을 적용하지
    않는다 (조건이 아닌데 조건처럼 보이면 안 된다)."""
    row = dict(ROW, usage_flag="△", usage_region="", usage_start="",
               usage_end="", detail="클렌징 비누 부문 1위 선정")
    text = awards.format_answer({"rows": [row], "total": 1, "synced_at": "-"})
    assert "클렌징 비누 부문 1위 선정" in text
    assert "조건:" in text


def test_a_non_conditional_row_with_no_usage_region_does_not_borrow_detail_as_a_condition():
    """승인 표기는 확정된 판정이다 — usage_region 이 비었다고 detail 을 조건
    칸에 끌어오면 없던 '조건' 이 생긴 것처럼 보인다."""
    row = dict(ROW, usage_flag="O", usage_region="", usage_start="", usage_end="",
               detail="클렌징 비누 부문 1위 선정")
    text = awards.format_answer({"rows": [row], "total": 1, "synced_at": "-"})
    assert "조건:" not in text


def test_a_long_detail_is_truncated_visibly():
    row = dict(ROW, detail="가" * 80)
    text = awards.format_answer({"rows": [row], "total": 1, "synced_at": "-"})
    assert "…" in text
    assert ("가" * 80) not in text


# ── Fix 3 (Important): 배포 첫날 "아직 안 들어옴" 과 "그런 수상 없음" 이 같은 문장 ──

def test_an_empty_table_says_not_yet_loaded_not_no_such_award():
    text = awards.format_answer({"rows": [], "total": 0, "synced_at": "-", "table_empty": True})
    assert "적재되지 않았" in text
    assert "찾지 못했습니다" not in text


def test_a_zero_match_on_a_loaded_table_still_says_not_found():
    """테이블에 데이터가 있는데 조건에 맞는 게 없으면 여전히 '못 찾음' 이다 —
    두 문구가 달라야 배포 첫날의 '아직 안 들어옴' 과 구분된다."""
    text = awards.format_answer({"rows": [], "total": 0, "synced_at": "2026-09-07",
                                 "table_empty": False})
    assert "찾지 못했습니다" in text
    assert "적재되지 않았" not in text


def test_search_marks_the_table_as_empty_only_when_synced_at_is_null(monkeypatch):
    """추가 COUNT 조회를 늘리지 않는다 — 기존 MAX(synced_at) 조회 하나로
    판정한다 (리포트에 적은 판단 근거)."""
    monkeypatch.setattr(awards, "_word_exists", lambda w: True)
    monkeypatch.setattr(awards, "fetch_all", lambda sql, params: [])
    monkeypatch.setattr(
        awards, "fetch_one",
        lambda sql, *a, **k: {"n": 0} if "COUNT" in sql else {"s": None})

    result = awards.search("화해")
    assert result["table_empty"] is True

    monkeypatch.setattr(
        awards, "fetch_one",
        lambda sql, *a, **k: {"n": 0} if "COUNT" in sql else {"s": "2026-09-07"})
    result = awards.search("화해")
    assert result["table_empty"] is False


# ── Fix 4 (Important): 상한 때문에 쓰지 않은 낱말이 조용히 있다 ──────────────

def test_a_word_beyond_the_eight_word_cap_is_disclosed_separately_from_dropped(monkeypatch):
    """9번째 낱말은 _word_exists 조차 안 돈다 — '자료에 없다' 는 이유는 거짓이라
    dropped 가 아니라 capped 로 따로 담는다."""
    checked = []
    monkeypatch.setattr(awards, "_word_exists", lambda w: checked.append(w) or True)
    monkeypatch.setattr(awards, "fetch_all", lambda sql, params: [ROW])
    monkeypatch.setattr(
        awards, "fetch_one",
        lambda sql, *a, **k: {"n": 1} if "COUNT" in sql else {"s": "2026-09-07"})

    nine_words = " ".join(f"낱말{i}" for i in range(9))
    result = awards.search(nine_words)

    assert "낱말8" not in checked          # 9번째는 데이터 확인조차 안 됐다
    assert "낱말8" in result["capped"]
    assert "낱말8" not in result["dropped"]


def test_a_kept_word_beyond_the_five_filter_cap_is_disclosed_as_capped(monkeypatch):
    """데이터에 있는(=_word_exists 통과) 6번째 낱말은 필터에 못 걸렸을 뿐 —
    dropped('자료에 없음') 가 아니라 capped('상한') 로 담는다."""
    monkeypatch.setattr(awards, "_word_exists", lambda w: True)
    captured = {}

    def fake_fetch_all(sql, params):
        captured["params"] = params
        return [ROW]

    monkeypatch.setattr(awards, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        awards, "fetch_one",
        lambda sql, *a, **k: {"n": 1} if "COUNT" in sql else {"s": "2026-09-07"})

    six_words = " ".join(f"낱말{i}" for i in range(6))
    result = awards.search(six_words)

    assert result["dropped"] == []
    assert "낱말5" in result["capped"]
    assert not any("%낱말5%" in str(p) for p in captured["params"])


def test_capped_words_are_disclosed_in_the_answer_with_a_distinct_reason():
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "-",
                                 "capped": ["넘친낱말"]})
    assert "넘친낱말" in text
    assert "반영하지 못했습니다" in text


# ── Minor: `|` 나 개행이 든 셀이 표를 깨뜨리지 않는다 ────────────────────────

def test_a_pipe_character_in_a_cell_does_not_break_the_markdown_table():
    row = dict(ROW, title="A|B")
    text = awards.format_answer({"rows": [row], "total": 1, "synced_at": "-"})
    header, sep, data_row = text.splitlines()[0:3]
    assert header.count("|") == sep.count("|") == data_row.count("|")
    assert "A/B" in text


def test_a_newline_in_a_cell_does_not_split_the_markdown_row():
    row = dict(ROW, title="A\nB")
    text = awards.format_answer({"rows": [row], "total": 1, "synced_at": "-"})
    data_row = text.splitlines()[2]
    assert data_row.startswith("|") and data_row.endswith("|")
    assert "A B" in text


# ── Minor: "자료에 없는" 문구가 _GENERIC_NOUNS 에는 거짓이었다 ───────────────

def test_dropped_disclosure_does_not_claim_the_word_is_missing_from_the_data():
    """_GENERIC_NOUNS(제품, 브랜드 등)는 실제로 데이터에 있다 — '자료에 없는' 은
    거짓 문구다. 이유를 밝히지 않는 참인 문구로 바꿨다."""
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "-",
                                 "dropped": ["제품"]})
    assert "자료에 없는" not in text
    assert "필터로 쓰지 않고" in text


# ── Minor: 적재 기준일이 낡았을 땐 표보다 먼저 말한다 ────────────────────────

def test_a_fresh_answer_keeps_the_freshness_note_at_the_end():
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "2026-09-07",
                                 "synced_at_raw": datetime.now()})
    assert text.rstrip().endswith("적재분*")
    assert not text.startswith("⚠")


def test_a_stale_answer_moves_the_freshness_warning_to_the_front():
    from datetime import timedelta

    old = datetime.now() - timedelta(hours=40)
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "2026-09-05",
                                 "synced_at_raw": old})
    assert text.startswith("⚠")
    table_pos = text.index("| 구분")
    warn_pos = text.index("적재가")
    assert warn_pos < table_pos


def test_a_missing_synced_at_raw_is_not_treated_as_stale():
    """synced_at_raw 를 안 주는 기존 호출부(대부분의 테스트)는 하위 호환이다 —
    stale 판정이 조용히 켜지면 안 된다."""
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "2026-09-07"})
    assert not text.startswith("⚠")


# ── 활용 가부 후속 질문 — 첫 턴에만 있던 방어선을 다음 턴까지 잇는다 (2026-09-08) ──
#
# 최종 리뷰가 잡은 구멍이다. 수상 표를 받은 뒤 "이거 광고에 써도 돼?" 를 물으면
# `_is_followup_utterance` 가 서술어를 보고 "독립 질문" 으로 판정해 상속을 안 태우고,
# 그 질문이 `direct` 로 떨어져 **대화 맥락에 남은 △ 표를 보고 LLM 이
# "조건부라 사용 가능합니다" 를 지어낼 수 있었다.** 활용 가부는 법적 판단이다.
#
# ⚠️ 좁게 연다 — **직전이 수상 답변일 때만**. 그래서 초상권 경로를 훔칠 수 없다.

_AWARDS_CTX_ROW = {
    "category": "랭킹", "brand": "스킨1004", "organizer": "화해",
    "title": "2021 화해 뷰티 어워드", "award_date": "2021-11-24", "award_start": "",
    "country": "대한민국", "product": "센텔라 앰플", "detail": "앰플 부문 1위 선정",
    "rank_raw": "1", "rank_value": 1, "paid": "무료", "amount_raw": "-",
    "usage_flag": "△", "usage_start": "", "usage_end": "", "usage_region": "",
    "source_url": "",
}


def _awards_context():
    from app.core.awards import format_answer

    answer = format_answer({"rows": [_AWARDS_CTX_ROW], "total": 1,
                            "synced_at": "2026-09-08", "dropped": [], "rank_filter": 1})
    return "user: 화해 어워드 1위\nassistant: " + answer


@pytest.mark.parametrize("q", [
    "이거 광고에 써도 돼?",
    "이거 마케팅에 사용 가능해?",
    "이 수상 활용해도 되나요?",
    "저작권 문제 없어?",
])
def test_usage_questions_after_an_awards_answer_stay_on_the_awards_route(q):
    """서술어가 붙어도 상속시킨다 — 이 경로에만 코드 보증이 있다."""
    from app.agents.orchestrator import _inherit_route_for_followup

    assert _inherit_route_for_followup(q, _awards_context()) == "awards"


@pytest.mark.parametrize("q", ["일본 매출 얼마야?", "오늘 날씨 어때?", "보고서 만들어줘"])
def test_ordinary_questions_after_an_awards_answer_route_normally(q):
    """⛔ 넓히면 안 된다 — 활용 가부를 묻지 않는 말은 예전 그대로 정상 라우팅한다."""
    from app.agents.orchestrator import _inherit_route_for_followup

    assert _inherit_route_for_followup(q, _awards_context()) is None


@pytest.mark.parametrize("prev_answer", [
    "assistant: [메일] 3건 요약",
    "assistant: Notion 사내 문서 검색 결과",
])
def test_usage_questions_do_not_hijack_other_routes(prev_answer):
    """⛔ 사진 사용 가부는 초상권(model_rights)의 몫이다 — 직전이 수상일 때만 연다."""
    from app.agents.orchestrator import _inherit_route_for_followup

    ctx = "user: q\n" + prev_answer
    assert _inherit_route_for_followup("이 사진 써도 돼?", ctx) is None


def test_the_usage_answer_is_written_by_code_and_never_asserts_permission():
    """⛔ 조회도 LLM 도 끼지 않는다 — 단정이 구조적으로 불가능해야 한다."""
    from app.core.awards import usage_permission_answer

    text = usage_permission_answer()
    for banned in ("사용 가능합니다", "활용 가능합니다", "써도 됩니다", "문제 없습니다"):
        assert banned not in text
    assert "판단하지 않습니다" in text
    assert "담당자 확인" in text


def test_a_usage_question_does_not_run_a_search():
    """⛔ 조회하면 안 쓴 낱말로 기본 목록 40행이 나간다 — 답이 아니라 잡음이다."""
    from app.core.awards import asks_usage_permission

    assert asks_usage_permission("이거 광고에 써도 돼?")
    assert asks_usage_permission("이거 마케팅에 사용가능해?")   # 공백 없이 써도 잡는다
    assert not asks_usage_permission("화해 어워드 1위 한 제품")


def test_the_handler_short_circuits_usage_questions():
    """핸들러가 실제로 조회를 건너뛰는지 — 소스에 배선이 있는지 본다."""
    import inspect

    from app.agents.orchestrator import OrchestratorAgent

    src = inspect.getsource(OrchestratorAgent._handle_awards_query)
    assert "asks_usage_permission" in src and "usage_permission_answer" in src
