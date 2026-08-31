"""전체 SQL 결과 임시 저장소 — 표에서 잘린 나머지를 CSV 로 받는 통로.

"인도네시아의 2020~2026 월별 매출" 질문이 146행 중 15행만 보이고 나머지를 받을
방법이 없었다 (2026-08-31). 이 저장소는 그 나머지를 잠시 들고 있다가 소유자에게만
CSV 로 내준다 — 판정은 `get()` 한 곳에서만 한다 (보고서 열람과 같은 사상).
"""
from __future__ import annotations

from app.core import sql_result_store as store


def _rows(n: int) -> list[dict]:
    return [{"country": f"국가{i}", "revenue": i * 1000} for i in range(n)]


def test_owner_can_read_back_the_saved_result():
    token = store.save(1, ["country", "revenue"], _rows(3))

    entry = store.get(token, 1)

    assert entry is not None
    assert len(entry["rows"]) == 3
    assert entry["columns"] == ["country", "revenue"]


def test_a_different_user_cannot_fetch_the_result():
    token = store.save(1, ["country", "revenue"], _rows(3))

    assert store.get(token, 2) is None


def test_unknown_token_returns_none():
    assert store.get("does-not-exist", 1) is None


def test_csv_contains_every_row_not_a_truncated_subset():
    rows = _rows(250)
    body = store.to_csv_bytes(["country", "revenue"], rows, {"country": "국가", "revenue": "매출"})

    text = body.decode("utf-8-sig")
    # 헤더 1줄 + 데이터 250줄 (끝 개행으로 생기는 빈 줄 제외)
    lines = [ln for ln in text.splitlines() if ln]
    assert len(lines) == 251
    assert "국가0" in text and "국가249" in text


def test_csv_has_utf8_bom_so_korean_excel_does_not_mangle_hangul():
    body = store.to_csv_bytes(["country"], [{"country": "베트남"}], {"country": "국가"})

    assert body.startswith(b"\xef\xbb\xbf")
    # Excel이 BOM을 보고 읽는 것과 같은 방식으로 왕복 디코드해도 한글이 그대로다
    assert "베트남" in body.decode("utf-8-sig")
    assert "국가" in body.decode("utf-8-sig")


def test_decimal_and_none_cells_survive_the_round_trip():
    from decimal import Decimal

    rows = [{"revenue": Decimal("123.45"), "note": None}]
    body = store.to_csv_bytes(["revenue", "note"], rows, {"revenue": "매출", "note": "비고"})

    text = body.decode("utf-8-sig")
    assert "123.45" in text
