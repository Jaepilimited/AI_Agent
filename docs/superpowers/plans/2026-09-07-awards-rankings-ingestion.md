# 수상/랭킹 적재 Implementation Plan (1단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수상/랭킹 구글 시트를 매일 MariaDB 로 적재하고 `@@수상` 및 일반 질문에서 **조회로** 답한다.

**Architecture:** OP 재고(`app/core/inventory.py`)와 같은 계열이다 — 시트 → 표 적재 → SQL 조회. 벡터를 쓰지 않는 이유는 스펙 §2에 있다(랭킹이 숫자고, 마케팅 활용 가능 여부가 법적 판단이다). 신규 모듈 `app/core/awards.py` 하나에 파싱·적재·조회·의도판정을 모으고, 라우팅은 기존 `inventory` 경로와 같은 모양으로 붙인다.

**Tech Stack:** Python 3.12 · google-api-python-client(시트 읽기, 서비스계정) · PyMySQL/MariaDB · APScheduler · pytest

**Spec:** `docs/superpowers/specs/2026-09-07-pr-awards-ingestion-design.md`

## Global Constraints

- **읽는 탭은 `수상및랭킹` 하나뿐.** `ALLOWED_TABS` 에 없으면 **왜 막혔는지 적어서** 거절한다(`KeyError` 에 기대지 않는다). 다른 탭 이름을 소스에 두지도 않는다.
- **0행은 성공이 아니라 실패다.** 기존 데이터를 지우지 않고 잡을 실패로 기록한다.
- **`synced_at` 비교 전 `microsecond=0`.** MariaDB DATETIME 은 초 단위다.
- **`가능 여부` 를 문장으로 해석하지 않는다.** 기호 원문 + 조건을 그대로 보여준다. "사용 가능합니다" 로 단정하는 문장을 코드가 만들지 않는다.
- **`랭킹/점수` 를 정수로 강제하지 않는다.** `97%`·`-` 가 섞여 있다. 원문 `rank_raw` 를 남기고 깔끔한 정수만 `rank_value` 에 넣는다.
- 시트: `1oxtCpeubAuo2e_uXkKcy0oY3Nb-iGQ-KMW3ImWc4-FE` · 탭 `수상및랭킹` · 헤더는 **찾아서** 쓴다(현재 6번째 행).
- ⚠️ **bash 힙독으로 코드를 치환하지 마라** — 백슬래시를 한 겹 먹어 `\uD83C` 같은 이스케이프가 실제 서로게이트 문자가 되고, `open(...,"w")` 는 쓰기 실패 전에 파일을 이미 비운다. 이 계획을 쓰는 중에 실제로 파일이 0바이트가 됐다. 편집은 Write/Edit 도구로 한다.
- 콘솔이 cp949 다 — 스크립트에서 한글을 `print` 하려면 `sys.stdout` 을 UTF-8 로 감싼다.
- 작업트리를 다른 세션과 공유한다 — **`git add <경로>`** 로 내가 만진 파일만 커밋한다.

---

### Task 1: 파싱 — 헤더 탐색 · 중복 헤더 · 랭킹 값

시트 구조를 해석하는 순수 함수만 만든다. 네트워크·DB 없음.

**Files:**
- Create: `app/core/awards.py`
- Test: `tests/test_awards.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `SHEET_ID: str`, `SHEET_TAB: str`, `ALLOWED_TABS: frozenset[str]`, `SHEET_URL: str`
  - `find_header_row(values: list[list[str]]) -> int`
  - `column_index(header: list[str]) -> dict[str, int]`
  - `parse_rank(raw: str) -> int | None`
  - `parse_rows(values: list[list[str]]) -> list[dict]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_awards.py
# -*- coding: utf-8 -*-
"""수상/랭킹 시트 파싱 — 조용히 틀릴 자리를 고정한다."""
from datetime import datetime

import pytest

from app.core.awards import (ALLOWED_TABS, SHEET_TAB, column_index,
                             find_header_row, parse_rank, parse_rows)

HEADER = ["", "구분", "브랜드", "주최사", "수상명 / 타이틀", "시작일", "종료일",
          "수상/랭킹 일자", "획득 국가", "제품/브랜드", "상세 내용", "랭킹/점수",
          "유/무료", "금액", "가능 여부", "시작일", " 종료일", "지역", "소스"]
SHEET = [
    [], ["", "스킨1004 수상 및 랭킹 등의 정보 취합"], [],
    ["", "※ 아래 경로에 캡처 이미지 업로드 해주세요"], ["", "", "", "", "", "기간"],
    HEADER,
    ["", "랭킹", "좀비뷰티", "화해", "2021 화해 뷰티 어워드", "2020-11-01", "2021-10-31",
     "2021-11-24", "대한민국", "누에고치 모공팩", "클렌징 비누 부문 1위", "1",
     "무료", "-", "O", "무기한", "무기한", "국내", "https://ex/1"],
]


def test_finds_the_header_row_instead_of_hardcoding_it():
    """안내문이 한 줄 늘어도 찾아야 한다 — 어긋나면 에러가 아니라 0건이다."""
    assert find_header_row(SHEET) == 5
    assert find_header_row([[]] + SHEET) == 6


def test_raises_when_there_is_no_header_row():
    with pytest.raises(ValueError):
        find_header_row([["아무", "관계없는", "행"]])


def test_duplicate_headers_are_split_by_position_not_name():
    """시작일/종료일이 두 쌍이다 — 수상 기간과 마케팅 활용 기간.
    이름으로 찾으면 두 기간이 조용히 섞인다."""
    ci = column_index(HEADER)
    assert ci["award_start"] == 5 and ci["award_end"] == 6
    assert ci["usage_start"] == 15 and ci["usage_end"] == 16
    assert ci["award_start"] != ci["usage_start"]


