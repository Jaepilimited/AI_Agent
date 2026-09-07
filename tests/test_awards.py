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
