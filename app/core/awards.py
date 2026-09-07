# -*- coding: utf-8 -*-
"""수상/랭킹 — 시트를 MariaDB 로 적재하고 **조회로** 답한다.

왜 벡터가 아니라 표인가 (2026-09-07 결정, 스펙 §2):
    핵심이 숫자(랭킹 1위 63건)와 판정(마케팅 활용 O/△/X)이다. 임베딩은 `1` 과 `10`
    을 구분하지 못하고(OP 재고와 같은 근거), 못 쓰는 수상을 "쓸 수 있다" 고 답하면
    실제 문제가 된다. 초상권을 LLM 없는 경로로 뺀 것과 같은 계열이다.
"""
from __future__ import annotations

import hashlib
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
    """같은 행을 다시 넣을 때 겹치게 하는 키.

    ⛔ 자연키(브랜드·주최사·타이틀·제품·일자·순위)로는 **35/206(17%)이 충돌한다**
       (2026-09-07 실측, 라이브 시트). 원인 둘:
       - `country` 를 빼서 겹침: 쇼피 Top Item 같은 제품·같은 10위가
         말레이시아/글로벌/대만 3개 국가로 나뉜 3행 → 1행으로 뭉개짐
       - `detail` 을 빼서 겹침: 화해 대한민국 1위가 "저자극 스킨케어"·
         "비건 스킨케어" 두 부문인데 부문 구분이 없어 2행 → 1행으로 뭉개짐
       `+country` 만 추가해도 174/206 로 여전히 부족하다. `detail` 을 자연키에
       더하면 되지만 한 행이 1,900자가 넘어 180자 절단에서 다시 충돌이 되살아난다.
       그래서 **`_HEADER_ORDER` 전 컬럼**을 해시한다 — 실측으로 206/206(전 행 유일)
       확인했고, 전 컬럼이 완전히 같은 행은 0건이다 (진짜 중복이 없다는 뜻).
       sha256 hex 64자는 기존 `row_key VARCHAR(180)` 그대로 들어간다.
    ⚠️ 트레이드오프: 셀 하나만 고쳐도 키가 바뀌어 update 대신 delete+insert 가 된다
       (기능적으로는 같다 — `cleanup_refused` 가 대량 삭제를 계속 막아 준다).
    ⚠️ `\x1f`(cell 에 나올 수 없는 제어문자)로 이어붙인다 — `|` 같은 일반 문자는
       셀 값 안에 그대로 나올 수 있어 다른 행이 같은 문자열로 뭉개질 수 있다.
    """
    raw = "\x1f".join(str(rec.get(k, "")) for _, k in _HEADER_ORDER)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


# app/core/awards.py 에 이어서 — 조회와 표시

#: ⛔ 기호를 문장으로 바꾸지 않는다 — **뜻풀이**만 붙인다. 판단은 사람이 한다.
USAGE_LEGEND = {
    "O": "사용 승인 표기",
    "△": "조건부 표기 — 조건을 확인해야 한다",
    "X": "사용 불가 표기",
    "논의중": "논의중 표기",
    "": "표기 없음",
}

_SEARCH_COLS = ("title", "product", "organizer", "country", "detail", "brand", "category")

# `N위` 는 텍스트가 아니라 숫자 필터다 — 시트마다 '1위 선정'·'TOP10 진입' 처럼
# 표기가 갈려, 문자열 AND 로는 화해 84행 중 정답 10행 중 1행만 걸린다 (실측).
_RANK_TOKEN = re.compile(r"(\d+)\s*위")


def _extract_rank_filter(term: str) -> "tuple[Optional[int], str]":
    """`N위` 를 뽑아 숫자 필터로 돌려주고, 그 토큰은 텍스트 검색 대상에서 뗀다."""
    m = _RANK_TOKEN.search(term or "")
    if not m:
        return None, term or ""
    return int(m.group(1)), (term[:m.start()] + " " + term[m.end():])


def _word_exists(word: str) -> bool:
    """이 낱말이 든 행이 하나라도 있는가 — 없으면 검색어가 아니라 질문의 군더더기다.

    ⛔ 불용어 목록을 늘리는 방식(에서·한·우리…)은 끝이 없다. OP 재고와 같은 해법:
       **데이터에 물어본다.** 낱말당 SELECT 1 LIMIT 1 이라 비싸지 않다.
    """
    where = " OR ".join(f"{c} LIKE %s" for c in _SEARCH_COLS)
    params = tuple(f"%{word}%" for _ in _SEARCH_COLS)
    row = fetch_one(f"SELECT 1 n FROM awards_rankings WHERE {where} LIMIT 1", params)
    return bool(row)


def search(term: str = "", limit: int = 40) -> Dict[str, Any]:
    """낱말을 AND 로 걸되, 데이터에 없는 낱말은 빼고 건다.

    ⛔ 전부 AND 로 걸면 "화해 뷰티 어워드에서 1위 한 제품" 같은 흔한 말투가
       `어워드에서`·`1위` 때문에 0건이 된다 (실측). 대응은 둘:
       1. `N위` 는 `rank_value` 숫자 필터로 뺀다 (`_extract_rank_filter`).
       2. 남은 낱말은 데이터에 실제로 있는 것만 남긴다 (`_word_exists`).
       쓸 낱말이 하나도 안 남고 순위 필터도 없으면 조건 없이(=기본 목록,
       최근·상위 순) 돌려준다 — 빈 결과보다 낫다.
    """
    rank_filter, text = _extract_rank_filter(term)
    words = [w for w in re.split(r"\s+", (text or "").strip()) if len(w) >= 2]

    kept: List[str] = []
    dropped: List[str] = []
    for w in words[:8]:
        (kept if _word_exists(w) else dropped).append(w)

    where = "1=1"
    params: List[Any] = []
    if rank_filter is not None:
        where += " AND rank_value = %s"
        params.append(rank_filter)
    for w in kept[:5]:
        where += " AND (" + " OR ".join(f"{c} LIKE %s" for c in _SEARCH_COLS) + ")"
        params += [f"%{w}%"] * len(_SEARCH_COLS)

    total = int((fetch_one(f"SELECT COUNT(*) n FROM awards_rankings WHERE {where}",
                           tuple(params)) or {}).get("n") or 0)
    rows = fetch_all(
        f"SELECT * FROM awards_rankings WHERE {where} "
        "ORDER BY (rank_value IS NULL), rank_value ASC, award_date DESC LIMIT %s",
        tuple(params) + (limit,))
    stamp = (fetch_one("SELECT MAX(synced_at) s FROM awards_rankings") or {}).get("s")
    return {"rows": rows or [], "total": total, "synced_at": str(stamp or "-"),
            "dropped": dropped, "rank_filter": rank_filter}


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
    # ⛔ 안 쓴 말로 찾은 결과를 그대로 주면 사용자가 그 조건까지 맞는 줄 읽는다.
    dropped = result.get("dropped") or []
    if dropped:
        out.append("\n검색어 중 자료에 없는 낱말은 빼고 찾았습니다: " + ", ".join(dropped))
    if result.get("total", 0) > len(rows):
        out.append(f"\n총 {result['total']}건 중 {len(rows)}건만 표시했습니다.")
    out.append(f"\n*기준: {result.get('synced_at', '-')} 적재분*")
    return "\n".join(out)
