# 공유드라이브 COA·MSDS 롯트 일괄 찾기 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SKU·제품명·롯트 목록을 올리면 공유드라이브에서 일치하는 COA·MSDS 파일을 찾아 링크로 보여주고, 선택한 것을 ZIP 으로 한 번에 받게 한다.

**Architecture:** 판정(`app/core/coa_finder.py`)과 조회(Drive API)를 분리한다. 판정은 순수 함수라 네트워크 없이 테스트한다. 색인을 만들지 않고 조회 시점에 사용자 본인 OAuth 로 드라이브에 직접 묻는다. LLM 은 이 경로 어디에도 쓰지 않는다 — 파일 선택은 규칙이 한다.

**Tech Stack:** FastAPI · google-api-python-client (Drive v3) · openpyxl 3.1.5 · pytest · 순수 JS(빌드 없음)

**Spec:** `docs/superpowers/specs/2026-08-28-coa-lot-finder-design.md`

## Global Constraints

- **넓혀 찾지 않는다.** 이 경로에서 `query_keywords.expand()`·`llm_variants()` 를 부르지 않는다. `E07Z083` 에 `E07Z082` 를, `416022` 에 `416006` 을 주는 것이 최악의 실패다
- **접근 주체는 사용자 본인 구글 계정** — `GoogleAuthManager.get_credentials(user_email)`. 서비스계정을 쓰지 않는다
- **검색 범위는 공유드라이브 전체** — `includeItemsFromAllDrives=True`, `supportsAllDrives=True`, `corpora="allDrives"`
- **드라이브는 읽기 전용** — 쓰기·이동·삭제 호출을 만들지 않는다
- **`ThreadPoolExecutor` 를 `with` 로 감싸지 않는다** — `pool.shutdown(wait=False)` (프로젝트 규칙: `with` 는 타임아웃을 무력화한다)
- **`print()` 에 비ASCII 를 쓰지 않는다** — 콘솔이 cp949 다. 진단은 `logger` 로
- ⛔ **`git commit -a` 를 쓰지 않는다.** 이 작업트리를 다른 세션이 함께 쓴다 — 각 커밋 단계에 적힌 파일만 `git add` 로 지정한다
- 프론트 색은 `style.css` 의 기존 토큰만 쓴다: `--bg`·`--bg-surface`·`--bg-elevated`·`--bg-input`·`--bg-hover`·`--border`·`--border-strong`·`--text`·`--text-secondary`·`--text-muted`·`--shadow-card`. 자바스크립트 인라인 스타일 금지
- 실측 고정값 (테스트 픽스처의 근거, 2026-08-27~28 드라이브 확인):
  - `E07Z083`(목록) ↔ `51082SEA-001W SKIN1004 MADAGASCAR CENTELLA POREMIZING FRESH AMPOULE COA (E07Z082)`(드라이브) — **다른 롯트**
  - `416022`(목록) ↔ `SKIN1004 Madagascar Centella Cream COA (SKMC) 416006.pdf의 사본`(드라이브) — **다른 롯트**
  - `FE161` → `COA_10116720_SCA1-MHWSCM(F)N4(A)_SKIN1004 MADAGASCAR CENTELLA HYALU-CICA WATER-FIT SUN SERUM_FE161_15643EA` — **정상 매칭**

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `app/core/coa_finder.py` | 목록 파싱 · 판정 · 드라이브 조회 오케스트레이션. **LLM 없음** |
| `app/api/coa_finder_api.py` | `/coa-finder` 페이지 · `/api/coa-finder/search`(SSE) · `/api/coa-finder/download`(ZIP) |
| `app/static/coa_finder.html` | 전용 페이지 (HTML+CSS+JS 한 파일, 기존 `dashboard.html` 과 같은 방식) |
| `tests/test_coa_finder.py` | 판정 회귀 — 네트워크 없음 |
| `tests/test_coa_finder_api.py` | 엔드포인트 — TestClient |
| `app/core/google_workspace.py` (수정) | `search_drive()` 에 공유드라이브 파라미터 |
| `app/frontend/chat.html` · `chat.js` (수정) | 사이드바 진입 버튼 + 캐시 버전 |
| `app/core/self_check.py` (수정) | 드라이브 접근 생존 검사 1종 |

---

### Task 1: 공유드라이브 접근 — 실측부터

⚠️ **이 태스크가 실패하면 나머지 계획을 멈추고 보고한다.** 사용자 본인 OAuth 로 공유드라이브가 안 보이면 접근 주체를 다시 정해야 한다 (스펙 "먼저 확인할 가정").

**Files:**
- Modify: `app/core/google_workspace.py:344-360` (`search_drive` 내부 `_run`)
- Test: `tests/test_coa_finder.py`

**Interfaces:**
- Consumes: 없음
- Produces: `search_drive(creds, query, max_results=10, mime_contains=None, exact_name=None) -> list[dict]` — `exact_name` 이 주어지면 그 문자열이 파일명에 그대로 든 것만 남긴다. 반환 dict 키는 기존과 같고 `size`·`parentId` 가 추가된다

- [ ] **Step 1: 공유드라이브가 보이는지 직접 확인하는 일회성 스크립트를 스크래치패드에 쓴다**

파일: `%TEMP%\claude\...\scratchpad\probe_shared_drive.py` (프로젝트에 남기지 않는다)

```python
import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.getcwd())
from app.core.google_auth import GoogleAuthManager
from googleapiclient.discovery import build

EMAIL = "jeffrey@skin1004korea.com"
creds = GoogleAuthManager().get_credentials(EMAIL)
assert creds is not None, "구글 미연결 — 먼저 셀라에서 구글 계정을 연결할 것"
svc = build("drive", "v3", credentials=creds, cache_discovery=False)

drives = svc.drives().list(pageSize=20, fields="drives(id,name)").execute()
print("공유드라이브:", [d["name"] for d in drives.get("drives", [])])

for corpora in (None, "allDrives"):
    params = dict(q="name contains 'E08Z011' and trashed = false",
                  pageSize=5, fields="files(id,name,size)")
    if corpora:
        params.update(corpora=corpora, includeItemsFromAllDrives=True,
                      supportsAllDrives=True)
    res = svc.files().list(**params).execute()
    print(f"corpora={corpora}:", [f["name"] for f in res.get("files", [])])
```

- [ ] **Step 2: 스크립트를 돌려 세 가지를 기록한다**

Run: `python <스크래치패드>/probe_shared_drive.py`

기록할 것: ① 공유드라이브 목록이 비어 있지 않은가 ② `corpora=None` 과 `allDrives` 의 결과 건수 차이 ③ `E08Z011` 파일이 `allDrives` 에서 잡히는가

