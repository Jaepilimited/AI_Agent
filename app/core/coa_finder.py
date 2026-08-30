"""공유드라이브에서 롯트로 COA·MSDS 를 찾는다.

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


FOUND = "찾음"
MANY = "여러건"
NONE = "없음"
CHECK = "확인필요"

# 짧은 롯트는 다른 코드의 머리에 걸릴 수 있다 (Drive 는 토큰 앞부분 매칭이다)
_SHORT_LOT_LEN = 4

# 사본 표기. 원본과 같은 파일인데 이름만 다르다
_COPY_SUFFIX = re.compile(r"(의\s*사본|\s*\(\d+\)|\s*-\s*복사본)\s*$")


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    size: int
    web_link: str


@dataclass(frozen=True)
class Verdict:
    status: str
    files: tuple[DriveFile, ...]
    note: str = ""


def _dedup_key(f: DriveFile) -> tuple:
    base = _COPY_SUFFIX.sub("", f.name).strip()
    return (base.casefold(), f.size)


def _dedup(files: Sequence[DriveFile]) -> tuple[DriveFile, ...]:
    """같은 파일의 사본을 접는다. 먼저 온 것을 남긴다."""
    seen: dict[tuple, DriveFile] = {}
    for f in files:
        seen.setdefault(_dedup_key(f), f)
    return tuple(seen.values())


def _is_delimited_match(name: str, lot: str) -> bool:
    """lot 앞뒤가 영숫자와 붙어 있지 않은 매칭이 하나라도 있는가.

    ⛔ 경계를 안 보면 FE161 이 FE1615 에도 걸린다 — 다른 롯트의 증명서를
       주는 것은 못 찾는 것보다 나쁘다.
    """
    start = 0
    while True:
        idx = name.find(lot, start)
        if idx == -1:
            return False
        before_ok = idx == 0 or not name[idx - 1].isalnum()
        after = idx + len(lot)
        after_ok = after == len(name) or not name[after].isalnum()
        if before_ok and after_ok:
            return True
        start = idx + 1


def classify_lot(lot: str, files: Sequence[DriveFile]) -> Verdict:
    """롯트 하나에 대한 판정.

    ⛔ 넓혀 찾지 않는다. 롯트 문자열이 파일명에 **그대로** 든 것만 받는다 —
       E07Z083 에 E07Z082 를, 416022 에 416006 을 주는 것이 최악의 실패다.
    """
    lot = (lot or "").strip()
    if not lot:
        return Verdict(NONE, (), "롯트가 비어 있습니다")

    candidates = [f for f in files if lot in f.name]
    if candidates:
        delimited = [f for f in candidates if _is_delimited_match(f.name, lot)]
        if delimited:
            exact = _dedup(delimited)
            if len(exact) > 1:
                return Verdict(MANY, exact, f"{len(exact)}건 — 어느 것인지 확인이 필요합니다")
            if len(lot) <= _SHORT_LOT_LEN:
                return Verdict(CHECK, exact,
                               f"롯트가 {len(lot)}자로 짧아 다른 코드에 걸렸을 수 있습니다")
            return Verdict(FOUND, exact)

        # 앞뒤가 다른 글자와 붙어 있는 매칭뿐이다 — 더 긴 롯트의 일부일 수 있으니
        # 없다고 단정하지 않고 사람에게 넘긴다
        exact = _dedup(candidates)
        return Verdict(
            CHECK, exact,
            "롯트 앞뒤가 다른 글자와 붙어 있습니다 — 더 긴 롯트의 일부일 수 있습니다")

    # 접미가 붙은 롯트("F31C28 D")인데 접미 없는 파일만 있는 경우
    head = lot.split()[0]
    if head != lot:
        base_hits = _dedup([f for f in files if head in f.name])
        if base_hits:
            return Verdict(
                CHECK, base_hits,
                f"접미 없이 '{head}' 로만 된 파일입니다 — 같은 롯트인지 확인하세요")

    return Verdict(NONE, (), "")


import concurrent.futures
import logging

from app.core.google_workspace import search_drive

logger = logging.getLogger(__name__)

# 모든 제품에 공통으로 들어가 변별력이 없는 낱말
_BRAND_WORDS = {"skin1004", "madagascar", "centella", "cpnp", "twin", "pack"}
_TERM_SPLIT = re.compile(r"[\s_/,()]+")


@dataclass(frozen=True)
class Result:
    row: Row
    coa: Verdict
    msds: Verdict


def product_terms(description: str) -> list[str]:
    """MSDS 검색에 쓸 제품 고유어. 브랜드어를 빼지 않으면 전 제품이 걸린다."""
    out = []
    for token in _TERM_SPLIT.split(description or ""):
        t = token.strip()
        if len(t) < 2 or t.casefold() in _BRAND_WORDS:
            continue
        out.append(t)
    return out


def _to_files(raw: Iterable[dict]) -> list[DriveFile]:
    return [
        DriveFile(id=r.get("id", ""), name=r.get("name", ""),
                  size=int(r.get("size") or 0), web_link=r.get("webViewLink", ""))
        for r in raw
    ]


def _one(creds, row: Row, search) -> Result:
    # COA — 롯트 정확 매칭. exact_name 으로 Drive 의 토큰 매칭 잡음을 걷어낸다
    if row.lot:
        try:
            raw = search(creds, row.lot, max_results=25, exact_name=row.lot)
            coa = classify_lot(row.lot, _to_files(raw))
        except Exception as exc:                      # noqa: BLE001
            # ⚠️ 실패를 삼키지 마라 — 프로덕션은 INFO 를 버린다
            logger.warning("coa_finder_query_failed",
                           extra={"lot": row.lot, "error": str(exc)[:200]})
            coa = Verdict(CHECK, (), "조회에 실패했습니다 — 다시 시도하세요")
    else:
        coa = Verdict(NONE, (), "롯트가 비어 있습니다")

    # MSDS — 롯트가 없다. 제품 고유어 + 'MSDS' 로 찾는다
    terms = product_terms(row.description)
    if terms:
        try:
            raw = search(creds, " ".join(terms[:4] + ["MSDS"]), max_results=25,
                         widen=False)
            files = _dedup(_to_files(raw))
            if not files:
                msds = Verdict(NONE, (), "")
            elif len(files) == 1:
                msds = Verdict(FOUND, files, "제품명으로 찾았습니다 (롯트 무관)")
            else:
                msds = Verdict(MANY, files, "제품명으로 찾았습니다 (롯트 무관)")
        except Exception as exc:                      # noqa: BLE001
            logger.warning("msds_finder_query_failed",
                           extra={"sku": row.sku, "error": str(exc)[:200]})
            msds = Verdict(CHECK, (), "조회에 실패했습니다 — 다시 시도하세요")
    else:
        msds = Verdict(NONE, (), "제품명이 비어 있습니다")

    return Result(row=row, coa=coa, msds=msds)


def find_all(creds, rows: Sequence[Row], search=None, max_workers: int = 8):
    """행 순서를 지키면서 병렬로 조회한다.

    ⛔ `with ThreadPoolExecutor` 로 감싸지 마라 — 블록을 나갈 때 shutdown(wait=True)
       가 걸려 타임아웃이 무의미해진다 (프로젝트 규칙).
    """
    search = search or search_drive
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = [pool.submit(_one, creds, row, search) for row in rows]
        for fut in futures:                  # 제출 순서 = 행 순서
            yield fut.result()
    finally:
        pool.shutdown(wait=False)
