"""개인화 데일리 브리핑 — 2026-08-20.

왜: 30일 활성 29명 중 **하루만 쓴 사람이 11명(38%)**. "궁금할 때 찾아가는 도구" 라서
궁금하지 않은 날은 아무도 오지 않는다. 먼저 찾아가는 알림이 빈도를 올리는 유일한 길이다.

⛔ 이 테스트가 지키는 것은 **틀린 숫자를 먼저 보내지 않는 것**이다. 묻지도 않았는데 간
   숫자가 틀리면 신뢰 회복이 안 된다. 실측에서 나온 함정 셋을 고정한다:
   ① 적재 지연 — 어제 358행 / 그저께 21,653행. "어제 매출" 로 보내면 급감으로 읽힌다
   ② 미래 날짜 8,741건 — 안 막으면 합계가 부푼다
   ③ 하루 대 하루 비교 — B2B 는 하루 0이 정상이라 "−100%" 가 쏟아진다
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core import briefing


class _BQ:
    """일별 행수와 집계를 흉내내는 최소 클라이언트."""

    def __init__(self, daily_counts):
        self.daily = daily_counts        # [(date, rows)]
        self.queries = []

    def execute_query(self, sql, **kw):
        self.queries.append(sql)
        return [{"d": d, "n": n} for d, n in self.daily]


def test_stable_date_skips_days_still_loading():
    """① 적재 중인 날은 기준일이 될 수 없다 (실측 패턴 그대로)."""
    today = date(2026, 8, 20)
    bq = _BQ([(today, 117), (today - timedelta(days=1), 358),
              (today - timedelta(days=2), 21653), (today - timedelta(days=3), 34138),
              (today - timedelta(days=4), 29983), (today - timedelta(days=5), 35380)])
    assert briefing.stable_date(bq, "t") == today - timedelta(days=2)


def test_stable_date_query_excludes_future_rows():
    """② 미래 날짜는 애초에 조회하지 않는다."""
    bq = _BQ([(date(2026, 8, 18), 30000)])
    briefing.stable_date(bq, "t")
    assert "Date <= CURRENT_DATE()" in bq.queries[0]


def test_collect_compares_seven_day_windows():
    """③ 하루가 아니라 7일 합계끼리 견준다."""
    captured = []

    class _C:
        def execute_query(self, sql, **kw):
            captured.append(sql)
            return []

    briefing.collect(_C(), "t", date(2026, 8, 18))
    joined = " ".join(captured)
    assert "2026-08-12" in joined and "2026-08-18" in joined      # 최근 7일
    assert "2026-08-05" in joined and "2026-08-11" in joined      # 직전 7일


def _data(now, prev, countries=()):
    return {
        "base": date(2026, 8, 18), "cur_from": date(2026, 8, 12),
        "prev_from": date(2026, 8, 5), "prev_to": date(2026, 8, 11),
        "by_team": {"EAST1": {"now": now, "prev": prev}},
        "countries": [{"team": "EAST1", "country": c, "now_amt": n, "prev_amt": p}
                      for c, n, p in countries],
    }


TEAM = {"kind": "team", "code": "EAST1", "label": "동남아시아1팀"}


def test_no_notable_change_sends_nothing():
    """⛔ 평소와 같은 날은 보내지 않는다 — 매일 뜨는 알림은 곧 무시당한다."""
    assert briefing.compose(TEAM, _data(10e8, 10.3e8)) is None


def test_notable_change_is_sent():
    b = briefing.compose(TEAM, _data(20e8, 10e8))
    assert b and "+100%" in b["title"]
    assert "최근 7일" in b["title"]


def test_team_with_no_records_is_skipped():
    """기록이 없는 팀에 "0원 · −100%" 를 보내지 않는다."""
    assert briefing.compose(TEAM, _data(0, 0)) is None


def test_change_item_needs_scale_on_both_sides():
    """⛔ 한쪽만 크면 "0원 → −100%" 가 1위로 올라온다 (규모 없는 변화는 변화가 아니다)."""
    d = _data(20e8, 10e8, countries=[("미국", 0, 4e7), ("베트남", 5e8, 2e8)])
    b = briefing.compose(TEAM, d)
    assert "미국" not in b["body"]
    assert "베트남" in b["body"]


def test_change_item_is_not_the_team_itself():
    """팀이 한 나라만 담당하면 그 나라는 팀 합계와 **같은 말**이다."""
    d = _data(8.2e8, 4.6e8, countries=[("일본", 8.2e8, 4.6e8)])
    b = briefing.compose(TEAM, d)
    assert "눈에 띄는 변화" not in b["body"]


def test_body_discloses_the_base_date():
    """기준일을 밝히지 않으면 "언제 숫자냐" 를 되묻게 된다."""
    b = briefing.compose(TEAM, _data(20e8, 10e8))
    assert "2026-08-18" in b["body"] and "적재 지연" in b["body"]


def test_repeat_of_the_same_story_is_skipped(monkeypatch):
    """같은 추세가 이어지면 같은 문장이 매일 간다 — 제목이 같으면 보내지 않는다."""
    monkeypatch.setattr(briefing, "fetch_one", lambda *a, **k: {"title": "동남아시아1팀 최근 7일 매출 20.0억 · 직전 7일 대비 +100%"})
    assert briefing.is_repeat(1, "동남아시아1팀 최근 7일 매출 20.0억 · 직전 7일 대비 +100%") is True
    assert briefing.is_repeat(1, "다른 이야기") is False


@pytest.mark.parametrize("dep,expected", [
    ("글로벌마케팅본부 > 동남아시아1팀", "동남아시아1팀"),
    ("글로벌마케팅본부 > 서구권 마케팅팀", "서구권마케팅팀"),   # 공백 표기 차이
    ("상품본부 > 브랜드 상품팀", "전사"),                      # 매칭 실패 → 전사
])
def test_scope_resolution(dep, expected):
    team_map = {"EAST1": "동남아시아1팀", "WEST_MKT": "서구권마케팅팀", "JBT": "일본사업팀"}
    assert briefing.resolve_scope(dep, team_map)["label"] == expected


def test_llm_is_not_used_anywhere():
    """숫자도 문장도 코드가 만든다 — 브리핑에 LLM 이 끼면 검증할 수 없는 문장이 나간다."""
    import inspect
    src = inspect.getsource(briefing)
    for bad in ("generate", "llm", "gemini", "claude"):
        assert bad not in src.lower().replace("llm 은", "").replace("llm 이", "")


# ── 관심 국가 추론 (팀이 매출 축에 없는 사람들) ─────────────────────────────

def test_infer_country_needs_repetition(monkeypatch):
    """한두 번 지나가듯 물은 나라를 관심사로 삼지 않는다."""
    # 세 번 이상 물어야 관심사로 인정한다
    rows = [{"q": "일본 매출"}, {"q": "일본 리뷰"}, {"q": "일본 큐텐 순위"},
            {"q": "베트남 매출 알려줘"}]
    monkeypatch.setattr(briefing, "fetch_all", lambda *a, **k: rows)
    assert briefing.infer_country("me@x.com", ["일본", "베트남"]) == "일본"

    monkeypatch.setattr(briefing, "fetch_all",
                        lambda *a, **k: [{"q": "일본 매출"}, {"q": "일본 리뷰"}])
    assert briefing.infer_country("me@x.com", ["일본"]) is None      # 2회 → 인정 안 함


def test_infer_country_uses_measured_list_not_a_hardcoded_one():
    """국가 목록은 값 목록 캐시에서 온다 — 손으로 적으면 반드시 낡는다."""
    import inspect
    src = inspect.getsource(briefing.run_daily)
    assert "value_lists" in src and "_cached" in src


def test_country_scope_does_not_repeat_itself():
    """국가 축이면 그 나라가 주제다 — '눈에 띄는 변화: 그 나라' 는 같은 말이다."""
    data = {
        "base": date(2026, 8, 18), "cur_from": date(2026, 8, 12),
        "prev_from": date(2026, 8, 5), "prev_to": date(2026, 8, 11),
        "by_team": {}, "countries": [{"team": "X", "country": "일본",
                                      "now_amt": 20e8, "prev_amt": 10e8}],
        "by_country": {"일본": {"now": 20e8, "prev": 10e8}},
    }
    b = briefing.compose({"kind": "country", "code": "일본", "label": "일본"}, data)
    assert b and "+100%" in b["title"]
    assert "눈에 띄는 변화" not in b["body"]


def test_leavers_are_excluded():
    """퇴사자에게 매일 매출 브리핑을 보내지 않는다."""
    import inspect
    assert "퇴사" in inspect.getsource(briefing.run_daily)
