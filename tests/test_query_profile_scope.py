# -*- coding: utf-8 -*-
"""자주 묻는 질문 칩의 담당 국가 범위 회귀 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from app.core import query_profile as qp


# ⚠️ 매출과 광고는 **다른 축**이다. B2B2 는 태국을 팔지만 광고는 돌리지 않는다 —
#    실측에서 광고 집행 팀은 7개뿐이다. 한 사전에 담되 섞이지 않게 키를 나눈다.
TEAM_SCOPE = {
    "teams": {
        "EAST2": {"말레이시아", "싱가포르"},
        "B2B2": {"태국"},
    },
    "all": {"말레이시아", "미국"},
    "ad_teams": {"EAST2": {"말레이시아"}},
    "ad_all": {"미국"},
}


def _rebuild_with(
    monkeypatch,
    *,
    email: str,
    department: str,
    questions: List[str],
    scope_map: Dict[str, Any] | None,
):
    """외부 저장소 없이 실제 후보 선택과 저장 payload를 관찰한다."""
    base = datetime(2026, 8, 27, 9, 0, 0)
    audit_rows = [
        {
            "query": question,
            "route": "bigquery",
            "created_at": base - timedelta(minutes=index),
            "ctx": 0,
        }
        for index, question in enumerate(questions)
    ]
    writes = []

    def fake_fetch_all(sql, params=()):
        if "FROM audit_logs" in sql:
            return audit_rows
        if "FROM users u" in sql and "directory_users" in sql:
            return [{"department": department}]
        raise AssertionError(f"예상하지 않은 DB 조회: {sql}")

    def fake_execute(sql, params=()):
        if sql.startswith("INSERT INTO user_query_profile"):
            writes.append(params)
        return 1

    monkeypatch.setattr(qp, "ensure_tables", lambda: None)
    monkeypatch.setattr(qp, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(qp, "execute", fake_execute)

    stats = qp.rebuild(email, scope_map=scope_map)
    saved = [params[2] for params in writes if params[1] == "question"]
    return saved, stats


def test_east2_drops_thailand_but_keeps_malaysia(monkeypatch):
    """팀 범위를 무시하면 EAST2 사용자에게 태국 칩이 다시 노출된다."""
    saved, stats = _rebuild_with(
        monkeypatch,
        email="east2@skin1004korea.com",
        department="글로벌마케팅본부 동남아시아2팀",
        questions=["2026년 3월 태국 매출 알려줘", "2026년 3월 말레이시아 매출 알려줘"],
        scope_map=TEAM_SCOPE,
    )

    assert "2026년 3월 태국 매출 알려줘" not in saved
    assert "2026년 3월 말레이시아 매출 알려줘" in saved
    assert stats["dropped_out_of_scope"] == 1


def test_b2b2_keeps_thailand(monkeypatch):
    """같은 태국 질문을 전 사용자에게 버리면 영업2팀의 정당한 칩도 사라진다."""
    saved, stats = _rebuild_with(
        monkeypatch,
        email="b2b2@skin1004korea.com",
        department="영업1본부 영업2팀",
        questions=["2026년 3월 태국 매출 알려줘"],
        scope_map=TEAM_SCOPE,
    )

    assert saved == ["2026년 3월 태국 매출 알려줘"]
    assert stats["dropped_out_of_scope"] == 0


def test_company_scope_drops_country_below_company_threshold(monkeypatch):
    """팀이 없는 사용자는 팀 목록이 아니라 전사 1% 목록을 써야 한다."""
    saved, stats = _rebuild_with(
        monkeypatch,
        email="analyst@skin1004korea.com",
        department="데이터분석파트",
        questions=["2026년 3월 태국 매출 알려줘", "2026년 3월 미국 매출 알려줘"],
        scope_map=TEAM_SCOPE,
    )

    assert "2026년 3월 태국 매출 알려줘" not in saved
    assert "2026년 3월 미국 매출 알려줘" in saved
    assert stats["dropped_out_of_scope"] == 1


def test_question_without_country_is_not_filtered(monkeypatch):
    """국가 필터가 없는 추이 질문까지 버리면 개인 칩의 유용한 축이 사라진다."""
    saved, stats = _rebuild_with(
        monkeypatch,
        email="east2@skin1004korea.com",
        department="동남아시아2팀",
        questions=["이번 달 매출 추이"],
        scope_map=TEAM_SCOPE,
    )

    assert saved == ["이번 달 매출 추이"]
    assert stats["dropped_out_of_scope"] == 0


def test_none_scope_map_preserves_existing_behavior(monkeypatch):
    """범위 지도가 없을 때 필터링하면 기존 개별 호출과 장애 우회가 깨진다."""
    saved, stats = _rebuild_with(
        monkeypatch,
        email="east2@skin1004korea.com",
        department="동남아시아2팀",
        questions=["2026년 3월 태국 매출 알려줘"],
        scope_map=None,
    )

    assert saved == ["2026년 3월 태국 매출 알려줘"]
    assert stats["dropped_out_of_scope"] == 0


def test_scope_country_map_builds_team_and_company_ranges(monkeypatch):
    """국가를 손으로 고정하거나 사용자별로 조회하는 회귀를 막는다."""
    calls = []

    class FakeBigQueryClient:
        def execute_query(self, sql):
            calls.append(sql)
            if "Team_NEW AS team" in sql:
                return [
                    {"team": "EAST2", "country": "말레이시아", "share": 0.55},
                    {"team": "EAST2", "country": "싱가포르", "share": 0.45},
                    {"team": "B2B2", "country": "태국", "share": 0.0506},
                ]
            if "integrated_ad" in sql and "GROUP BY team, country" in sql:
                return [{"team": "EAST2", "country": "말레이시아", "share": 0.83}]
            if "integrated_ad" in sql:
                return [{"country": "미국", "share": 0.33}]
            return [
                {"country": "말레이시아", "share": 0.02},
                {"country": "미국", "share": 0.30},
            ]

    from app.core import bigquery

    monkeypatch.setattr(bigquery, "BigQueryClient", FakeBigQueryClient)

    assert qp.scope_country_map() == TEAM_SCOPE
    # 매출 팀·전사 + 광고 팀·전사 = 네 축을 각각 한 번씩. 사용자 수와 무관해야 한다.
    assert len(calls) == 4, len(calls)
    assert "WHERE t.amt / s.team_total >= 0.01" in calls[0]
    assert "Team_NEW" not in calls[1]
    assert all("integrated_ad" in sql for sql in calls[2:])


def test_bigquery_failure_disables_scope_filter(monkeypatch):
    """범위 조회 장애가 추천 칩 전체의 장애로 번지면 안 된다."""
    class BrokenBigQueryClient:
        def execute_query(self, sql):
            raise RuntimeError("offline")

    from app.core import bigquery

    monkeypatch.setattr(bigquery, "BigQueryClient", BrokenBigQueryClient)
    assert qp.scope_country_map() is None

    saved, stats = _rebuild_with(
        monkeypatch,
        email="east2@skin1004korea.com",
        department="동남아시아2팀",
        questions=["2026년 3월 태국 매출 알려줘"],
        scope_map=None,
    )
    assert saved == ["2026년 3월 태국 매출 알려줘"]
    assert stats["dropped_out_of_scope"] == 0


def test_rebuild_all_loads_scope_once_for_every_user(monkeypatch):
    """범위 조회를 사용자 루프 안으로 옮기면 BigQuery 비용과 시간이 인원수만큼 는다."""
    scope_calls = []
    rebuilt = []
    sentinel = {"teams": {}, "all": set()}

    monkeypatch.setattr(qp, "ensure_tables", lambda: None)
    monkeypatch.setattr(
        qp,
        "fetch_all",
        lambda sql, params=(): [
            {"user_email": "one@skin1004korea.com"},
            {"user_email": "two@skin1004korea.com"},
        ],
    )
    monkeypatch.setattr(
        qp,
        "scope_country_map",
        lambda: scope_calls.append("called") or sentinel,
    )
    monkeypatch.setattr(
        qp,
        "rebuild",
        lambda email, scope_map=None: rebuilt.append((email, scope_map)) or {},
    )

    assert qp.rebuild_all() == {"users": 2}
    assert scope_calls == ["called"]
    assert rebuilt == [
        ("one@skin1004korea.com", sentinel),
        ("two@skin1004korea.com", sentinel),
    ]


# ── 마케팅·ROAS 는 광고 실적 기준으로 좁힌다 (2026-08-27 사용자 지시) ────────────

def _ad_scope_map():
    """매출과 광고의 팀 구성이 **다르다**는 것을 그대로 담은 표본 (실측 기반).

    영업2팀(B2B2)은 태국을 팔지만 광고는 한 푼도 쓰지 않는다.
    """
    return {
        "teams": {"B2B2": {"태국", "폴란드"}, "EAST2": {"말레이시아", "싱가포르"}},
        "all": {"미국", "인도네시아", "일본", "태국"},
        "ad_teams": {"EAST2": {"말레이시아", "싱가포르"}},   # B2B2 는 아예 없다
        "ad_all": {"미국", "인도네시아", "말레이시아", "일본"},  # 태국 없음
    }


def test_ad_questions_use_the_ad_scope_not_the_sales_scope(monkeypatch):
    """⛔ 광고 질문을 매출 범위로 거르면 **광고하지 않는 나라를 허용**한다.

    전사 기준 실측: 매출 1% 국가 22개 vs 광고 1% 국가 10개. 태국은 매출에는 있고
    광고에는 없다 — 축을 섞으면 "태국 ROAS" 가 그대로 통과한다.
    """
    from app.core import query_profile

    assert query_profile._is_ad_question("태국 ROAS 알려줘")
    assert query_profile._is_ad_question("국가별 Facebook 광고 효율")
    assert not query_profile._is_ad_question("2026년 3월 태국 매출 알려줘")


def test_a_team_without_ads_falls_back_to_company_ad_scope():
    """⚠️ 광고를 안 돌리는 팀(영업·유통·BCM)의 범위는 **비어 있다.**

    그대로 쓰면 그분들의 마케팅 질문이 통째로 사라진다. 사용자 지시대로
    "없으면 전사 기준" 으로 떨어뜨린다.
    """
    import inspect

    from app.core import query_profile

    src = inspect.getsource(query_profile.rebuild)
    assert "_pick(" in src, "범위 선택이 한 곳으로 모이지 않았다"
    assert "ad_teams" in src and "ad_all" in src
    # 팀 범위가 비면 전사로 떨어지는 분기가 있어야 한다
    assert "if mine:" in src, "빈 팀 범위를 전사로 폴백하지 않는다"


def test_ad_scope_query_bounds_the_future_rows():
    """⚠️ `integrated_ad` 에는 미래 날짜 행이 있다 (CLAUDE.md 실측) — 상한을 걸지 않으면
    범위 계산에 아직 일어나지 않은 집행이 섞인다."""
    from app.core import query_profile

    for sql in (query_profile._AD_TEAM_SCOPE_SQL, query_profile._AD_COMPANY_SCOPE_SQL):
        assert "date <= CURRENT_DATE()" in sql, sql[:80]
        assert "integrated_ad" in sql


def test_scope_map_keeps_sales_and_ads_apart(monkeypatch):
    """두 축이 한 사전 안에서 **섞이지 않아야** 한다 — 섞이면 조용히 잘못 걸러진다."""
    from app.core import query_profile

    class FakeBQ:
        def __init__(self):
            self.calls = 0

        def execute_query(self, sql):
            self.calls += 1
            if "integrated_ad" in sql and "team" in sql and "GROUP BY team, country" in sql:
                return [{"team": "EAST2", "country": "말레이시아", "share": 0.8}]
            if "integrated_ad" in sql:
                return [{"country": "미국", "share": 0.3}]
            if "Team_NEW" in sql:
                return [{"team": "B2B2", "country": "태국", "share": 0.05}]
            return [{"country": "태국", "share": 0.01}]

    fake = FakeBQ()
    monkeypatch.setattr("app.core.bigquery.BigQueryClient", lambda: fake)
    out = query_profile.scope_country_map()

    assert out["teams"] == {"B2B2": {"태국"}}
    assert out["all"] == {"태국"}
    assert out["ad_teams"] == {"EAST2": {"말레이시아"}}
    assert out["ad_all"] == {"미국"}
    assert fake.calls == 4, "네 축을 각각 한 번씩만 조회해야 한다"
