"""SQL 결과를 LLM에 보여주는 방식 -- 2026-08-31 인도네시아 매출 사고, 두 번째 원인.

행 상한을 200으로 올려도(첫 번째 커밋) 고쳐지지 않았다. 실측 결과: 146행 전부가
`format_answer`에 전달됐고 총합(1,839.0억)도 전체 146행 기준으로 정확했다 --
누락은 없었다. 그런데 LLM이 스스로 "매출 상위 15행"을 골라 표를 그렸고, 그 15행이
전부 2024~2026년이었다(매출이 매년 커졌으므로). 사용자는 "2020년-2026년"을
물었는데 2020~2023년이 통째로 안 보였다 -- 데이터가 없는 게 아니라 값 기준
정렬로 뽑은 표본에서 초기 연도가 전부 잘려 나간 것이다.

원인은 `_build_smart_preview`의 "총 매출 상위 15행" 표본 전략 자체가 아니라,
그 전략이 **시계열 질문에도 그대로 적용된 것**이다 (`if len(results) > 100:`이
`_is_timeseries` 분기보다 먼저 걸린다). 시계열 질문은 값이 아니라 기간이
축이므로, 표본을 뽑아야 한다면 값 기준이 아니라 기간 기준으로 고르게 뽑아야
한다.
"""
from __future__ import annotations

from app.agents.sql_agent import (
    _attach_full_data_download,
    _bounded_result_preview,
    _build_result_preview,
    _even_span_sample_preview,
)


def _indonesia_rows(start_year=2020, end_year=2026, end_month=1):
    """실제 사고 모양: 국가 하나, 월별 x B2C/B2B, 매출이 해가 갈수록 커진다."""
    rows = []
    year, month = start_year, 1
    base = 100
    while (year, month) <= (end_year, end_month):
        growth = (year - start_year) * 12 + month  # 뒤로 갈수록 매출이 크다
        for category in ("B2C", "B2B"):
            rows.append({
                "month": f"{year}-{month:02d}",
                "sales_type": category,
                "revenue": base * growth * (1.5 if category == "B2C" else 1.0),
            })
        month += 1
        if month > 12:
            month = 1
            year += 1
    return rows


def test_146_row_monthly_series_covers_every_year_not_just_the_biggest():
    rows = _indonesia_rows()
    assert len(rows) == 146, f"fixture drifted: {len(rows)} rows"

    preview, withheld = _build_result_preview(rows, "인도네시아의 2020년-2026년 현재까지의 B2C, B2B 월별 매출")

    for year in (2020, 2021, 2022, 2023, 2024, 2025, 2026):
        assert str(year) in preview, f"{year} missing from preview -- {preview[:200]}"
    assert withheld is False, "a pivot represents every row; nothing was withheld"


def test_non_timeseries_ranking_query_still_samples_by_value():
    """일반 순위 질문(시계열 아님)은 예전처럼 값 기준 상위 표본이 맞다 -- 회귀 방지."""
    rows = [{"product": f"P{i}", "revenue": i} for i in range(150)]

    preview, withheld = _build_result_preview(rows, "제품별 매출 순위 알려줘")

    assert withheld is True
    assert "P149" in preview  # highest revenue row survives
    assert "P0" not in preview  # lowest revenue row is the one that's sampled out


def test_unpivotable_timeseries_too_large_samples_across_the_whole_span_not_by_value():
    """피벗이 안 되는 모양(그룹 축이 없는 단일 시계열)이 60행을 넘고 100행도 넘으면
    마지막 수단으로 표본을 뽑는다 -- 그래도 값 기준이면 안 된다."""
    rows = [{"day": f"2020-01-{(i % 28) + 1:02d}-{i}", "revenue": i} for i in range(500)]

    preview, withheld = _build_result_preview(rows, "일별 매출 추이 알려줘")

    assert withheld is True
    # 값 기준으로 뽑았다면 뒤쪽(큰 값)만 남았을 것 -- 앞쪽도 반드시 남아야 한다
    assert '"revenue": 0' in preview or '"revenue":0' in preview or "day\": \"2020-01-01-0\"" in preview


def test_even_span_sample_keeps_both_ends():
    rows = [{"i": i} for i in range(1000)]

    preview = _even_span_sample_preview(rows, limit=20)

    assert '"i": 0' in preview
    assert '"i": 999' in preview


def test_bounded_preview_forces_withheld_true_when_hard_cap_still_trips():
    # A pathological non-timeseries case where even the smart preview can't
    # be trusted to fit -- the absolute safety net must still mark withheld.
    rows = [{"product": "x" * 200, "revenue": i} for i in range(300)]

    preview, withheld = _bounded_result_preview(rows, "제품별 매출 알려줘", hard_cap=50)

    assert withheld is True
    assert len(preview) <= 5000  # still bounded, just not by the artificially low hard_cap


def test_attach_full_data_download_strips_the_empty_promise_and_adds_a_real_link():
    from app.core import sql_result_store

    rows = [{"month": "2020-01", "revenue": 100}]
    answer = "### 결과\n\n표 생략\n\n※ 전체 데이터가 필요하시면 추가로 요청해 주세요"

    out = _attach_full_data_download(answer, rows, user_id=9, rows_withheld=True)

    assert "필요하시면" not in out
    assert "/api/sql-results/" in out
    import re
    m = re.search(r"/api/sql-results/([^)\s]+)/csv", out)
    entry = sql_result_store.get(m.group(1), 9)
    assert entry is not None and len(entry["rows"]) == 1


def test_attach_full_data_download_no_link_when_nothing_was_withheld():
    rows = [{"month": "2020-01", "revenue": 100}]
    answer = "### 결과\n\n표 전체 표시"

    out = _attach_full_data_download(answer, rows, user_id=9, rows_withheld=False)

    assert "/api/sql-results/" not in out
    assert out == answer


def test_attach_full_data_download_strips_promise_even_without_user_id():
    rows = [{"month": "2020-01", "revenue": 100}]
    answer = "### 결과\n\n전체 데이터가 필요하시면 말씀해주세요"

    out = _attach_full_data_download(answer, rows, user_id=None, rows_withheld=True)

    assert "필요하시면" not in out
    assert "/api/sql-results/" not in out