**공유드라이브 목록이 비었거나 두 결과가 같으면 → 여기서 멈추고 보고한다.**

- [ ] **Step 3: 실패하는 테스트를 쓴다**

파일: `tests/test_coa_finder.py`

```python
"""COA·MSDS 롯트 찾기 — 판정 회귀. 네트워크를 타지 않는다."""
from unittest.mock import MagicMock, patch

from app.core.google_workspace import search_drive


def _fake_service(captured):
    svc = MagicMock()

    def _list(**params):
        captured.append(params)
        resp = MagicMock()
        resp.execute.return_value = {"files": []}
        return resp

    svc.files.return_value.list.side_effect = _list
    return svc


def test_search_drive_includes_shared_drives():
    """공유드라이브가 빠지면 COA 는 통째로 안 보인다 (2026-08-28)."""
    captured = []
    with patch("app.core.google_workspace.build",
               return_value=_fake_service(captured)):
        search_drive(MagicMock(), "E08Z011")

    assert captured, "files().list() 가 불리지 않았다"
    p = captured[0]
    assert p.get("includeItemsFromAllDrives") is True
    assert p.get("supportsAllDrives") is True
    assert p.get("corpora") == "allDrives"


def test_search_drive_exact_name_drops_near_miss():
    """E07Z083 을 물었는데 E07Z082 가 남으면 안 된다."""
    near = {"id": "1", "name": "…POREMIZING FRESH AMPOULE COA (E07Z082)",
            "mimeType": "application/pdf", "modifiedTime": "", "webViewLink": ""}
    svc = MagicMock()
    resp = MagicMock()
    resp.execute.return_value = {"files": [near]}
    svc.files.return_value.list.return_value = resp

    with patch("app.core.google_workspace.build", return_value=svc):
        got = search_drive(MagicMock(), "E07Z083", exact_name="E07Z083")

    assert got == []
```

- [ ] **Step 4: 테스트가 실패하는 것을 확인한다**

Run: `python -m pytest tests/test_coa_finder.py -v`
Expected: FAIL — `includeItemsFromAllDrives` 가 없고, `exact_name` 인자가 없어 `TypeError`

- [ ] **Step 5: `search_drive` 를 고친다**

`app/core/google_workspace.py` — 시그니처에 `exact_name` 을 더하고, `_run` 의 `params` 를 바꾼다.

```python
def search_drive(
    creds: Credentials,
    query: str,
    max_results: int = 10,
    mime_contains: Optional[str] = None,
    exact_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
```

`params` 를 만드는 자리(현행 355~357행)를 이렇게 바꾼다:

```python
        params = dict(
            q=" and ".join(clauses), pageSize=max_results,
            fields="files(id, name, mimeType, modifiedTime, webViewLink, size)",
            # ⛔ 공유드라이브를 빼면 COA·MSDS 가 통째로 안 보인다. Drive 는 기본값이
            #    'user' 라 공유드라이브 항목을 조용히 제외한다 — 에러가 아니라 0건이다
            corpora="allDrives",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
```

반환 dict 에 크기를 더한다 (현행 385~392행의 컴프리헨션). 중복본을 접을 때 쓴다:

```python
            "size": int(f.get("size") or 0),
```

`return` 직전에 정확 매칭 필터를 넣는다:

```python
    # ⛔ Drive 의 `contains` 는 토큰 앞부분 매칭이라 요청하지 않은 파일이 섞일 수 있다.
    #    롯트 조회는 **넓히면 안 되는** 경로다 — 문자열이 그대로 든 것만 남긴다
    if exact_name:
        files = [f for f in files if exact_name in f.get("name", "")]
```

- [ ] **Step 6: 테스트가 통과하는 것을 확인한다**

Run: `python -m pytest tests/test_coa_finder.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: 기존 드라이브 검색이 안 깨졌는지 본다**

Run: `python -m pytest tests/ -k "drive or gws" -v`
Expected: 기존 테스트 전부 PASS. 실패하면 그것이 내 변경 탓인지 먼저 확인한다 (작업트리를 다른 세션과 공유한다)

- [ ] **Step 8: 커밋**

```bash
git add app/core/google_workspace.py tests/test_coa_finder.py
git commit -m "feat(drive): 공유드라이브를 검색 대상에 넣고 정확 매칭 필터를 더한다"
```

---

### Task 2: 목록 파싱 — 붙여넣기와 엑셀

**Files:**
- Create: `app/core/coa_finder.py`
- Test: `tests/test_coa_finder.py` (이어서)

**Interfaces:**
- Consumes: Task 1 의 `search_drive(..., exact_name=)`
- Produces:
  - `@dataclass(frozen=True) Row(sku: str, description: str, lot: str, line_no: int)`
  - `parse_pasted(text: str) -> list[Row]`
  - `parse_xlsx(data: bytes) -> list[Row]`
  - `HeaderNotFound(Exception)` — `.found: tuple[str, ...]`, `.missing: tuple[str, ...]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_coa_finder.py` 에 이어 붙인다.

```python
import pytest
from app.core import coa_finder as cf

PASTED = """이 시트는 OP팀이 관리합니다
갱신: 2026-08-27

SKU\tDESCRIPTION\tLOT
EUSKA022\tSKIN1004 Madagascar Centella Ampoule 100ml_CPNP\tFE103C
EUSKC017\tSKIN1004 Madagascar Centella Cream 75ml_CPNP\t416022
EUSKA024\tSKIN1004 Madagascar Centella Tone Brightening Capsule Ampoule 100ml_CPNP\tF31C28 D
"""


def test_parse_pasted_finds_header_below_preamble():
    """⛔ 헤더를 몇 번째 행이라고 박지 마라 — 안내문이 한 줄 늘면 조용히 0건이 난다."""
    rows = cf.parse_pasted(PASTED)
    assert [r.sku for r in rows] == ["EUSKA022", "EUSKC017", "EUSKA024"]
    assert rows[2].lot == "F31C28 D"
    assert rows[0].line_no == 5


def test_parse_pasted_accepts_korean_headers():
    rows = cf.parse_pasted("품목\t제품명\t롯트\nEUSKA022\t앰플\tFE103C\n")
    assert rows[0].sku == "EUSKA022" and rows[0].lot == "FE103C"


def test_parse_pasted_reports_missing_column_instead_of_guessing():
    """⛔ 열을 못 찾으면 추측해서 진행하지 않는다."""
    with pytest.raises(cf.HeaderNotFound) as e:
        cf.parse_pasted("SKU\tDESCRIPTION\nEUSKA022\t앰플\n")
    assert "LOT" in e.value.missing


