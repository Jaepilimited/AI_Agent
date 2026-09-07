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
