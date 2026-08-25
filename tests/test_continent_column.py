# -*- coding: utf-8 -*-
"""대륙 값이 반대 컬럼에 걸리면 바로잡는다 — 붐따 #120 (박혜진, 2026-08-05).

    질문: "내가 말하는 유럽은 서유럽 동남유럽 북유럽 모두 합친 유럽이야"
    결과: 0건. 답변은 "대륙 필드가 세분화되어 저장되어 있어서" 라고 설명했다.

⛔ 사실이 아니다. `Continent1` 에는 **'유럽' 이 있다** (실측 10개: CIS·글로벌·기타·
   북미·아시아·아프리카·오세아니아·**유럽**·중남미·중동). 없는 것은 `Continent2` 쪽이고
   (16개: …동남유럽·북유럽·서유럽…), SQL 이 하필 그 컬럼에 '유럽' 을 걸었다.
   에러가 아니라 **0건**이라 아무도 못 잡는다 — 대륙 오답의 반복되는 형태다.

값을 손으로 적지 않는다. 어느 컬럼에 무엇이 있는지는 `value_lists` 가 매일 실측한다.
"어느 한쪽에만 있는 값" 이면 그 컬럼으로 옮긴다 — 양쪽에 다 있는 값(북미·중동…)은
건드리지 않는다.
"""
import pytest

C1 = ["CIS", "글로벌", "기타", "북미", "아시아", "아프리카", "오세아니아", "유럽", "중남미", "중동"]
C2 = ["CIS", "글로벌_B2B", "글로벌_플랫폼", "기타", "남아메리카", "동남아시아", "동남유럽",
      "동아시아", "북미", "북유럽", "서남아시아", "서유럽", "아프리카", "오세아니아", "중동",
      "중앙아메리카"]


@pytest.fixture
def fix(monkeypatch):
    from app.agents import sql_agent
    from app.core import value_lists
    monkeypatch.setattr(value_lists, "values",
                        lambda n: C1 if n == "Continent1" else C2)
    return sql_agent._fix_continent_column


def test_europe_on_the_wrong_column_is_moved(fix):
    """#120 그 자체 — '유럽' 은 Continent1 에만 있다."""
    out = fix("SELECT Continent2, SUM(Sales1_R) FROM t WHERE Continent2 = '유럽' GROUP BY Continent2")
    assert "Continent1 = '유럽'" in out
    assert "Continent2" not in out          # 라벨·GROUP BY 도 함께 옮긴다


def test_southeast_asia_moves_the_other_way(fix):
    """CLAUDE.md 가 적어 둔 예외 — '동남아' 는 Continent2 에만 있다."""
    out = fix("SELECT 1 FROM t WHERE Continent1 = '동남아시아'")
    assert "Continent2 = '동남아시아'" in out


def test_correct_column_is_left_alone(fix):
    for sql in ("SELECT 1 FROM t WHERE Continent1 = '유럽'",
                "SELECT 1 FROM t WHERE Continent2 IN ('서유럽','동남유럽')"):
        assert fix(sql) == sql


def test_values_present_in_both_columns_are_never_moved(fix):
    """'북미'·'중동' 은 양쪽에 다 있다 — 옮길 근거가 없다."""
    sql = "SELECT 1 FROM t WHERE Continent2 IN ('북미','중동')"
    assert fix(sql) == sql


def test_a_mixed_condition_is_left_alone(fix):
    """⛔ 하나라도 그 컬럼에 실재하면 통째로 옮기면 안 된다 — 절반이 조용히 0건이 된다."""
    sql = "SELECT 1 FROM t WHERE Continent2 IN ('유럽','서유럽')"
    assert fix(sql) == sql


def test_no_value_list_means_no_change(monkeypatch):
    """실측 목록을 못 얻으면 아무것도 하지 않는다 — 추측으로 컬럼을 바꾸지 않는다."""
    from app.agents import sql_agent
    from app.core import value_lists
    monkeypatch.setattr(value_lists, "values", lambda n: None)
    sql = "SELECT 1 FROM t WHERE Continent2 = '유럽'"
    assert sql_agent._fix_continent_column(sql) == sql


def test_every_sql_generation_path_applies_it():
    """⛔ 한 경로만 고치면 재생성 때 다시 0건이 난다 — 팀·국가 교정과 같은 4경로다."""
    import inspect

    from app.agents import sql_agent

    src = inspect.getsource(sql_agent)
    assert src.count("_fix_continent_column(") == 1 + 4  # 정의 1 + 호출 4
