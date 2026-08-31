# -*- coding: utf-8 -*-
"""0행 원인을 **추측하지 않는지** 지킨다.

사고 원문 (2026-08-14 사용자 제보): "에콰도르 Valkirias FOC 볼 수 있나" 에
"에콰도르는 유효 국가 목록에 존재하지 않습니다" 라고 답했다. 실제로는
2,448건·33.8억이 있었다. 원인은 0행 힌트가 국가 191개 중 12개만 나열하고
"등" 을 붙인 것 — LLM 이 그걸 **전체 목록으로 읽었다**.
"""
from pathlib import Path

import pytest

from app.agents.sql_agent import _all_countries, _country_hint
from app.core.zero_row import _parse, split_and

# 국가 목록은 이제 **값 목록 캐시**에서 온다 (프롬프트 파싱이 아니다 — 2026-08-18).
# 로컬·CI 에는 캐시 DB 가 없으므로 주입해서 판정 로직만 검사한다.
_COUNTRIES = ["가나", "과테말라", "멕시코", "미국", "베트남", "에콰도르", "일본",
              "칠레", "페루", "한국"]


@pytest.fixture(autouse=True)
def _seed_country_cache(monkeypatch):
    import app.agents.sql_agent as sa
    monkeypatch.setattr(sa, "_COUNTRY_VALUES", list(_COUNTRIES))
    yield


class TestCountryHint:
    def test_reads_from_cache_not_prompt(self):
        """⛔ 프롬프트를 파싱하지 않는다. 값 목록 캐시가 단일 소스다.

        예전엔 `prompts/sql_generator.txt` 의 DISTINCT 줄을 정규식으로 읽었는데,
        그 줄이 `{{VALUES:Country}}` 자리표시자로 바뀌면서 파싱이 빈 목록을
        돌려줬고 "에콰도르는 실재한다" 판정이 통째로 죽었다 (2026-08-18).
        """
        assert "에콰도르" in _all_countries()
        prompt = Path("prompts/sql_generator.txt").read_text(encoding="utf-8")
        assert "{{VALUES:Country}}" in prompt

    @pytest.mark.parametrize("country", ["에콰도르", "칠레", "멕시코", "페루", "과테말라"])
    def test_real_country_never_called_invalid(self, country):
        """⛔ 실재하는 국가를 '없다' 고 말하게 두면 안 된다 — 이 사고의 본체다."""
        hint = _country_hint(f"SELECT 1 WHERE Country = '{country}'")
        assert "실재하는 국가다" in hint
        assert "목록에 없다" not in hint

    def test_typo_is_flagged_with_suggestion(self):
        hint = _country_hint("SELECT 1 WHERE Country = '에콰돌'")
        assert "목록에 없다" in hint and "에콰도르" in hint

    def test_hint_is_not_a_partial_sample(self):
        """부분 목록을 '유효 값' 으로 내밀던 옛 문구가 돌아오지 않게."""
        hint = _country_hint("SELECT 1 WHERE Country = '에콰도르'")
        assert "미국, 인도네시아, 말레이시아" not in hint


class TestSplitAnd:
    def test_top_level_only(self):
        assert split_and("a = 1 AND (b = 2 OR c = 3) AND d = 'x AND y'") == [
            "a = 1", "(b = 2 OR c = 3)", "d = 'x AND y'"]

    def test_and_inside_quotes_is_not_a_boundary(self):
        assert split_and("name = 'Black AND White'") == ["name = 'Black AND White'"]

    def test_backticked_column_survives(self):
        # `SET` 은 BigQuery 예약어라 백틱이 붙어 온다
        assert split_and("`SET` LIKE '%A%' AND Country = '칠레'") == [
            "`SET` LIKE '%A%'", "Country = '칠레'"]


class TestParse:
    _SQL = ("SELECT SUM(x) FROM `p.d.t` WHERE Country = '에콰도르' "
            "AND FOC_or_Not = 'O' AND Date >= '2022-01-01' GROUP BY 1 LIMIT 10")

    def test_extracts_table_and_conditions(self):
        p = _parse(self._SQL)
        assert p["table"] == "`p.d.t`"
        assert len(p["conds"]) == 3
        assert "GROUP BY" not in p["conds"][-1] and "LIMIT" not in p["conds"][-1]

    @pytest.mark.parametrize("sql", [
        "SELECT 1 FROM `p.d.t`",                                  # WHERE 없음
        "SELECT 1 FROM `p.d.t` WHERE a = 1",                      # 조건 1개 — 뺄 게 없다
        "SELECT (SELECT 1) FROM `p.d.t` WHERE a = 1 AND b = 2",   # 서브쿼리는 다루지 않는다
    ])
    def test_skips_when_unsure(self, sql):
        """확신이 없으면 진단을 건너뛴다 — 틀린 진단보다 없는 게 낫다."""
        assert _parse(sql) is None