def test_parse_pasted_keeps_row_with_empty_lot():
    """롯트가 비어도 MSDS 는 제품명으로 찾을 수 있다 — 행을 버리지 않는다."""
    rows = cf.parse_pasted("SKU\tDESCRIPTION\tLOT\nEUSKA022\t앰플\t\n")
    assert rows[0].lot == ""
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_coa_finder.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.coa_finder`

- [ ] **Step 3: 모듈을 만든다**

파일: `app/core/coa_finder.py`

```python
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_coa_finder.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 엑셀 경로도 같은 함수를 타는지 확인하는 테스트를 더한다**

```python
def test_parse_xlsx_uses_the_same_header_logic():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["이 시트는 OP팀이 관리합니다"])
    ws.append([])
    ws.append(["SKU", "DESCRIPTION", "LOT"])
    ws.append(["EUSKA022", "앰플 100ml", "FE103C"])
    buf = io.BytesIO()
    wb.save(buf)

    rows = cf.parse_xlsx(buf.getvalue())
    assert rows[0].sku == "EUSKA022" and rows[0].lot == "FE103C"
```

테스트 파일 맨 위에 `import io` 를 더한다.

- [ ] **Step 6: 통과 확인**

Run: `python -m pytest tests/test_coa_finder.py -v`
Expected: PASS (7 passed)

- [ ] **Step 7: 커밋**

```bash
git add app/core/coa_finder.py tests/test_coa_finder.py
git commit -m "feat(coa): 목록 파싱 — 헤더 낱말로 찾고 열이 없으면 추측하지 않는다"
```

---

### Task 3: 판정 — 네 갈래와 중복본

**Files:**
- Modify: `app/core/coa_finder.py`
- Test: `tests/test_coa_finder.py` (이어서)

**Interfaces:**
- Consumes: Task 2 의 `Row`
- Produces:
  - `@dataclass(frozen=True) DriveFile(id: str, name: str, size: int, web_link: str)`
  - `@dataclass(frozen=True) Verdict(status: str, files: tuple[DriveFile, ...], note: str)`
  - `classify_lot(lot: str, files: Sequence[DriveFile]) -> Verdict`
  - 상태 문자열 상수: `FOUND="찾음"` · `MANY="여러건"` · `NONE="없음"` · `CHECK="확인필요"`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 회귀 두 개가 핵심이다**

```python
def _f(name, size=1000, fid="x"):
    return cf.DriveFile(id=fid, name=name, size=size, web_link="http://d/" + fid)


def test_near_miss_lot_is_not_a_match():
    """⛔ E07Z083 을 물었는데 E07Z082 를 주면 규제 문서가 잘못 나간다 (실측값)."""
    files = [_f("51082SEA-001W SKIN1004 MADAGASCAR CENTELLA "
                "POREMIZING FRESH AMPOULE COA (E07Z082)")]
    v = cf.classify_lot("E07Z083", files)
    assert v.status == cf.NONE
    assert v.files == ()


def test_numeric_near_miss_lot_is_not_a_match():
    """416022 를 물었는데 416006 을 주면 안 된다 (실측값)."""
    files = [_f("SKIN1004 Madagascar Centella Cream COA (SKMC) 416006.pdf의 사본")]
    assert cf.classify_lot("416022", files).status == cf.NONE


def test_lot_between_underscores_matches():
    """FE161 처럼 언더바 사이에 낀 롯트는 정상 매칭이다 (실측값)."""
    files = [_f("COA_10116720_SCA1-MHWSCM(F)N4(A)_SKIN1004 MADAGASCAR CENTELLA "
                "HYALU-CICA WATER-FIT SUN SERUM_FE161_15643EA")]
    v = cf.classify_lot("FE161", files)
    assert v.status == cf.FOUND and len(v.files) == 1


def test_suffixed_lot_without_suffix_in_filename_is_check_needed():
    """F31C28 D 를 물었는데 접미 없는 파일만 있으면 사람에게 넘긴다."""
    files = [_f("COA_SKIN1004 ... TONE BRIGHTENING CAPSULE AMPOULE 100ml(N3)_F31C28")]
    v = cf.classify_lot("F31C28 D", files)
    assert v.status == cf.CHECK
    assert "접미" in v.note


def test_suffixed_lot_with_exact_filename_is_found():
    files = [_f("COA_SKIN1004 ... PROBIO-CICA Glow SUN AMPOULE 50ml(N3)_F14D40 D")]
    assert cf.classify_lot("F14D40 D", files).status == cf.FOUND


def test_short_lot_is_check_needed():
    """4자 이하는 다른 코드의 머리에 걸릴 수 있다."""
    v = cf.classify_lot("FF21", [_f("COA_...FF21...")])
    assert v.status == cf.CHECK


def test_copies_collapse_to_one_row():
    """'의 사본' 과 원본, 같은 파일이 두 폴더에 있는 경우를 한 줄로 접는다."""
    files = [
        _f("COA (SKMC) 416006.pdf", size=82070, fid="a"),
        _f("COA (SKMC) 416006.pdf의 사본", size=82070, fid="b"),
        _f("COA (SKMC) 416006.pdf", size=82070, fid="c"),
    ]
    v = cf.classify_lot("416006", files)
    assert v.status == cf.FOUND
    assert len(v.files) == 1


def test_two_real_candidates_are_all_shown():
    files = [_f("COA_A_FE103C", size=100, fid="a"),
             _f("COA_B_FE103C", size=200, fid="b")]
    v = cf.classify_lot("FE103C", files)
    assert v.status == cf.MANY and len(v.files) == 2


def test_empty_lot_is_not_searched():
    v = cf.classify_lot("", [_f("아무거나")])
    assert v.status == cf.NONE and "롯트" in v.note
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_coa_finder.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'DriveFile'`

- [ ] **Step 3: 판정을 구현한다**

`app/core/coa_finder.py` 에 이어 붙인다.

