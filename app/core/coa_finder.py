"""公有드라이브에서 롯트로 COA·MSDS 를 찾는다.

⛔ 이 모듈은 LLM 을 부르지 않는다. 파일 선택은 규칙이 한다 — 성분 조회를 LLM 에
   맡기지 않은 것과 같은 이유다. 규제 문서에서 틀린 파일을 주는 것은
   못 찾는 것보다 나쁘다.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

# 헤더 낱말. ⛔ "몇 번째 행" 으로 박지 마라 — 안내문이 늘면 조용히 0건이 난다
_HEADER_ALIASES = {
    "sku": ("sku", "품목", "품목코드", "제품코드"),
    "description": ("description", "제품명", "품목명", "품명", "desc"),
    "lot": ("lot", "롯트", "로트", "lot no", "lot.no", "제조번호"),
}


@dataclass(frozen=True)
class Row:
    sku: str
    description: str
    lot: str
    line_no: int


class HeaderNotFound(Exception):
    def __init__(self, found: Sequence[str], missing: Sequence[str]) -> None:
        self.found = tuple(found)
        self.missing = tuple(missing)
        super().__init__(
            f"헤더를 찾지 못했습니다. 찾은 열: {', '.join(found) or '없음'} / "
            f"없는 열: {', '.join(missing)}"
        )


def _match_header(cells: Sequence[str]) -> dict:
    """이 행에서 알아본 {필드: 열번호}. 아무것도 못 알아보면 빈 dict."""
    idx: dict[str, int] = {}
    for col, raw in enumerate(cells):
        key = (raw or "").strip().casefold().replace("_", " ")
        for field, aliases in _HEADER_ALIASES.items():
            if field not in idx and key in aliases:
                idx[field] = col
    return idx


def _cell(cells: Sequence[str], col: Optional[int]) -> str:
    if col is None or col >= len(cells):
        return ""
    return (cells[col] or "").strip()


def _rows_from_cells(table: Iterable[Sequence[str]]) -> list[Row]:
    """헤더 행을 찾고 그 아래를 읽는다.

    ⛔ 헤더를 몇 번째 행이라고 박지 마라. 머리말 안내문이 한 줄만 늘어도 어긋나고,
       그때 나는 것은 에러가 아니라 **0건**이다 (OP 재고 적재에서 겪은 함정).
    """
    header: Optional[dict] = None
    best_partial: dict = {}          # 무엇을 못 찾았는지 알려주기 위해 기억한다
    rows: list[Row] = []

    for line_no, cells in enumerate(table, start=1):
        if header is None:
            hit = _match_header(cells)
            if "sku" in hit and "lot" in hit:
                header = hit
            elif len(hit) > len(best_partial):
                best_partial = hit
            continue

        sku = _cell(cells, header["sku"])
        if not sku:
            continue
        rows.append(Row(
            sku=sku,
            description=_cell(cells, header.get("description")),
            lot=_cell(cells, header["lot"]),
            line_no=line_no,
        ))

    if header is None:
        found = [f.upper() for f in sorted(best_partial)]
        missing = [f for f in ("SKU", "DESCRIPTION", "LOT") if f not in found]
        raise HeaderNotFound(found=found, missing=missing)
    return rows


def parse_pasted(text: str) -> list[Row]:
    """엑셀에서 복사한 탭 구분 텍스트."""
    table = [line.split("\t") for line in (text or "").splitlines()]
    return _rows_from_cells(table)


def parse_xlsx(data: bytes) -> list[Row]:
    """.xlsx 업로드. 첫 시트만 읽는다."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    table = [
        ["" if c is None else str(c) for c in row]
        for row in ws.iter_rows(values_only=True)
    ]
    return _rows_from_cells(table)
