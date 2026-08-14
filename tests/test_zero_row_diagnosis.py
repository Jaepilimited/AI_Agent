# -*- coding: utf-8 -*-
"""0행 원인을 **추측하지 않는지** 지킨다.

사고 원문 (2026-08-14 사용자 제보): "에콰도르 Valkirias FOC 볼 수 있나" 에
"에콰도르는 유효 국가 목록에 존재하지 않습니다" 라고 답했다. 실제로는
2,448건·33.8억이 있었다. 원인은 0행 힌트가 국가 191개 중 12개만 나열하고
"등" 을 붙인 것 — LLM 이 그걸 **전체 목록으로 읽었다**.
"""
import pytest

from app.agents.sql_agent import _all_countries, _country_hint
from app.core.zero_row import _parse, split_and


class TestCountryHint:
    def test_full_list_parsed(self):
        """프롬프트의 DISTINCT 목록을 단일 소스로 읽는다 (코드에 목록을 또 두지 않는다)."""
        assert len(_all_countries()) > 150

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