def test_trailing_space_in_a_header_does_not_break_lookup():
    """실제 시트의 두 번째 종료일은 ' 종료일' 로 앞에 공백이 있다."""
    assert column_index(HEADER)["usage_end"] == 16


@pytest.mark.parametrize("raw,expected", [
    ("1", 1), ("10", 10), (" 2 ", 2),
    ("97%", None), ("-", None), ("", None), ("TOP10", None),
])
def test_rank_is_parsed_only_when_it_is_a_clean_integer(raw, expected):
    """97% 와 - 를 정수로 강제하면 그 행이 조용히 사라지거나 거짓 순위가 된다."""
    assert parse_rank(raw) == expected


def test_rows_keep_the_raw_rank_even_when_it_cannot_be_parsed():
    rows = parse_rows(SHEET + [
        ["", "설문", "스킨1004", "PICKY", "만족도", "", "", "", "대한민국", "센텔라 앰플",
         "재구매 의사", "97%", "유료", "1,000,000", "△", "", "", "", ""]])
    assert rows[-1]["rank_raw"] == "97%"
    assert rows[-1]["rank_value"] is None


def test_blank_rows_are_dropped_but_rows_without_a_rank_are_kept():
    rows = parse_rows(SHEET + [["", "", "", "", "", "", ""], []])
    assert len(rows) == 1


def test_only_the_named_tab_is_allowed():
    assert ALLOWED_TABS == frozenset({SHEET_TAB})
    assert SHEET_TAB == "수상및랭킹"


def test_hidden_tab_names_are_not_present_in_the_source():
    """탭 이름을 코드에 두면 다음 사람이 '읽어도 되나 보다' 한다."""
    src = open("app/core/awards.py", encoding="utf-8").read()
    for hidden in ("매출,손익", "실적공유용", "대표제품 지역별 판매량", "미중일 매출비중"):
        assert hidden not in src
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_awards.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.awards'`

- [ ] **Step 3: 최소 구현**

```python
# app/core/awards.py
# -*- coding: utf-8 -*-
"""수상/랭킹 — 시트를 MariaDB 로 적재하고 **조회로** 답한다.

왜 벡터가 아니라 표인가 (2026-09-07 결정, 스펙 §2):
    핵심이 숫자(랭킹 1위 63건)와 판정(마케팅 활용 O/△/X)이다. 임베딩은 `1` 과 `10`
    을 구분하지 못하고(OP 재고와 같은 근거), 못 쓰는 수상을 "쓸 수 있다" 고 답하면
    실제 문제가 된다. 초상권을 LLM 없는 경로로 뺀 것과 같은 계열이다.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog

from app.db.mariadb import execute, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

SHEET_ID = "1oxtCpeubAuo2e_uXkKcy0oY3Nb-iGQ-KMW3ImWc4-FE"
SHEET_TAB = "수상및랭킹"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=2037853201"
MAX_ROWS = 3000
_RANGE = f"A1:U{MAX_ROWS}"

# ⛔ **지정한 탭만 읽는다** (2026-09-07 사용자 지시: "내가 말한 탭만 학습해").
#    이 시트에는 숨김 탭이 여러 개 있고 그중에 매출·손익 자료가 있다.
#    다른 탭 이름을 여기 적지도 않는다 — 회귀가 그것까지 검사한다.
ALLOWED_TABS = frozenset({SHEET_TAB})

#: 시트 헤더 → 우리 컬럼. **위치로** 가른다 (같은 이름이 두 번 나온다).
_HEADER_ORDER = [
    ("구분", "category"), ("브랜드", "brand"), ("주최사", "organizer"),
    ("수상명 / 타이틀", "title"), ("시작일", "award_start"), ("종료일", "award_end"),
    ("수상/랭킹 일자", "award_date"), ("획득 국가", "country"),
    ("제품/브랜드", "product"), ("상세 내용", "detail"), ("랭킹/점수", "rank_raw"),
    ("유/무료", "paid"), ("금액", "amount_raw"), ("가능 여부", "usage_flag"),
    ("시작일", "usage_start"), ("종료일", "usage_end"), ("지역", "usage_region"),
    ("소스", "source_url"),
]


def find_header_row(values: List[List[Any]]) -> int:
    """`구분` 이 있는 행을 찾는다.

    ⛔ 숫자로 박지 마라 — 머리말이 한 줄만 늘어도 어긋나고, 그때 나는 것은
       에러가 아니라 **0건**이다 (OP 재고에서 겪은 그대로).
    """
    for i, row in enumerate(values[:20]):
        if any(str(c).strip() == "구분" for c in row):
            return i
    raise ValueError("헤더 행을 찾지 못했다 — '구분' 열이 있어야 한다")


def column_index(header: List[Any]) -> Dict[str, int]:
    """헤더 → {우리이름: 열 위치}.

    ⚠️ `시작일`·`종료일` 이 **두 쌍**이다 (수상 기간 / 마케팅 활용 기간). 게다가
       두 번째 종료일은 `' 종료일'` 로 앞에 공백이 있다. 이름으로 찾으면 두 기간이
       조용히 섞이므로 **나온 순서대로** 소비한다.
    """
    cells = [str(c).strip() for c in header]
    out: Dict[str, int] = {}
    cursor = 0
    for label, key in _HEADER_ORDER:
        for i in range(cursor, len(cells)):
            if cells[i] == label:
                out[key] = i
                cursor = i + 1
                break
    return out


_INT_ONLY = re.compile(r"^\d+$")


def parse_rank(raw: Any) -> Optional[int]:
    """깔끔한 정수일 때만 순위로 본다.

    ⛔ `97%`·`-`·`TOP10` 을 정수로 강제하면 그 행이 사라지거나 거짓 순위가 된다.
       성분에서 '미상' 을 '미포함' 으로 쓴 오답과 같은 함정이다.
    """
    s = str(raw or "").strip()
    return int(s) if _INT_ONLY.match(s) else None


def parse_rows(values: List[List[Any]]) -> List[Dict[str, Any]]:
    hdr = find_header_row(values)
    ci = column_index(values[hdr])

    def cell(row: List[Any], key: str) -> str:
        i = ci.get(key)
        return str(row[i]).strip() if i is not None and i < len(row) else ""

    rows: List[Dict[str, Any]] = []
    for row in values[hdr + 1:]:
        rec = {k: cell(row, k) for _, k in _HEADER_ORDER}
        # 완전히 빈 행만 버린다. 순위가 없다고 버리면 '설문' 60건이 통째로 사라진다.
        if not any(rec.values()):
            continue
        rec["rank_value"] = parse_rank(rec["rank_raw"])
        rows.append(rec)
    return rows
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_awards.py -q`
Expected: PASS (13 passed — parametrize 7건 포함)