```python
FOUND = "찾음"
MANY = "여러건"
NONE = "없음"
CHECK = "확인필요"

# 짧은 롯트는 다른 코드의 머리에 걸릴 수 있다 (Drive 는 토큰 앞부분 매칭이다)
_SHORT_LOT_LEN = 4

# 사본 표기. 원본과 같은 파일인데 이름만 다르다
_COPY_SUFFIX = re.compile(r"(의\s*사본|\s*\(\d+\)|\s*-\s*복사본)\s*$")
_EXT = re.compile(r"\.(pdf|xlsx?|docx?)$", re.IGNORECASE)


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
    base = _EXT.sub("", base).strip()
    return (base.casefold(), f.size)


def _dedup(files: Sequence[DriveFile]) -> tuple[DriveFile, ...]:
    """같은 파일의 사본을 접는다. 먼저 온 것을 남긴다."""
    seen: dict[tuple, DriveFile] = {}
    for f in files:
        seen.setdefault(_dedup_key(f), f)
    return tuple(seen.values())


def classify_lot(lot: str, files: Sequence[DriveFile]) -> Verdict:
    """롯트 하나에 대한 판정.

    ⛔ 넓혀 찾지 않는다. 롯트 문자열이 파일명에 **그대로** 든 것만 받는다 —
       E07Z083 에 E07Z082 를, 416022 에 416006 을 주는 것이 최악의 실패다.
    """
    lot = (lot or "").strip()
    if not lot:
        return Verdict(NONE, (), "롯트가 비어 있습니다")

    exact = _dedup([f for f in files if lot in f.name])
    if exact:
        if len(exact) > 1:
            return Verdict(MANY, exact, f"{len(exact)}건 — 어느 것인지 확인이 필요합니다")
        if len(lot) <= _SHORT_LOT_LEN:
            return Verdict(CHECK, exact,
                           f"롯트가 {len(lot)}자로 짧아 다른 코드에 걸렸을 수 있습니다")
        return Verdict(FOUND, exact)

    # 접미가 붙은 롯트("F31C28 D")인데 접미 없는 파일만 있는 경우
    head = lot.split()[0]
    if head != lot:
        base_hits = _dedup([f for f in files if head in f.name])
        if base_hits:
            return Verdict(
                CHECK, base_hits,
                f"접미 없이 '{head}' 로만 된 파일입니다 — 같은 롯트인지 확인하세요")

    return Verdict(NONE, (), "")
```

⚠️ `Sequence` 는 Task 2 에서 이미 import 했다. `re` 도 마찬가지다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_coa_finder.py -v`
Expected: PASS (16 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/core/coa_finder.py tests/test_coa_finder.py
git commit -m "feat(coa): 판정 네 갈래 — 넓히지 않는다는 것을 회귀로 못 박는다"
```

---

### Task 4: 드라이브 조회 연결 — COA 는 롯트로, MSDS 는 제품명으로

**Files:**
- Modify: `app/core/coa_finder.py`
- Test: `tests/test_coa_finder.py` (이어서)

**Interfaces:**
- Consumes: Task 1 `search_drive`, Task 3 `classify_lot`/`Verdict`/`DriveFile`
- Produces:
  - `@dataclass(frozen=True) Result(row: Row, coa: Verdict, msds: Verdict)`
  - `product_terms(description: str) -> list[str]`
  - `find_all(creds, rows, search=None, max_workers=8) -> Iterator[Result]` — `search` 는 테스트에서 갈아끼우는 조회 함수. 진행 순서는 `rows` 순서를 지킨다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_product_terms_drops_brand_words():
    """브랜드어만 남으면 MSDS 검색이 전 제품을 긁는다."""
    terms = cf.product_terms("SKIN1004 Madagascar Centella Poremizing Fresh Ampoule 50ml")
    assert "Poremizing" in terms and "Fresh" in terms
    assert not any(t.lower() in ("skin1004", "madagascar", "centella") for t in terms)


def test_product_terms_strips_cpnp_marker():
    assert "CPNP" not in cf.product_terms("SKIN1004 Madagascar Centella Ampoule 100ml_CPNP")


def test_find_all_never_widens_the_query():
    """⛔ expand()/llm_variants() 를 부르면 이 기능은 오답 생성기가 된다."""
    rows = [cf.Row("EUSKA032", "SKIN1004 Madagascar Centella Poremizing Fresh Ampoule 50ml",
                   "E07Z083", 1)]
    calls = []

    def fake_search(creds, query, max_results=10, mime_contains=None, exact_name=None):
        calls.append((query, exact_name))
        return [{"id": "1", "name": "...POREMIZING FRESH AMPOULE COA (E07Z082)",
                 "size": 100, "webViewLink": "http://d/1"}]

    with patch("app.core.query_keywords.expand") as ex, \
         patch("app.core.query_keywords.llm_variants") as lv:
        results = list(cf.find_all(MagicMock(), rows, search=fake_search))

    ex.assert_not_called()
    lv.assert_not_called()
    assert results[0].coa.status == cf.NONE
    assert all(c[1] for c in calls if c[0] == "E07Z083"), "exact_name 을 넘기지 않았다"


def test_find_all_keeps_row_order():
    rows = [cf.Row(f"S{i}", "앰플", f"LOT{i:04d}", i) for i in range(5)]

    def fake_search(*a, **k):
        return []

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert [r.row.sku for r in got] == [f"S{i}" for i in range(5)]


