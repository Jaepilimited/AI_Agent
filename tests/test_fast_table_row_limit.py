"""채팅 표 행 제한 — 2026-08-31 인도네시아 매출 사고.

"인도네시아의 2020~2026 월별 매출"이 146행을 돌려받고도 15행만 보였고,
어느 15행이 남을지는 SQL의 ORDER BY에 달려 있어 "2025년부터 데이터가 있다"는
잘못된 결론으로 이어졌다. 표는 잘려도 되지만(붐따가 될 정도로 길면 곤란하다),
① 실제 다년 월별 리포트 같은 크기는 통째로 보여야 하고 ② 그래도 잘리면 각주가
있어야 하고 ③ 잘린 나머지는 CSV로 받을 수 있어야 한다.
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
    # 헤더 1줄 + 구분선(| :--- |...) 은 "| " 로 시작하지 않으므로 데이터만 잡힌다
    # (구분선은 "|" 로 시작해 공백 없이 이어짐)
    assert len(data_lines) >= 146
    assert "상위" not in table  # 잘렸다는 각주가 없어야 한다


def test_result_past_the_limit_is_still_truncated_and_footnoted():
    """제한을 넘는 결과는 여전히 잘려야 하고, 잘렸다는 사실이 반드시 남아야 한다."""
    rows = _rows(400)
    table = _fast_table_markdown(rows)

    assert f"전체 {len(rows)}행 중 상위" in table


def test_truncated_table_offers_a_csv_download_link_for_the_owner():
    from app.core import sql_result_store

    rows = _rows(400)
    table = _fast_table_markdown(rows, user_id=7)

    assert "/api/sql-results/" in table
    assert "csv" in table.lower()

    # 링크의 토큰이 실제로 전체 400행을 들고 있어야 한다 — 표만 그럴듯하고
    # 링크는 빈 껍데기면 안 된다.
    import re
    m = re.search(r"/api/sql-results/([^)\s]+)/csv", table)
    assert m, table
    entry = sql_result_store.get(m.group(1), 7)
    assert entry is not None
    assert len(entry["rows"]) == 400


def test_without_a_user_id_no_download_link_is_offered():
    """사용자 컨텍스트가 없는 호출(과거 테스트 등)은 조용히 링크를 생략한다."""
    table = _fast_table_markdown(_rows(400))

    assert "/api/sql-results/" not in table