# ── "없다" 와 "아직 안 들어왔다" 는 다르다 (2026-08-31 사용자 제보) ───────────
#
# ⛔ 8/30 KBT 광고를 물었을 때 NaverGFA·NaverSearch 가 빠진 채 답이 나갔고,
#    이유를 "해당 조건의 데이터가 존재하지 않습니다" 라고 단정했다. 실제로는
#    그 시각에 아직 적재 전이었고 지금은 각각 72만원·66만원이 들어 있다.
#    같은 사용자가 30분 사이 같은 질문에 8/26 → 8/30 으로 갈린 답을 받았다.
#    성분의 '미상'을 '미포함'으로 쓰던 오답과 같은 부류다.

def test_date_axis_is_picked_from_the_conditions():
    from app.core.zero_row import _date_axis

    assert _date_axis(["date = '2026-08-30'", "team = 'KBT'"]) == ("date", "2026-08-30")
    col, asked = _date_axis(["Date BETWEEN '2026-08-01' AND '2026-08-31'"])
    assert (col, asked) == ("Date", "2026-08-31"), "가장 늦은 날짜를 골라야 한다"
    # 날짜가 없는 조건만 있으면 축이 없다
    assert _date_axis(["team = 'KBT'", "media = 'NaverGFA'"]) == (None, None)
    # 날짜 리터럴이 있어도 컬럼 이름이 날짜가 아니면 축으로 삼지 않는다
    assert _date_axis(["memo = '2026-08-30 메모'"]) == (None, None)


def test_lag_is_called_out_only_when_the_question_is_ahead():
    """⚠️ 반대 방향이 없으면 멀쩡한 0행에도 '적재 시차' 를 붙여 새 오답이 된다."""
    from app.core.zero_row import _loading_lag_note

    note = _loading_lag_note("date", "2026-08-30", "2026-08-26")
    assert "적재 시차" in note and "2026-08-26" in note and "2026-08-30" in note
    assert "존재하지 않는다" in note, "단정하지 말라는 지시가 빠지면 LLM 이 되돌아간다"

    assert _loading_lag_note("date", "2026-08-26", "2026-08-30") == "", "최신인데 시차라 했다"
    assert _loading_lag_note("date", "2026-08-30", "2026-08-30") == "", "같은 날은 시차가 아니다"
    assert _loading_lag_note("date", "2026-08-30", None) == ""
    assert _loading_lag_note(None, None, None) == ""


def test_probe_measures_the_loaded_maximum_in_the_same_query():
    """⚠️ 0행 경로에 조회를 더 늘리지 않는다 — 기존 프로브에 열 하나를 더할 뿐이다."""
    import inspect

    from app.core import zero_row
    src = inspect.getsource(zero_row.diagnose)
    assert "loaded_max" in src and src.count("bq.execute_query") == 1


# ── BETWEEN 의 AND 로 자르면 진단이 통째로 죽는다 (2026-08-31 실측) ──────────
#
# ⛔ `date BETWEEN 'a' AND 'b'` 가 두 조각으로 갈려 `date BETWEEN 'a'` 라는
#    **깨진 SQL** 이 됐다. 프로브가 BigQuery 에서 실패하면 진단은 빈 문자열을
#    돌려주므로(WARNING 만 남는다) **기간을 BETWEEN 으로 쓴 질문은 0행 진단이
#    늘 죽어 있었다.** 에러가 안 나서 오래 안 드러났다.

def test_between_keeps_its_own_and():
    from app.core.zero_row import split_and

    got = split_and("date BETWEEN '2026-08-01' AND '2026-08-31' AND media = 'X'")
    assert got == ["date BETWEEN '2026-08-01' AND '2026-08-31'", "media = 'X'"], got

    assert split_and("date BETWEEN '2026-08-01' AND '2026-08-31'") == [
        "date BETWEEN '2026-08-01' AND '2026-08-31'"]

    got = split_and("team = 'KBT' AND date BETWEEN '2026-01-01' AND '2026-06-30' AND m = 'Y'")
    assert len(got) == 3, got


def test_ordinary_and_splitting_still_works():
    """⚠️ 반대 방향 — BETWEEN 을 살리려다 평범한 AND 를 안 자르면 진단이 무의미해진다."""
    from app.core.zero_row import split_and

    assert split_and("a = 1 AND (b = 2 OR c = 3) AND d = 'x AND y'") == [
        "a = 1", "(b = 2 OR c = 3)", "d = 'x AND y'"]
    assert split_and("date = '2026-08-30' AND team = 'KBT'") == [
        "date = '2026-08-30'", "team = 'KBT'"]