def test_find_all_marks_query_failure_without_killing_the_run():
    rows = [cf.Row("A", "앰플", "LOT1", 1), cf.Row("B", "앰플", "LOT2", 2)]

    def fake_search(creds, query, **k):
        if query == "LOT1":
            raise RuntimeError("drive down")
        return []

    got = list(cf.find_all(MagicMock(), rows, search=fake_search))
    assert got[0].coa.status == cf.CHECK and "조회" in got[0].coa.note
    assert got[1].coa.status == cf.NONE
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_coa_finder.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'product_terms'`

- [ ] **Step 3: 구현한다**

`app/core/coa_finder.py` 에 이어 붙인다.

```python
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
            raw = search(creds, " ".join(terms[:4] + ["MSDS"]), max_results=25)
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
```

⚠️ `Iterable` 은 Task 2 의 typing import 에 이미 있다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_coa_finder.py -v`
Expected: PASS (21 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/core/coa_finder.py tests/test_coa_finder.py
git commit -m "feat(coa): 드라이브 조회 연결 — COA 는 롯트, MSDS 는 제품명"
```

---

### Task 5: 검색 API — 진행률을 흘린다

**Files:**
- Create: `app/api/coa_finder_api.py`
- Modify: `app/main.py:320` 부근 (라우터 등록)
- Test: `tests/test_coa_finder_api.py`

**Interfaces:**
- Consumes: Task 2·4 의 `parse_pasted`/`parse_xlsx`/`find_all`
- Produces:
  - `GET /coa-finder` → `app/static/coa_finder.html`
  - `POST /api/coa-finder/search` (multipart: `pasted` 또는 `file`) → SSE. 이벤트 `row`(결과 1행) · `done`(요약) · `error`
  - `coa_finder_router` (모듈 전역, `main.py` 가 import 한다)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

파일: `tests/test_coa_finder_api.py`

```python
"""COA 찾기 엔드포인트."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.auth_middleware import get_current_user


class _User:
    email = "tester@skin1004korea.com"
    role = "user"


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: _User()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_search_requires_google_connection(client):
    """⛔ 미연결을 '전부 없음' 으로 보여주면 안 된다."""
    with patch("app.api.coa_finder_api._credentials", return_value=None):
        r = client.post("/api/coa-finder/search",
                        data={"pasted": "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\n"})
    assert r.status_code == 409
    assert "구글" in r.json()["detail"]


def test_search_streams_one_event_per_row(client):
    payload = "SKU\tDESCRIPTION\tLOT\nA\t앰플\tFE103C\nB\t크림\t416022\n"
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.core.coa_finder.search_drive", return_value=[]):
        r = client.post("/api/coa-finder/search", data={"pasted": payload})
    assert r.status_code == 200
    body = r.text
    assert body.count("event: row") == 2
    assert "event: done" in body


def test_search_reports_missing_header_column(client):
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()):
        r = client.post("/api/coa-finder/search",
                        data={"pasted": "SKU\tDESCRIPTION\nA\t앰플\n"})
    assert r.status_code == 400
    assert "LOT" in r.json()["detail"]


def test_page_is_served(client):
    r = client.get("/coa-finder")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_coa_finder_api.py -v`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: 엔드포인트를 만든다**

파일: `app/api/coa_finder_api.py`

```python
"""공유드라이브 COA·MSDS 찾기 — 페이지와 조회 API.

⛔ 서비스계정을 쓰지 않는다. 사용자 본인 OAuth 로만 읽어 드라이브 권한을 그대로 따른다.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.api.auth_middleware import get_current_user
from app.core import coa_finder as cf
from app.core.google_auth import GoogleAuthManager

logger = logging.getLogger(__name__)
router = APIRouter()

_auth = GoogleAuthManager()
_MAX_ROWS = 500


def _credentials(email: str):
    return _auth.get_credentials(email)


@router.get("/coa-finder")
async def coa_finder_page(_: object = Depends(get_current_user)):
    return FileResponse("app/static/coa_finder.html", media_type="text/html")


@router.post("/api/coa-finder/search")
async def coa_finder_search(
    pasted: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    user: object = Depends(get_current_user),
):
    if file is not None:
        rows_source = ("xlsx", await file.read())
    elif pasted:
        rows_source = ("pasted", pasted)
    else:
        raise HTTPException(400, "목록을 붙여넣거나 엑셀 파일을 올려주세요")

    try:
        rows = (cf.parse_xlsx(rows_source[1]) if rows_source[0] == "xlsx"
                else cf.parse_pasted(rows_source[1]))
    except cf.HeaderNotFound as exc:
        raise HTTPException(400, str(exc)) from exc

    if not rows:
        raise HTTPException(400, "행을 찾지 못했습니다 — 헤더 아래에 데이터가 있는지 확인하세요")
    if len(rows) > _MAX_ROWS:
        raise HTTPException(
            400, f"{len(rows)}행입니다. 한 번에 {_MAX_ROWS}행까지 처리합니다")

    creds = _credentials(user.email)
    if creds is None:
        # ⛔ 미연결을 '전부 없음' 으로 보여주면 조용한 오답이 된다
        raise HTTPException(409, "구글 계정이 연결되어 있지 않습니다. 먼저 연결해주세요")

    def _verdict(v: cf.Verdict) -> dict:
        return {
            "status": v.status,
            "note": v.note,
            "files": [{"id": f.id, "name": f.name, "link": f.web_link, "size": f.size}
                      for f in v.files],
        }

    def _stream():
        counts = {cf.FOUND: 0, cf.MANY: 0, cf.NONE: 0, cf.CHECK: 0}
        total = len(rows)
        for i, res in enumerate(cf.find_all(creds, rows), start=1):
            counts[res.coa.status] = counts.get(res.coa.status, 0) + 1
            payload = {
                "index": i, "total": total,
                "sku": res.row.sku, "description": res.row.description,
                "lot": res.row.lot,
                "coa": _verdict(res.coa), "msds": _verdict(res.msds),
            }
            yield f"event: row\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        summary = {"total": total, "counts": counts}
        yield f"event: done\ndata: {json.dumps(summary, ensure_ascii=False)}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})
```

- [ ] **Step 4: `main.py` 에 라우터를 등록한다**

`app/main.py` 의 import 구역에 더한다:

```python
from app.api.coa_finder_api import router as coa_finder_router
```

`app.include_router(face_search_router)` 다음 줄에 더한다:

```python
    app.include_router(coa_finder_router)     # /coa-finder, /api/coa-finder/*
```

- [ ] **Step 5: 빈 페이지 파일을 만들어 라우트가 200 을 주게 한다**

Task 7 에서 내용을 채운다. 지금은 뼈대만.

```bash
printf '<!doctype html><title>COA 찾기</title><p>준비 중</p>' > app/static/coa_finder.html
```

- [ ] **Step 6: 통과를 확인한다**

Run: `python -m pytest tests/test_coa_finder_api.py -v`
Expected: PASS (4 passed)

- [ ] **Step 7: 커밋**

```bash
git add app/api/coa_finder_api.py app/main.py app/static/coa_finder.html tests/test_coa_finder_api.py
git commit -m "feat(coa): 검색 엔드포인트 — 진행률을 SSE 로 흘린다"
```

---

### Task 6: ZIP 다운로드

**Files:**
- Modify: `app/api/coa_finder_api.py`
- Test: `tests/test_coa_finder_api.py` (이어서)

**Interfaces:**
- Consumes: Task 5 의 `router`·`_credentials`
- Produces: `POST /api/coa-finder/download` — body `{"items": [{"file_id","sku","lot","name"}]}` → `application/zip`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_download_rejects_too_many_items(client):
    items = [{"file_id": str(i), "sku": "A", "lot": "L", "name": "x.pdf"}
             for i in range(201)]
    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()):
        r = client.post("/api/coa-finder/download", json={"items": items})
    assert r.status_code == 400
    assert "200" in r.json()["detail"]


def test_download_records_files_it_could_not_fetch(client):
    """⛔ 조용히 빠지면 아무도 모른다 — 못 받은 목록을 ZIP 안에 남긴다."""
    import io as _io
    import zipfile

    items = [{"file_id": "ok", "sku": "A", "lot": "FE103C", "name": "a.pdf"},
             {"file_id": "bad", "sku": "B", "lot": "416022", "name": "b.pdf"}]

    def fake_fetch(creds, file_id):
        if file_id == "bad":
            raise RuntimeError("403")
        return b"%PDF-1.4 fake"

    with patch("app.api.coa_finder_api._credentials", return_value=MagicMock()), \
         patch("app.api.coa_finder_api._fetch_file", side_effect=fake_fetch):
        r = client.post("/api/coa-finder/download", json={"items": items})

    assert r.status_code == 200
    zf = zipfile.ZipFile(_io.BytesIO(r.content))
    names = zf.namelist()
    assert "A_FE103C_a.pdf" in names
    assert "_받지못한_목록.txt" in names
    assert "416022" in zf.read("_받지못한_목록.txt").decode("utf-8")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_coa_finder_api.py -v`
Expected: FAIL — 404

- [ ] **Step 3: 구현한다**

`app/api/coa_finder_api.py` 에 이어 붙인다.

```python
import io
import re
import zipfile

from pydantic import BaseModel

_MAX_DOWNLOAD_ITEMS = 200
_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n]+')


class DownloadItem(BaseModel):
    file_id: str
    sku: str = ""
    lot: str = ""
    name: str = ""


class DownloadRequest(BaseModel):
    items: list[DownloadItem]


def _fetch_file(creds, file_id: str) -> bytes:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req, chunksize=1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def _zip_name(item: DownloadItem) -> str:
    """어느 롯트 것인지 열어보지 않아도 알게 한다."""
    parts = [p for p in (item.sku, item.lot, item.name or item.file_id) if p]
    return _UNSAFE.sub("_", "_".join(parts))[:180]


@router.post("/api/coa-finder/download")
async def coa_finder_download(
    payload: DownloadRequest,
    user: object = Depends(get_current_user),
):
    items = payload.items
    if not items:
        raise HTTPException(400, "받을 파일을 선택해주세요")
    if len(items) > _MAX_DOWNLOAD_ITEMS:
        raise HTTPException(
            400,
            f"{len(items)}건입니다. 한 번에 {_MAX_DOWNLOAD_ITEMS}건까지 받을 수 있습니다 "
            f"({len(items) - _MAX_DOWNLOAD_ITEMS}건 초과)")

    creds = _credentials(user.email)
    if creds is None:
        raise HTTPException(409, "구글 계정이 연결되어 있지 않습니다. 먼저 연결해주세요")

    buf = io.BytesIO()
    failed: list[str] = []
    total = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for item in items:
            try:
                data = _fetch_file(creds, item.file_id)
            except Exception as exc:                  # noqa: BLE001
                logger.warning("coa_download_failed",
                               extra={"file_id": item.file_id, "error": str(exc)[:200]})
                failed.append(f"{item.sku} · {item.lot} · {item.name}: {str(exc)[:120]}")
                continue
            total += len(data)
            if total > _MAX_DOWNLOAD_BYTES:
                failed.append(f"{item.sku} · {item.lot} · {item.name}: 총 용량 상한 초과")
                break
            name = _zip_name(item)
            n, stem = 1, name
            while name in used:
                name, n = f"{stem}({n})", n + 1
            used.add(name)
            zf.writestr(name, data)

        if failed:
            # ⛔ 조용히 빠지면 아무도 모른다
            zf.writestr("_받지못한_목록.txt",
                        "받지 못한 파일\n\n" + "\n".join(failed))

    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="coa_msds.zip"'})
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_coa_finder_api.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/api/coa_finder_api.py tests/test_coa_finder_api.py
git commit -m "feat(coa): ZIP 다운로드 — 못 받은 파일을 목록으로 남긴다"
```

---

### Task 7: 전용 페이지

**Files:**
- Modify: `app/static/coa_finder.html` (내용을 채운다)
- Modify: `app/frontend/chat.html:66-72` (사이드바 버튼) · `app/frontend/chat.js:1186` 부근 (핸들러)
- Modify: `app/frontend/chat.html:457` (`chat.js?v=270` → `271`)
- Modify: `CLAUDE.md` 의 "캐시 버전" 줄
- Test: `tests/test_no_silent_failures.py` (이어서)

**Interfaces:**
- Consumes: Task 5·6 의 `/api/coa-finder/search`·`/api/coa-finder/download`
- Produces: 없음 (최종 화면)

- [ ] **Step 1: 페이지를 쓴다**

`app/static/coa_finder.html` 을 통째로 바꾼다. `style.css` 를 그대로 쓰고, **정의된 토큰만** 참조한다.

```html
<!doctype html>
<html lang="ko" class="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>인증서류 찾기 — 셀라</title>
  <link rel="stylesheet" href="/static/style.css?v=192">
  <style>
    .cf-wrap { max-width: 1200px; margin: 0 auto; padding: 24px; color: var(--text); }
    .cf-input { width: 100%; min-height: 140px; background: var(--bg-input);
                border: 1px solid var(--border); border-radius: 8px;
                color: var(--text); padding: 12px; font-family: inherit; }
    .cf-bar { display: flex; gap: 12px; align-items: center; margin: 16px 0; }
    .cf-summary { color: var(--text-secondary); font-variant-numeric: tabular-nums; }
    table.cf { width: 100%; border-collapse: collapse; }
    table.cf th, table.cf td { border-bottom: 1px solid var(--border);
                               padding: 8px 10px; text-align: left; vertical-align: top; }
    table.cf th { color: var(--text-muted); font-weight: 600; }
    .cf-scroll { overflow-x: auto; }
    .st-found { color: var(--text); }
    .st-none { color: var(--text-muted); }
    .st-check, .st-many { color: var(--text); font-weight: 600; }
    .cf-note { color: var(--text-muted); font-size: 0.85em; }
  </style>
</head>
<body>
  <div class="cf-wrap">
    <h1>인증서류 찾기 (COA · MSDS)</h1>
    <p class="cf-summary">엑셀에서 SKU · DESCRIPTION · LOT 열을 복사해 붙여넣거나
      .xlsx 파일을 올리세요. 공유드라이브에서 롯트가 정확히 일치하는 파일만 찾습니다.</p>

    <textarea class="cf-input" id="cf-paste"
              placeholder="SKU&#9;DESCRIPTION&#9;LOT"></textarea>
    <div class="cf-bar">
      <input type="file" id="cf-file" accept=".xlsx">
      <button id="cf-run">찾기</button>
      <button id="cf-download" disabled>선택한 파일 받기</button>
      <span class="cf-summary" id="cf-progress"></span>
    </div>
    <div id="cf-error" class="cf-note"></div>
    <div class="cf-scroll">
      <table class="cf" id="cf-table" hidden>
        <thead><tr>
          <th><input type="checkbox" id="cf-all"></th>
          <th>SKU</th><th>제품명</th><th>롯트</th><th>COA</th><th>MSDS</th>
        </tr></thead>
        <tbody id="cf-body"></tbody>
      </table>
    </div>
  </div>
  <script src="/static/coa_finder.js?v=1"></script>
</body>
</html>
```

- [ ] **Step 2: 페이지 스크립트를 쓴다**

파일: `app/static/coa_finder.js`

```javascript
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STATUS_CLASS = { "찾음": "st-found", "없음": "st-none",
                         "여러건": "st-many", "확인필요": "st-check" };

  function cell(v, sku, lot) {
    const wrap = document.createElement("td");
    const label = document.createElement("div");
    label.className = STATUS_CLASS[v.status] || "";
    label.textContent = v.status;
    wrap.appendChild(label);
    (v.files || []).forEach((f) => {
      const a = document.createElement("a");
      a.href = f.link; a.target = "_blank"; a.rel = "noopener";
      a.textContent = f.name;
      a.dataset.fileId = f.id; a.dataset.sku = sku; a.dataset.lot = lot;
      a.className = "cf-file";
      const line = document.createElement("div");
      line.appendChild(a);
      wrap.appendChild(line);
    });
    if (v.note) {
      const n = document.createElement("div");
      n.className = "cf-note"; n.textContent = v.note;
      wrap.appendChild(n);
    }
    return wrap;
  }

  function addRow(d) {
    const tr = document.createElement("tr");
    const pick = document.createElement("td");
    const box = document.createElement("input");
    box.type = "checkbox"; box.className = "cf-pick";
    box.checked = d.coa.status === "찾음";
    pick.appendChild(box);
    tr.appendChild(pick);
    [d.sku, d.description, d.lot].forEach((t) => {
      const td = document.createElement("td");
      td.textContent = t || "";
      tr.appendChild(td);
    });
    tr.appendChild(cell(d.coa, d.sku, d.lot));
    tr.appendChild(cell(d.msds, d.sku, d.lot));
    $("cf-body").appendChild(tr);
  }

  // SSE 는 POST 를 못 쓴다 — fetch 로 받아 줄 단위로 읽는다
  async function run() {
    $("cf-body").innerHTML = "";
    $("cf-error").textContent = "";
    $("cf-table").hidden = false;
    $("cf-download").disabled = true;

    const fd = new FormData();
    const file = $("cf-file").files[0];
    if (file) fd.append("file", file);
    else fd.append("pasted", $("cf-paste").value);

    const res = await fetch("/api/coa-finder/search", { method: "POST", body: fd });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: "요청에 실패했습니다" }));
      $("cf-error").textContent = body.detail || "요청에 실패했습니다";
      $("cf-table").hidden = true;
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop();
      chunks.forEach((chunk) => {
        const ev = /^event:\s*(\w+)/m.exec(chunk);
        const dm = /^data:\s*(.+)$/m.exec(chunk);
        if (!ev || !dm) return;
        const data = JSON.parse(dm[1]);
        if (ev[1] === "row") {
          addRow(data);
          $("cf-progress").textContent = `${data.index} / ${data.total}`;
        } else if (ev[1] === "done") {
          const c = data.counts || {};
          $("cf-progress").textContent =
            `${data.total}건 중 COA 찾음 ${c["찾음"] || 0} · ` +
            `여러건 ${c["여러건"] || 0} · 확인필요 ${c["확인필요"] || 0} · ` +
            `없음 ${c["없음"] || 0}`;
          $("cf-download").disabled = false;
        }
      });
    }
  }

  async function download() {
    const items = [];
    document.querySelectorAll("#cf-body tr").forEach((tr) => {
      if (!tr.querySelector(".cf-pick").checked) return;
      const a = tr.querySelector("a.cf-file");
      if (!a) return;
      items.push({ file_id: a.dataset.fileId, sku: a.dataset.sku,
                   lot: a.dataset.lot, name: a.textContent });
    });
    if (!items.length) { $("cf-error").textContent = "받을 파일을 선택해주세요"; return; }

    const res = await fetch("/api/coa-finder/download", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: "받기에 실패했습니다" }));
      $("cf-error").textContent = body.detail || "받기에 실패했습니다";
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "coa_msds.zip";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("cf-run").addEventListener("click", run);
    $("cf-download").addEventListener("click", download);
    $("cf-all").addEventListener("change", (e) => {
      document.querySelectorAll(".cf-pick").forEach((b) => { b.checked = e.target.checked; });
    });
  });
})();
```

- [ ] **Step 3: 문법을 검사한다**

Run: `node --check app/static/coa_finder.js`
Expected: 출력 없음 (통과). ⛔ 문법이 깨지면 서버는 200 을 주고 화면만 죽는다

- [ ] **Step 4: 사이드바 진입 버튼을 더한다**

`app/frontend/chat.html` — Dashboard 버튼 블록(66~72행) 바로 아래에 넣는다:

```html
      <!-- 인증서류 찾기 -->
      <div class="sidebar-dashboard sidebar-bottom-btn">
        <button class="sidebar-dashboard-btn" id="btn-coa-finder">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 15l2 2 4-4"/></svg>
          <span>인증서류 찾기</span>
        </button>
      </div>
```

`app/frontend/chat.js` — 1186행의 dashboard 핸들러 바로 아래에 넣는다:

```javascript
    document.getElementById("btn-coa-finder").addEventListener("click", () => {
      window.open("/coa-finder", "_blank", "noopener");
    });
```

- [ ] **Step 5: 캐시 버전을 올린다**

`app/frontend/chat.html:457` — `chat.js?v=270` → `chat.js?v=271`

`CLAUDE.md` 의 캐시 버전 줄을 같은 값으로 고친다:

```
- 현재: style.css?v=192, personal-briefing.js?v=25, chat.js?v=271 (2026-08-28 기준 — 올릴 때 이 줄도 같이 갱신할 것)
```

- [ ] **Step 6: 조용한 실패 검사를 더한다**

`tests/test_no_silent_failures.py` 에 붙인다. 기존 CSS 변수 검사 헬퍼의 이름을 먼저 확인하고 그것을 쓴다 (`grep -n "def test_.*css_var\|undefined_css" tests/test_no_silent_failures.py`).

```python
def test_coa_finder_page_uses_only_defined_css_tokens():
    """없는 변수는 에러가 아니라 폴백이다 — 라이트 모드에서 안 보이는 그 결함."""
    from pathlib import Path
    import re

    css = Path("app/static/style.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
    html = Path("app/static/coa_finder.html").read_text(encoding="utf-8")
    used = set(re.findall(r"var\((--[a-z0-9-]+)", html))
    assert used <= defined, f"style.css 에 없는 토큰: {sorted(used - defined)}"


def test_coa_finder_script_parses():
    """템플릿 스크립트가 깨지면 서버는 200 을 주고 화면만 죽는다."""
    import shutil, subprocess
    node = shutil.which("node")
    if not node:
        return  # 서버에는 node 가 없다 — 개발 환경 전용 검사
    r = subprocess.run([node, "--check", "app/static/coa_finder.js"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
```

- [ ] **Step 7: 검사를 돌린다**

Run: `python -m pytest tests/test_no_silent_failures.py -v`
Expected: 새 검사 2개 PASS. 캐시 버전 검사(`test_cache_version_doc_matches_reality`)도 PASS — 실패하면 5단계에서 `CLAUDE.md` 를 안 고친 것이다

- [ ] **Step 8: 실제 화면으로 확인한다**

Run: 로컬 앱을 띄우고 `http://localhost:3001/coa-finder` 를 연다 (`pm2 restart skin1004-dev`)

확인할 것: ① 목록 3행을 붙여넣고 `찾기` 를 눌렀을 때 진행 숫자가 올라가는가 ② 라이트/다크 양쪽에서 글자가 보이는가 ③ 사이드바 버튼이 새 탭을 여는가

- [ ] **Step 9: 커밋**

```bash
git add app/static/coa_finder.html app/static/coa_finder.js app/frontend/chat.html app/frontend/chat.js CLAUDE.md tests/test_no_silent_failures.py
git commit -m "feat(coa): 전용 페이지와 사이드바 진입"
```

---

### Task 8: 자가 점검 — 토큰이 죽으면 "전부 없음" 으로 보인다

**Files:**
- Modify: `app/core/self_check.py`
- Test: `tests/test_coa_finder.py` (이어서)

**Interfaces:**
- Consumes: Task 1 의 `search_drive`
- Produces: `CHECKS` 에 `drive_shared_access` 항목 추가

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`CHECKS` 는 dict 가 아니라 `Check` dataclass 리스트다 (`app/core/self_check.py:199`, `915`).

```python
def test_self_check_has_drive_access_probe():
    """토큰이 만료되면 이 기능은 에러가 아니라 '전부 없음' 으로 보인다."""
    from app.core.self_check import CHECKS
    assert "drive_shared_access" in {c.id for c in CHECKS}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_coa_finder.py::test_self_check_has_drive_access_probe -v`
Expected: FAIL — `assert 'drive_shared_access' in {...}`

- [ ] **Step 3: 검사 함수를 더한다**

`app/core/self_check.py` — `_check_jandi_relay` (882행) 다음에 넣는다. 반환은 `CheckResult(ok, detail)` 이다.

판정은 **결과가 있느냐가 아니라 예외 없이 응답이 왔느냐** 로 한다. 검색어에 맞는 파일이 없는 것은 고장이 아니다.

```python
def _check_drive_shared_access() -> CheckResult:
    """사용자 OAuth 로 공유드라이브가 보이는가 (인증서류 찾기의 전제).

    ⚠️ 토큰이 죽어도 화면은 에러가 아니라 **'전부 없음'** 으로 보인다 — 조용한 실패다.
       그래서 "몇 건 나왔나"가 아니라 "응답이 왔나"로 판정한다.
    """
    from app.core.google_auth import GoogleAuthManager
    from app.core.google_workspace import search_drive

    email = "jeffrey@skin1004korea.com"
    creds = GoogleAuthManager().get_credentials(email)
    if creds is None:
        return CheckResult(False, f"{email} 구글 미연결 — 인증서류 찾기가 동작하지 않는다")
    try:
        files = search_drive(creds, "COA", max_results=1)
    except Exception as exc:                          # noqa: BLE001
        return CheckResult(False, f"드라이브 조회 실패: {str(exc)[:200]}")
    return CheckResult(True, f"공유드라이브 조회 정상 (표본 {len(files)}건)")
```

- [ ] **Step 4: `CHECKS` 에 등록한다**

`CHECKS` 목록의 `datasource` 항목들 옆에 같은 형식으로 넣는다:

```python
    Check("drive_shared_access", "datasource", SEV_WARNING,
          "공유드라이브 조회가 살아 있는가 (인증서류 찾기)", _check_drive_shared_access),
```

- [ ] **Step 5: 통과를 확인한다**

Run: `python -m pytest tests/test_coa_finder.py -v`
Expected: PASS (22 passed)

- [ ] **Step 6: 검사 개수 줄을 갱신한다**

Run: `python -c "from app.core.self_check import CHECKS; print(len(CHECKS))"`

`CLAUDE.md` 의 "검사 39종" 줄을 실제 값으로 고친다. ⛔ 손으로 세지 말고 위 출력을 쓴다 — 그 줄은 이미 두 번 낡았다.

- [ ] **Step 7: 전체 테스트를 돌린다**

Run: `python -m pytest tests/ -q`
Expected: 새 실패 없음. 실패가 있으면 **내 변경 탓인지 먼저 확인한다** — 이 작업트리를 다른 세션이 함께 쓴다

- [ ] **Step 8: 커밋**

```bash
git add app/core/self_check.py tests/test_coa_finder.py CLAUDE.md
git commit -m "feat(coa): 드라이브 접근 생존 검사 — 토큰이 죽으면 '전부 없음' 으로 보인다"
```

---

## 배포

```bash
./sshenv/Scripts/python scripts/deploy_new_server.py was
```

배포 전 확인:
- [ ] `python -c "from scripts.deploy_new_server import collect; print([p for p in collect() if 'coa_finder' in str(p)])"` — 새 파일 4개가 전송 목록에 드는지. `EXCLUDE_DIRS` 가 이름으로 걸러 패키지가 통째로 빠진 적이 있다
- [ ] 배포 후 `curl -s -o /dev/null -w "%{http_code}" http://10.1.100.5/coa-finder` — 302(로그인) 또는 200
- [ ] `journalctl -u ai-craver -n 50 --no-pager` 에 import 에러가 없는지

⚠️ 새 패키지를 추가하지 않았으므로 휠 재업로드는 필요 없다 (`openpyxl` 은 이미 설치돼 있다).

---

## 자체 검토 결과

**스펙 대비 빠진 것** — 없음. 스펙의 각 절이 태스크로 간다:
판정(Task 3) · 화면(Task 7) · 다운로드(Task 6) · 오류처리(Task 5·6) · 테스트(Task 3·7·8) ·
공유드라이브(Task 1) · MSDS 분리(Task 4) · 먼저 확인할 가정(Task 1 Step 1~2)

**범위 밖으로 남긴 것** (스펙과 같다): PDF 본문 검색 · 파일명 색인 · 채팅 `@@` 소스 · 드라이브 쓰기

**한 가지 남는 위험**: Task 1 Step 2 가 실패하면(사용자 OAuth 로 공유드라이브가 안 보이면)
Task 2 이후가 전부 무의미하다. 그래서 실측을 첫 단계에 뒀다 — 거기서 멈추고 보고한다.