- [ ] **Step 5: 커밋**

```bash
git add app/core/awards.py tests/test_awards.py
git commit -m "feat(awards): 수상/랭킹 시트 파싱 — 헤더는 찾고, 중복 헤더는 위치로 가른다"
```

---

### Task 2: 적재 — 테이블과 동기화

**Files:**
- Modify: `app/core/awards.py`
- Test: `tests/test_awards.py`

**Interfaces:**
- Consumes: Task 1 의 `parse_rows`, `ALLOWED_TABS`, `SHEET_TAB`
- Produces:
  - `ensure_awards_table() -> None`
  - `_fetch(svc, tab: str) -> list[list[Any]]` (탭 화이트리스트 강제)
  - `_read_sheet() -> list[list[Any]]`
  - `sync_awards(dry_run: bool = False) -> dict` — 키: `rows`, `written`, `empty`, `cleanup_refused`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_awards.py 에 이어서
from unittest.mock import MagicMock

from app.core import awards


def test_fetch_refuses_a_tab_that_is_not_allowed_and_says_why():
    with pytest.raises(ValueError) as e:
        awards._fetch(MagicMock(), "다른탭")
    msg = str(e.value)
    assert "다른탭" in msg and "허용" in msg


def test_sync_does_not_wipe_existing_data_when_the_sheet_reads_empty(monkeypatch):
    """0행은 권한 만료·탭 이름 변경으로 온다. 성공으로 적으면 영영 못 잡는다."""
    monkeypatch.setattr(awards, "ensure_awards_table", lambda: None)
    monkeypatch.setattr(awards, "_read_sheet", lambda: [])
    calls = []
    monkeypatch.setattr(awards, "execute", lambda *a, **k: calls.append(a))
    stat = awards.sync_awards()
    assert stat["rows"] == 0 and stat["empty"] is True
    assert calls == []


def test_sync_drops_microseconds_before_comparing(monkeypatch):
    """마이크로초가 남으면 방금 넣은 행이 전부 'synced_at < now' 에 걸려 지워진다."""
    monkeypatch.setattr(awards, "ensure_awards_table", lambda: None)
    monkeypatch.setattr(awards, "_read_sheet", lambda: SHEET)
    seen = {}
    monkeypatch.setattr(awards, "execute",
                        lambda sql, params=None: seen.setdefault("p", params))
    monkeypatch.setattr(awards, "fetch_one", lambda *a, **k: {"n": 0})
    awards.sync_awards()
    stamps = [v for v in seen["p"] if isinstance(v, datetime)]
    assert stamps and all(s.microsecond == 0 for s in stamps)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_awards.py -q`
Expected: FAIL — `AttributeError: module 'app.core.awards' has no attribute '_fetch'`

- [ ] **Step 3: 구현**

```python
# app/core/awards.py 에 이어서

