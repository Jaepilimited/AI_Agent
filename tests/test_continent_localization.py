# -*- coding: utf-8 -*-
"""사용자가 말하는 대륙 별칭(남미·중미)을 실재하는 값으로 교정한다.

배경 — 데이터에는 `Continent1` 에 `중남미` 하나로 통합돼 있고, 더 잘게는
`Continent2` 에 `남아메리카`·`중앙아메리카` 로 있다. 사용자는 "남미"·"중미"라고 말한다.
프롬프트 지시만으로는 확률적이라 **후처리가 보증**한다 (국가·팀 리터럴과 같은 계열).

⛔ 이 파일이 지키는 핵심은 **섞인 조건**이다. 컬럼을 통째로 바꾸면
   `Continent1 IN ('남미','유럽')` 이 `Continent2 IN ('남아메리카','유럽')` 이 되는데
   **`유럽` 은 Continent2 에 없다** — 절반이 조용히 0건이 되고, 답변은 그걸
   "유럽 매출이 없다" 로 설명한다. 에러가 없어 가장 늦게 발견되는 부류다.
"""

import pytest

from app.agents.sql_agent import _localize_continent_literals as loc


class TestAliasOnlyPredicates:
    """조건이 전부 남미·중미면 더 정확한 Continent2 로 옮긴다."""

    def test_single_alias(self):
        out = loc("SELECT SUM(Sales1_R) FROM t WHERE Continent1='남미'")
        assert "Continent2" in out and "'남아메리카'" in out
        assert "'남미'" not in out

    def test_alias_list(self):
        out = loc("SELECT SUM(Sales1_R) FROM t WHERE Continent1 IN ('남미','중미')")
        assert "Continent2" in out
        assert "'남아메리카'" in out and "'중앙아메리카'" in out

    def test_select_and_group_by_follow_the_column(self):
        """WHERE 만 바꾸고 SELECT 를 두면 라벨과 필터가 어긋난다."""
        out = loc("SELECT Continent1, SUM(Sales1_R) FROM t "
                  "WHERE Continent1='남미' GROUP BY Continent1")
        assert "Continent1" not in out
        assert out.count("Continent2") == 3


class TestMixedPredicateMustNotBreakOtherValues:
    """⛔ 이번 수정의 이유 — 섞인 조건에서 컬럼을 통째로 바꾸면 안 된다."""

    def test_mixed_with_continent1_only_value(self):
        out = loc("SELECT SUM(Sales1_R) FROM t WHERE Continent1 IN ('남미','유럽')")
        # '유럽' 은 Continent2 에 없다 — 컬럼을 바꾸면 그 절반이 0건이 된다
        assert "Continent2" not in out, f"컬럼을 바꾸면 안 된다: {out}"
        assert "'유럽'" in out
        assert "'중남미'" in out, "같은 컬럼에 실재하는 값으로 옮겨야 한다"
        assert "'남미'" not in out.replace("'중남미'", "")

    def test_mixed_dedupes_when_both_aliases_collapse(self):
        """남미·중미가 둘 다 중남미로 접히면 값이 중복된다."""
        out = loc("SELECT SUM(Sales1_R) FROM t "
                  "WHERE Continent1 IN ('남미','중미','유럽')")
        assert out.count("'중남미'") == 1, f"중복이 남았다: {out}"
        assert "'유럽'" in out


class TestNoFalsePositives:
    def test_jungnammi_is_not_touched(self):
        """⚠️ `'중남미'` 안에 '남미' 가 들어 있다 — 인용부호 경계를 봐야 한다."""
        sql = "SELECT SUM(Sales1_R) FROM t WHERE Continent1='중남미'"
        assert loc(sql) == sql

    def test_unrelated_sql_untouched(self):
        sql = "SELECT SUM(Sales1_R) FROM t WHERE Continent1='유럽'"
        assert loc(sql) == sql

    def test_continent2_already_correct_untouched(self):
        sql = "SELECT SUM(Sales1_R) FROM t WHERE Continent2='남아메리카'"
        assert loc(sql) == sql

    @pytest.mark.parametrize("sql", ["", None])
    def test_empty_input(self, sql):
        assert loc(sql) == sql
