"""채팅 표 행 상한 — 2026-08-31 인도네시아 매출 사고.

"인도네시아의 2020~2026 월별 매출"이 146행을 돌려받고도 15행만 보였고,
어느 15행이 남을지는 SQL의 ORDER BY에 달려 있어 "2025년부터 데이터가 있다"는
잘못된 결론으로 이어졌다. 표는 잘려도 되지만(끝없이 길면 그 나름대로 문제다),
① 실제 다년 월별 리포트 같은 크기는 통째로 보여야 하고 ② 그래도 잘리면
각주가 반드시 남아야 한다. 잘린 나머지를 다운로드로 받는 것은 별도 커밋이다.
"""
from __future__ import annotations

from app.agents.sql_agent import _fast_table_markdown


def _rows(n: int) -> list[dict]:
    return [
        {"country": "인도네시아", "month": f"2020-{(i % 12) + 1:02d}", "revenue": (i + 1) * 1_000_000}
        for i in range(n)
    ]


def test_146_row_result_renders_in_full_under_the_new_limit():
    """실제 사고 규모(146행)는 잘리지 않아야 한다."""
    table = _fast_table_markdown(_rows(146))

    data_lines = [ln for ln in table.splitlines() if ln.startswith("| ")]
    # 헤더 1줄 + 데이터 146줄 이상 (구분선 "|:---|..." 은 "| " 로 시작하지 않아 제외된다)
    assert len(data_lines) >= 146
    assert "상위" not in table  # 잘렸다는 각주가 없어야 한다


def test_result_past_the_limit_is_still_truncated_and_footnoted():
    """제한을 넘는 결과는 여전히 잘려야 하고, 잘렸다는 사실이 반드시 남아야 한다."""
    rows = _rows(400)
    table = _fast_table_markdown(rows)

    assert f"전체 {len(rows)}행 중 상위" in table