_DDL = """
CREATE TABLE IF NOT EXISTS awards_rankings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    category VARCHAR(40) NOT NULL DEFAULT '',
    brand VARCHAR(40) NOT NULL DEFAULT '',
    organizer VARCHAR(80) NOT NULL DEFAULT '',
    title VARCHAR(255) NOT NULL DEFAULT '',
    award_start VARCHAR(20) NOT NULL DEFAULT '',
    award_end VARCHAR(20) NOT NULL DEFAULT '',
    award_date VARCHAR(20) NOT NULL DEFAULT '',
    country VARCHAR(60) NOT NULL DEFAULT '',
    product VARCHAR(255) NOT NULL DEFAULT '',
    detail TEXT,
    rank_raw VARCHAR(40) NOT NULL DEFAULT '',
    rank_value INT NULL,
    paid VARCHAR(20) NOT NULL DEFAULT '',
    amount_raw VARCHAR(60) NOT NULL DEFAULT '',
    usage_flag VARCHAR(20) NOT NULL DEFAULT '',
    usage_start VARCHAR(20) NOT NULL DEFAULT '',
    usage_end VARCHAR(20) NOT NULL DEFAULT '',
    usage_region VARCHAR(80) NOT NULL DEFAULT '',
    source_url VARCHAR(500) NOT NULL DEFAULT '',
    row_key VARCHAR(180) NOT NULL,
    synced_at DATETIME NOT NULL,
    UNIQUE KEY uq_row (row_key),
    INDEX idx_rank (rank_value),
    INDEX idx_brand (brand)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_FIELDS = [k for _, k in _HEADER_ORDER] + ["rank_value"]


def ensure_awards_table() -> None:
    try:
        execute(_DDL)
    except Exception as e:
        logger.warning("awards_ddl_failed", error=str(e)[:160])


def _sheets_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _fetch(svc, tab: str) -> List[List[Any]]:
    # ⛔ 허용 탭만 읽는다. 범위 상수의 KeyError 에 기대지 않는다 — 왜 막혔는지가 보여야 한다.
    if tab not in ALLOWED_TABS:
        raise ValueError(
            f"허용되지 않은 시트 탭: {tab!r}. 지정한 탭만 학습한다 "
            f"(허용: {sorted(ALLOWED_TABS)})")
    from app.core.retrying import with_retry
    return with_retry(
        lambda: (svc.spreadsheets().values()
                 .get(spreadsheetId=SHEET_ID, range=f"'{tab}'!{_RANGE}")
                 .execute().get("values", [])),
        what="awards_sheet:" + tab, attempts=2, first_delay=1.0,
    )


def _read_sheet() -> List[List[Any]]:
    return _fetch(_sheets_service(), SHEET_TAB)


def _row_key(rec: Dict[str, Any]) -> str:
    """같은 행을 다시 넣을 때 겹치게 하는 키. 180자를 넘지 않게 자른다."""
    raw = "|".join((rec["brand"], rec["organizer"], rec["title"],
                    rec["product"], rec["award_date"] or rec["award_start"],
                    rec["rank_raw"]))
    return raw[:180]


def sync_awards(dry_run: bool = False) -> Dict[str, Any]:
    """시트 → `awards_rankings`. 매일 1회 (`awards_sync_daily`)."""
    ensure_awards_table()
    values = _read_sheet()
    rows = parse_rows(values) if values else []
    stat: Dict[str, Any] = {"rows": len(rows), "written": 0,
                            "empty": not rows, "dry_run": dry_run}
    # ⛔ 0행이면 아무것도 지우지 않는다 — 권한 만료·탭 이름 변경이 정확히 이렇게 온다.
    if not rows:
        logger.warning("awards_empty_sheet", tab=SHEET_TAB)
        return stat
    if dry_run:
        return stat

    # ⛔ 마이크로초를 버린다 (MariaDB DATETIME 은 초 단위 — OP 재고에서 겪은 함정).
    now = datetime.now().replace(microsecond=0)
    cols = _FIELDS + ["row_key", "synced_at"]
    placeholder = "(" + ",".join(["%s"] * len(cols)) + ")"
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        sql = ("INSERT INTO awards_rankings (" + ",".join(cols) + ") VALUES "
               + ",".join([placeholder] * len(chunk))
               + " ON DUPLICATE KEY UPDATE "
               + ",".join(f"{c}=VALUES({c})" for c in _FIELDS + ["synced_at"]))
        params: list = []
        for r in chunk:
            params += [r[c] for c in _FIELDS] + [_row_key(r), now]
        execute(sql, tuple(params))
        stat["written"] += len(chunk)

    # 시트에서 사라진 행 정리 — ⛔ 정리 대상이 적재분 이상이면 지우지 않는다.
    try:
        stale = fetch_one("SELECT COUNT(*) n FROM awards_rankings WHERE synced_at < %s",
                          (now,)) or {}
        n_stale = int(stale.get("n") or 0)
        if n_stale >= stat["written"]:
            logger.error("awards_cleanup_refused", stale=n_stale, written=stat["written"])
            stat["cleanup_refused"] = n_stale
        elif n_stale:
            execute("DELETE FROM awards_rankings WHERE synced_at < %s", (now,))
            stat["deleted"] = n_stale
    except Exception as e:
        logger.warning("awards_cleanup_failed", error=str(e)[:160])
    return stat
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_awards.py -q`
Expected: PASS (16 passed)

- [ ] **Step 5: 실제 시트로 dry-run**

Run:
```bash
python -c "import io,sys; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); from app.core.awards import sync_awards; print(sync_awards(dry_run=True))"
```
Expected: `rows` 가 200 근처(2026-09-07 실측 206) · `empty` 는 `False`

⚠️ 로컬에 서비스계정 자격증명이 없으면 WAS 에서 앱과 같은 환경으로 돌린다:
```bash
set -a; . /home/jeffrey/AI_Agent/.env; set +a
export HTTPS_PROXY=http://10.1.50.2:3128 HTTP_PROXY=$HTTPS_PROXY NO_PROXY=localhost,127.0.0.1,10.1.0.0/16
```

- [ ] **Step 6: 커밋**

```bash
git add app/core/awards.py tests/test_awards.py
git commit -m "feat(awards): 시트를 표로 적재한다 — 0행이면 지우지 않고 실패로 남긴다"
```

---

### Task 3: 조회 — 그리고 활용 가능 여부를 단정하지 않는다

**Files:**
- Modify: `app/core/awards.py`
- Test: `tests/test_awards.py`

**Interfaces:**
- Consumes: Task 2 의 테이블
- Produces:
  - `USAGE_LEGEND: dict[str, str]`
  - `search(term: str = "", limit: int = 40) -> dict` — 키: `rows`, `total`, `synced_at`
  - `format_answer(result: dict) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_awards.py 에 이어서

ROW = {"category": "랭킹", "brand": "좀비뷰티", "organizer": "화해",
       "title": "2021 화해 뷰티 어워드", "award_date": "2021-11-24",
       "award_start": "2020-11-01", "country": "대한민국",
       "product": "누에고치 모공팩", "detail": "클렌징 비누 부문 1위",
       "rank_raw": "1", "rank_value": 1, "paid": "무료", "amount_raw": "-",
       "usage_flag": "O", "usage_start": "무기한", "usage_end": "무기한",
       "usage_region": "국내", "source_url": ""}


def test_the_answer_never_asserts_that_an_award_may_be_used():
    """법적 판단이다. 못 쓰는 수상을 '쓸 수 있다' 고 답하면 실제 문제가 된다."""
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "2026-09-07"})
    for banned in ("사용 가능합니다", "사용하실 수 있습니다", "활용 가능합니다", "써도 됩니다"):
        assert banned not in text


def test_the_answer_shows_the_raw_usage_symbol_and_its_legend():
    text = awards.format_answer({"rows": [ROW], "total": 1, "synced_at": "-"})
    assert "O" in text and awards.USAGE_LEGEND["O"] in text
    assert "담당자 확인" in text


def test_a_conditional_usage_note_is_carried_into_the_answer():
    row = dict(ROW, usage_flag="△", usage_region="국문 엠블럼만, 화해 검수 필요")
    text = awards.format_answer({"rows": [row], "total": 1, "synced_at": "-"})
    assert "화해 검수 필요" in text


def test_unparsed_rank_is_shown_as_raw_not_dropped():
    row = dict(ROW, rank_raw="97%", rank_value=None)
    text = awards.format_answer({"rows": [row], "total": 1, "synced_at": "-"})
    assert "97%" in text


def test_answer_says_how_many_were_omitted_when_the_list_is_cut():
    text = awards.format_answer({"rows": [ROW], "total": 25, "synced_at": "-"})
    assert "25" in text


def test_empty_result_says_so_instead_of_returning_an_empty_table():
    assert "찾지 못했" in awards.format_answer({"rows": [], "total": 0, "synced_at": "-"})
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_awards.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'format_answer'`

- [ ] **Step 3: 구현**

```python
# app/core/awards.py 에 이어서

#: ⛔ 기호를 문장으로 바꾸지 않는다 — **뜻풀이**만 붙인다. 판단은 사람이 한다.
USAGE_LEGEND = {
    "O": "사용 승인 표기",
    "△": "조건부 표기 — 조건을 확인해야 한다",
    "X": "사용 불가 표기",
    "논의중": "논의중 표기",
    "": "표기 없음",
}

_SEARCH_COLS = ("title", "product", "organizer", "country", "detail", "brand", "category")


def search(term: str = "", limit: int = 40) -> Dict[str, Any]:
    """낱말을 AND 로 건다 — 제품명이 길어 통째로 LIKE 하면 0건이다."""
    words = [w for w in re.split(r"\s+", (term or "").strip()) if len(w) >= 2]
    where, params = "1=1", []
    for w in words[:5]:
        where += " AND (" + " OR ".join(f"{c} LIKE %s" for c in _SEARCH_COLS) + ")"
        params += [f"%{w}%"] * len(_SEARCH_COLS)
    total = int((fetch_one(f"SELECT COUNT(*) n FROM awards_rankings WHERE {where}",
                           tuple(params)) or {}).get("n") or 0)
    rows = fetch_all(
        f"SELECT * FROM awards_rankings WHERE {where} "
        "ORDER BY (rank_value IS NULL), rank_value ASC, award_date DESC LIMIT %s",
        tuple(params) + (limit,))
    stamp = (fetch_one("SELECT MAX(synced_at) s FROM awards_rankings") or {}).get("s")
    return {"rows": rows or [], "total": total, "synced_at": str(stamp or "-")}


def format_answer(result: Dict[str, Any]) -> str:
    """표 + 활용 표기. ⛔ '사용 가능합니다' 같은 단정을 만들지 않는다."""
    rows = result.get("rows") or []
    if not rows:
        return "조건에 맞는 수상·랭킹 기록을 찾지 못했습니다."
    out = ["| 구분 | 브랜드 | 주최사 | 수상명 | 제품 | 순위 | 국가 | 일자 | 활용 표기 |",
           "|---|---|---|---|---|---:|---|---|---|"]
    notes: List[str] = []
    for r in rows:
        flag = (r.get("usage_flag") or "").strip()
        out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r.get("category", ""), r.get("brand", ""), r.get("organizer", ""),
            r.get("title", ""), r.get("product", ""), r.get("rank_raw") or "-",
            r.get("country", ""), r.get("award_date") or r.get("award_start") or "",
            flag or "표기 없음"))
        cond = " ".join(x for x in (r.get("usage_region"), r.get("usage_start"),
                                    r.get("usage_end")) if x and x != "-")
        if cond:
            notes.append(f"- {r.get('title', '')}: {cond}")

    seen = {(r.get("usage_flag") or "").strip() for r in rows}
    legend = " · ".join(f"`{k or '빈칸'}` {v}" for k, v in USAGE_LEGEND.items() if k in seen)
    out += ["", f"활용 표기: {legend}",
            "⚠️ 위 표기는 **시트에 적힌 원문**입니다. 실제 사용 가부는 담당자 확인이 필요합니다."]
    if notes:
        out += ["", "조건:"] + notes
    if result.get("total", 0) > len(rows):
        out.append(f"\n총 {result['total']}건 중 {len(rows)}건만 표시했습니다.")
    out.append(f"\n*기준: {result.get('synced_at', '-')} 적재분*")
    return "\n".join(out)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_awards.py -q`
Expected: PASS (22 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/core/awards.py tests/test_awards.py
git commit -m "feat(awards): 조회와 표시 — 활용 가부를 단정하지 않고 원문 표기를 보여준다"
```

---

### Task 4: 라우팅 — `@@수상` 과 일반 질문, 그리고 매출 순위를 가로채지 않기

**Files:**
- Modify: `app/core/awards.py` (의도 판정)
- Modify: `app/agents/orchestrator.py` (`_DB_REGISTRY` + 라우트 분기)
- Modify: `app/frontend/chat.js` (`SOURCE_GROUPS` + `GROUP_BY_NAME`)
- Test: `tests/test_awards.py`

**Interfaces:**
- Consumes: Task 3 의 `search`, `format_answer`
- Produces: `awards_intent(query: str, explicit: bool = False) -> str | None`
  (검색어를 돌려주거나, 이 경로가 아니면 `None`)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_awards.py 에 이어서
import re as _re

from app.core.awards import awards_intent


@pytest.mark.parametrize("q", [
    "화해 어워드에서 1위 한 제품 알려줘",
    "우리 수상 이력 알려줘",
    "글로우픽 랭킹 알려줘",
    "쇼피 Top Item 랭킹 뭐 있어?",
])
def test_award_questions_reach_this_route(q):
    assert awards_intent(q) is not None


@pytest.mark.parametrize("q", [
    "제품별 판매 순위 알려줘",
    "매출 랭킹 보여줘",
    "8월 국가별 매출 순위",
    "인도네시아 재고 얼마나 있어",
])
def test_sales_and_stock_questions_do_not_reach_this_route(q):
    """⛔ `랭킹` 두 글자를 단독으로 켜면 매출 질문을 가로챈다."""
    assert awards_intent(q) is None


def test_explicit_source_selection_always_reaches_this_route():
    assert awards_intent("아무거나", explicit=True) is not None


def test_the_registry_has_the_awards_entry_and_the_front_knows_its_group():
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert '"key": "수상"' in src and '"route": "awards"' in src
    group = _re.search(r'"key": "수상".*?"group": "([^"]+)"', src, _re.S).group(1)
    js = open("app/frontend/chat.js", encoding="utf-8").read()
    assert group in js, f"{group} 그룹이 chat.js 에 없다 — 화면에서 통째로 사라진다"


def test_both_routing_paths_are_wired():
    """⚠️ 한쪽만 고치면 경로에 따라 답이 갈린다 — 이 저장소에서 이미 겪은 사고다."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert src.count("_awards_term(") >= 3   # 정의 1 + 호출 2(비스트리밍·스트리밍)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_awards.py -q`
Expected: FAIL — `ImportError: cannot import name 'awards_intent'`

- [ ] **Step 3: 의도 판정을 구현한다**

```python
# app/core/awards.py 에 이어서

#: 이 도메인에서만 쓰이는 낱말 — 단독으로 켜도 안전하다.
_STRONG = ("수상", "어워드", "awards", "수상이력", "수상 이력")
#: ⛔ `랭킹`·`순위` 는 매출 질문에도 흔하다. **축 낱말과 함께**일 때만 켠다
#:    (물류가 `발주` 를 단독으로 쓰지 않고 튜플로 건 것과 같은 방식).
_WEAK = ("랭킹", "순위", "1위", "top")
_AXIS = ("화해", "글로우픽", "picky", "쇼피", "올리브영 글로벌", "주최사", "어워드",
         "수상", "한국소비자포럼", "female daily", "예스스타일")
#: 이 낱말이 있으면 매출·재고 질문이다 — 켜지 않는다.
_BLOCK = ("매출", "판매", "재고", "광고", "물류", "발주", "출고")

_STOP = ("알려줘", "보여줘", "뭐", "있어", "우리", "관련", "정보", "목록", "리스트")


def awards_intent(query: str, explicit: bool = False) -> Optional[str]:
    """이 경로가 맞으면 검색어를, 아니면 None."""
    q = (query or "").strip()
    if explicit:
        return _search_term(q)
    low = q.lower()
    if any(b in low for b in _BLOCK):
        return None
    if any(s in low for s in _STRONG):
        return _search_term(q)
    if any(w in low for w in _WEAK) and any(a in low for a in _AXIS):
        return _search_term(q)
    return None


def _search_term(q: str) -> str:
    words = [w for w in re.split(r"\s+", q) if w and w not in _STOP]
    return " ".join(words)[:120]
```

- [ ] **Step 4: 오케스트레이터에 배선한다**

`_DB_REGISTRY` 에서 OP 엔트리 **바로 다음 줄**에 넣는다:

```python
        # 수상/랭킹 — ⚠️ 벡터가 아니라 **표 조회**다 (`route: "awards"`).
        #    랭킹이 숫자라 임베딩으로는 1위와 10위를 구분하지 못한다 (2026-09-07 결정).
        {"key": "수상", "aliases": ["awards", "어워드", "랭킹정보", "수상랭킹"],
         "route": "awards", "group": "브랜드 성과", "icon": "star", "label": "수상",
         "desc": "수상·랭킹·설문 이력 (주최사·순위·마케팅 활용 표기)"},
```

`_handle_inventory_query` 바로 아래에 같은 모양으로 추가한다:

```python
    async def _handle_awards_query(self, term: str) -> Dict[str, Any]:
        from app.core.awards import format_answer, search
        result = await asyncio.to_thread(search, term)
        return {"answer": format_answer(result), "route": "awards",
                "sources": ["수상/랭킹 시트"]}

    @staticmethod
    def _awards_term(query, clean_query, db_entry, enabled_sources):
        from app.core.awards import awards_intent
        explicit = bool(db_entry and db_entry.get("route") == "awards")
        return awards_intent(clean_query or query, explicit=explicit)
```

그리고 `_inventory_term` 분기 **바로 다음**에, 비스트리밍(`route_and_execute`)과
스트리밍(`route_and_stream`) **양쪽에** 끼운다. 스트리밍 쪽은 `inventory` 가 하는 것과
같이 `yield ("source", "awards")` 를 함께 낸다:

```python
        _awd_term = self._awards_term(query, clean_query, db_entry, enabled_sources)
        if _awd_term is not None:
            logger.info("awards_query", term=_awd_term[:60])
            return await self._handle_awards_query(_awd_term)
```

- [ ] **Step 5: 프론트에 그룹을 등록한다 (두 곳)**

`app/frontend/chat.js` 의 `SOURCE_GROUPS` 배열에서 `bc` 항목 **앞**에:

```javascript
    { id: "awards", label: "브랜드 성과", emoji: "🏆",
      keys: [] },
```

그리고 `GROUP_BY_NAME` 표에 한 항목을 더한다:

```javascript
  var GROUP_BY_NAME = {
    "보고서": "report", "매출 데이터": "sales", "마케팅 데이터": "marketing",
    "물류 데이터": "logistics", "브랜드 성과": "awards",
    "BC": "bc", "Notion": "notion", "시스템": "system"
  };
```

⛔ `GROUP_BY_NAME` 에 없으면 `fillSourceGroups()` 가 `if (!gid) return;` 로 건너뛰어
그 그룹의 소스가 **에러 없이 화면에서 통째로 사라진다.**

Run: `node --check app/frontend/chat.js`
Expected: 출력 없음(문법 정상)

- [ ] **Step 6: 통과를 확인한다**

Run: `python -m pytest tests/test_awards.py tests/test_no_silent_failures.py -q`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/core/awards.py app/agents/orchestrator.py app/frontend/chat.js tests/test_awards.py
git commit -m "feat(awards): @@수상 배선 — 랭킹 두 글자로 매출 질문을 가로채지 않는다"
```

---

### Task 5: 잡과 자가 점검

**Files:**
- Modify: `app/main.py` (스케줄 등록 + 잡 함수 + 기동 시 `ensure_awards_table`)
- Modify: `app/core/self_check.py` (`EXPECTED_JOBS` + 검사 2종 + `CHECKS` 등록)
- Test: `tests/test_awards.py`

**Interfaces:**
- Consumes: Task 2 의 `sync_awards`, Task 3 의 `USAGE_LEGEND`
- Produces: 잡 id `awards_sync_daily`, 검사 id `awards_unknown_usage` · `awards_sheet_freshness`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_awards.py 에 이어서

def test_the_job_is_registered_and_watched():
    main = open("app/main.py", encoding="utf-8").read()
    assert "awards_sync_daily" in main and 'track_job("awards_sync_daily")' in main
    sc = open("app/core/self_check.py", encoding="utf-8").read()
    assert "awards_sync_daily" in sc


def test_both_self_checks_are_registered_in_the_checks_list():
    """함수만 만들고 CHECKS 에 안 넣으면 아무 일도 일어나지 않는다."""
    sc = open("app/core/self_check.py", encoding="utf-8").read()
    for cid in ("awards_unknown_usage", "awards_sheet_freshness"):
        assert f'Check("{cid}"' in sc, f"{cid} 가 CHECKS 에 등록되지 않았다"


def test_zero_rows_is_recorded_as_a_failure_not_a_success():
    """0행을 성공으로 적으면 자가 점검이 영영 못 잡는다."""
    main = open("app/main.py", encoding="utf-8").read()
    i = main.index('track_job("awards_sync_daily")')
    body = main[i:i + 800]
    assert "empty" in body and "raise" in body
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_awards.py -q`
Expected: FAIL — `AssertionError` 또는 `ValueError: substring not found`

- [ ] **Step 3: 잡을 등록한다**

`app/main.py` 의 `_op_inventory_sync_job` 옆에 추가한다:

```python
    def _awards_sync_job():
        from app.core.awards import sync_awards
        with track_job("awards_sync_daily") as jr:
            stat = sync_awards()
            jr.note = f"rows={stat['rows']} written={stat['written']}"
            # ⛔ 0행은 성공이 아니다 — 권한 만료·탭 이름 변경이 이렇게 온다.
            if stat.get("empty"):
                raise RuntimeError("수상/랭킹 시트가 0행이다 — 권한·탭 이름을 확인할 것")
```

스케줄러에 등록한다 (OP 재고 등록 줄 아래):

```python
            _scheduler.add_job(_awards_sync_job, "cron", hour=4, minute=40,
                               id="awards_sync_daily")
```

기동 시 테이블을 보장한다 (`ensure_inventory_table()` 호출 옆):

```python
        from app.core.awards import ensure_awards_table
        ensure_awards_table()
```

- [ ] **Step 4: 자가 점검을 등록한다**

`app/core/self_check.py` 의 `EXPECTED_JOBS` 에:

```python
    "awards_sync_daily": (26, "수상/랭킹 시트 적재 (04:40)"),
```

검사 함수 두 개를 추가한다:

```python
def _check_awards_unknown_usage() -> dict:
    """새 활용 표기가 조용히 늘어나는 것을 사람이 보게 한다."""
    from app.core.awards import USAGE_LEGEND
    rows = fetch_all("SELECT DISTINCT usage_flag f FROM awards_rankings") or []
    unknown = sorted({(r["f"] or "").strip() for r in rows} - set(USAGE_LEGEND))
    return {"ok": not unknown,
            "detail": f"미등록 활용 표기: {unknown}" if unknown else "표기 전부 등록됨"}


def _check_awards_sheet_freshness() -> dict:
    """시트가 안 바뀐 것인지, 우리가 못 읽은 것인지 가른다.

    ⚠️ 잡이 돌았다는 것과 데이터가 있다는 것은 다르다 — 0건이면 먼저 말한다.
    """
    row = fetch_one("SELECT MAX(synced_at) s, COUNT(*) n FROM awards_rankings") or {}
    n = int(row.get("n") or 0)
    stamp = row.get("s")
    if not n:
        return {"ok": False, "detail": "적재된 수상/랭킹 행이 0건 — 권한·탭 이름 확인"}
    age_h = (datetime.now() - stamp).total_seconds() / 3600 if stamp else 999
    return {"ok": age_h <= 30, "detail": f"{n}행 · 마지막 적재 {age_h:.0f}시간 전"}
```

그리고 **`CHECKS` 목록에 등록해야 실제로 돈다.** 함수만 만들고 등록하지 않으면
아무 일도 일어나지 않는다. `datasource` 검사들 옆에 넣는다:

```python
    Check("awards_unknown_usage", "datasource", SEV_INFO,
          "수상 활용 표기 미등록", _check_awards_unknown_usage),
    Check("awards_sheet_freshness", "datasource", SEV_WARNING,
          "수상/랭킹 적재 신선도", _check_awards_sheet_freshness),
```

⚠️ `Check(...)` 의 인자 순서·심각도 상수는 **바로 위 기존 항목을 그대로 보고** 맞춘다
(이 계획을 쓰는 시점의 시그니처는 `Check(id, category, severity, label, fn)` 이다).

- [ ] **Step 5: 전체 테스트**

Run: `python -m pytest tests/ -q --ignore=tests/frontend`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add app/main.py app/core/self_check.py tests/test_awards.py
git commit -m "feat(awards): 매일 적재하고 감시한다 — 0행은 실패로 남긴다"
```

---

### Task 6: 골든셋과 배포 검증

**Files:**
- Modify: `data/golden_set.json`
- Test: 프로덕션 실행

**Interfaces:**
- Consumes: Task 1~5 전부

- [ ] **Step 1: 골든 문항 3개를 넣는다**

⚠️ 기대값은 **그 답변에서만 나올 값**으로 쓴다. 그리고 **살아 있는 수치를 문자열로
얼리지 않는다** — 2026-09-07 에 문항 4개가 그래서 매일 실패했다. 수치를 봐야 하면
`number_near` 를 쓴다.

```json
{
  "id": "awards_reaches_awards",
  "category": "awards", "freq": "daily",
  "question": "화해 뷰티 어워드에서 1위 한 우리 제품 알려줘",
  "expect": {
    "contains_all": ["화해"],
    "not_contains": ["사용 가능합니다", "활용 가능합니다"],
    "min_len": 80, "max_seconds": 90
  },
  "note": "2026-09-07 신규. 수상 질문이 awards 경로로 가고, 활용 가부를 단정하지 않는지 함께 본다"
},
{
  "id": "awards_sales_rank_stays_bigquery",
  "category": "awards", "freq": "daily",
  "question": "2026년 상반기 제품별 판매수량 순위 알려줘",
  "expect": {
    "sql_contains_any": ["Total_Qty"],
    "not_contains": ["주최사", "활용 표기"],
    "min_len": 100, "max_seconds": 120
  },
  "note": "⛔ `순위`·`랭킹` 이 매출 질문을 가로채면 여기서 걸린다"
},
{
  "id": "awards_usage_not_asserted",
  "category": "awards", "freq": "daily",
  "question": "수상 이력 중에 마케팅에 쓸 수 있는 거 알려줘",
  "expect": {
    "contains_any": ["담당자 확인"],
    "not_contains": ["사용 가능합니다", "써도 됩니다"],
    "min_len": 80, "max_seconds": 90
  },
  "note": "법적 판단이다. 표기 원문과 조건만 보여주고 단정하지 않는다"
}
```

- [ ] **Step 2: 골든셋 JSON 이 깨지지 않았는지 본다**

Run: `python -c "import json; json.load(open('data/golden_set.json', encoding='utf-8'))"`
Expected: 출력 없음(성공)

- [ ] **Step 3: 전체 테스트**

Run: `python -m pytest tests/ -q --ignore=tests/frontend`
Expected: PASS

- [ ] **Step 4: 배포**

⚠️ **배포 전에 `git log --oneline -8` 로 내 마지막 배포 이후 들어온 남의 커밋을 확인한다.**
배포는 작업트리를 통째로 전송하므로 다른 세션의 미검증 변경이 함께 나간다
(2026-09-07 실제로 그렇게 나갔고 골든 문항 2개가 깨졌다). 모르는 커밋이 있으면
**배포 전에 사용자에게 알린다.**

```bash
CRAVER_SSH_PW=... ./sshenv/Scripts/python scripts/deploy_new_server.py was
```

- [ ] **Step 5: 프로덕션에서 실제로 도는지 본다**

WAS 에서 앱과 같은 환경으로:

```bash
set -a; . /home/jeffrey/AI_Agent/.env; set +a
export HTTPS_PROXY=http://10.1.50.2:3128 HTTP_PROXY=$HTTPS_PROXY NO_PROXY=localhost,127.0.0.1,10.1.0.0/16
cd /home/jeffrey/AI_Agent && ./venv/bin/python -c "
from app.core.awards import sync_awards, search
print(sync_awards())
print(search('화해')['total'])"
```
Expected: `rows` 200 근처 · `empty=False` · `search('화해')` 가 80 근처(실측 84건)

- [ ] **Step 6: 골든셋을 돌려 대조한다**

```bash
./venv/bin/python -c "
from app.core.golden_runner import run_golden, get_runs, compare_runs
r = run_golden(trigger_type='manual'); print(r['passed'], '/', r['total'])
rs = get_runs(3); print(compare_runs(rs[1]['id'], rs[0]['id']))"
```
Expected: `newly_failed` 가 비어 있을 것. 새 문항 3개가 통과할 것.

- [ ] **Step 7: 커밋**

```bash
git add data/golden_set.json
git commit -m "test(golden): 수상/랭킹 문항 3개 — 양방향으로 지킨다"
```

---

## 2단계 (PR 이슈 벡터)

별도 계획으로 쓴다 — 저장소(Qdrant)·경로(`notion`)·감시가 전부 다르고 단독으로 동작한다.
스펙 §4 가 그 범위이고, 선행 조건(시트 접근)은 2026-09-07 해소됐다.
