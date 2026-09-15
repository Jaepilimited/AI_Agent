# -*- coding: utf-8 -*-
"""스킨천사는 좀비뷰티를 포함한다 — 2026-09-15 사용자 확정.

사용자 지시: "보통 스킨천사는 좀비뷰티 같이 확인해. 그래서 따로 보지 마"

⚠️ 차이가 0.1% 라 **표만 보고는 틀린 줄 모른다.** 그래서 프롬프트가 아니라 코드가
   보증하고, 회귀는 양방향으로 건다 — 걷어야 할 때 걷는가, 건드리면 안 될 때 두는가.
"""

from pathlib import Path

import pytest

from app.agents.sql_agent import _include_zombie_in_skin1004

_ROOT = Path(__file__).resolve().parents[1]

_BASE = ("SELECT Country, SUM(Sales1_R) r FROM "
         "`skin1004-319714.Sales_Integration.SALES_ALL_Backup` "
         "WHERE Date BETWEEN '2026-01-01' AND '2026-12-31' AND Brand IN ('SK','CBT')")


# ── 걷어야 하는 것 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("clause", [
    " AND (Line != 'ZB' OR Line IS NULL)",
    " AND (Line <> 'ZB' OR Line IS NULL)",
    " AND Line != 'ZB'",
    " AND Line <> 'ZB'",
    " AND (`Line` != 'ZB' OR `Line` IS NULL)",
    " AND (t.Line != 'ZB' OR t.Line IS NULL)",
])
def test_zombie_exclusion_is_dropped_for_a_plain_skin1004_question(clause: str) -> None:
    sql = _BASE + clause + " GROUP BY Country"

    out = _include_zombie_in_skin1004(sql, "2026년 스킨천사 국가별 매출 알려줘")

    assert "ZB" not in out
    assert "Brand IN ('SK','CBT')" in out
    assert "GROUP BY Country" in out


def test_the_rest_of_the_query_is_untouched() -> None:
    sql = _BASE + " AND (Line != 'ZB' OR Line IS NULL) GROUP BY Country ORDER BY r DESC"

    out = _include_zombie_in_skin1004(sql, "스킨천사 매출")

    assert out == _BASE + " GROUP BY Country ORDER BY r DESC"


# ── 건드리면 안 되는 것 ─────────────────────────────────────────────────────

def test_brand_breakdown_case_is_left_alone() -> None:
    """⛔ 브랜드별 표는 `CASE` 순서가 갈라 준다 — 여기서 걷으면 이중 계상이 된다."""
    sql = (
        "SELECT CASE WHEN Brand='UM' THEN '우마' "
        "WHEN Brand='SK' AND Line='ZB' THEN '좀비뷰티' "
        "WHEN Brand IN ('SK','CBT') THEN '스킨천사' ELSE '기타' END AS brand_name, "
        "SUM(Sales1_R) r FROM `skin1004-319714.Sales_Integration.SALES_ALL_Backup` "
        "WHERE Date BETWEEN '2026-01-01' AND '2026-12-31' "
        "AND (Line != 'ZB' OR Line IS NULL) GROUP BY brand_name")

    assert _include_zombie_in_skin1004(sql, "브랜드별 매출 알려줘") == sql


@pytest.mark.parametrize("question", [
    "스킨천사 매출 알려줘 좀비뷰티 제외",
    "좀비뷰티 빼고 스킨천사 매출",
    "스킨천사 매출 (좀비 제외)",
])
def test_an_explicit_exclusion_by_the_user_is_respected(question: str) -> None:
    """사용자가 스스로 뺀 것은 되돌리지 않는다 (기간을 직접 자른 것과 같은 규칙)."""
    sql = _BASE + " AND (Line != 'ZB' OR Line IS NULL)"

    assert _include_zombie_in_skin1004(sql, question) == sql


def test_a_zombie_only_question_still_works() -> None:
    sql = _BASE.replace("Brand IN ('SK','CBT')", "Brand = 'SK' AND Line = 'ZB'")

    assert _include_zombie_in_skin1004(sql, "좀비뷰티 매출 알려줘") == sql


def test_sql_without_the_clause_is_returned_unchanged() -> None:
    assert _include_zombie_in_skin1004(_BASE, "스킨천사 매출") == _BASE
    assert _include_zombie_in_skin1004("", "스킨천사") == ""


def test_a_line_column_that_is_not_zb_is_untouched() -> None:
    sql = _BASE + " AND Line != 'B_Line'"

    assert _include_zombie_in_skin1004(sql, "스킨천사 매출") == sql


# ── 배선 ────────────────────────────────────────────────────────────────────

def test_every_sql_path_runs_the_guard() -> None:
    """⛔ 한 곳만 고치면 경로에 따라 답이 갈린다 (이미 겪은 사고다).

    생성·재생성 4경로 전부에서 돌아야 한다 — 기존 브랜드 후처리와 같은 자리다.
    """
    src = (_ROOT / "app" / "agents" / "sql_agent.py").read_text(encoding="utf-8")

    calls = src.count("_include_zombie_in_skin1004(")
    strips = src.count("_strip_unrequested_brand_filter(")

    assert calls == strips, "브랜드 후처리와 호출 횟수가 다르다 — 경로가 빠졌다"
    assert calls == 5, f"정의 1 + 호출 4 = 5 여야 한다 (지금 {calls})"


def test_the_rule_is_written_where_the_llm_reads_it() -> None:
    prompt = (_ROOT / "prompts" / "sql_generator.txt").read_text(encoding="utf-8")

    # 옛 정의가 되살아나면 안 된다
    assert "`Brand IN ('SK','CBT') AND (Line != 'ZB' OR Line IS NULL)`" not in prompt
    assert "Brand IN ('SK','CBT')" in prompt
    # 브랜드별 CASE 는 그대로 있어야 한다 (여기서 좀비뷰티가 갈린다)
    assert "WHEN Brand = 'SK' AND Line = 'ZB' THEN '좀비뷰티'" in prompt


def test_report_brand_filter_matches_the_rule() -> None:
    """보고서 경로도 같은 정의를 써야 한다 — 사본이 갈리면 경로마다 답이 달라진다."""
    from app.reports.registry import _BRAND_FILTERS

    assert _BRAND_FILTERS["스킨천사"] == {"브랜드": ["SK", "CBT"]}
    assert "라인" not in _BRAND_FILTERS["스킨천사"]
    assert _BRAND_FILTERS["좀비뷰티"] == {"브랜드": ["SK"], "라인": ["ZB"]}
