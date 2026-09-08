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

# ⛔ 데이터에는 있지만 필터로 쓰면 답을 망가뜨리는 일반 명사 (2026-09-07 controller
#    ruling — 실측 사고).
#    `_word_exists` 는 "이 낱말이 든 행이 하나라도 있는가" 만 본다. `제품` 은
#    206행 중 6행에만("신제품" 안에) 들어 있어서 그 검사를 **통과한다** — 그런데
#    AND 필터로 쓰면 화해 1위 10행 중 9행을 날린다("화해 뷰티 어워드에서 1위 한
#    우리 제품 알려줘" → 정답 10행이 1행으로 줄었다). OP 재고의 `usable_words` 가
#    막던 것과 같은 함정("유통기한 임박한 제품" 이 품목명에 '제품' 든 3건만 찾음).
#    ⚠️ **`_STOP`(질문 형태 낱말, 예: OP 재고의 "얼마나"·"남았어") 과는 목적이
#    다르다.** `_STOP` 류는 "질문투라 애초에 검색어가 아니다" 이고, 이 목록은
#    "데이터에 실제로 있지만(다른 낱말 **안에** 우연히 끼어) 필터로 쓰면 위험하다"
#    는 뜻이라 `_word_exists` 검사 **전에** 뗀다 — 검사 결과와 무관하게 무조건 뺀다.
#    ⛔ `수상`·`랭킹` 은 넣지 마라 — 그건 `구분` 을 고르는 뜻 있는 낱말이다.
_GENERIC_NOUNS = frozenset({"제품", "상품", "브랜드", "회사", "자사"})


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
       `어워드에서`·`1위` 때문에 0건이 된다 (실측). 대응은 셋:
       1. `N위` 는 `rank_value` 숫자 필터로 뺀다 (`_extract_rank_filter`).
       2. **일반 명사**(`_GENERIC_NOUNS`)는 데이터 확인 없이 먼저 뗀다 — 다른
          낱말 안에 우연히 끼어 있어 `_word_exists` 를 통과해 버리기 때문이다
          (`제품` 이 6/206행에서 "신제품" 안에 있어 필터로 쓰면 정답의 90%가 날아간
          실측 사고, 2026-09-07). "화해"·"뷰티" 처럼 남은 진짜 검색어만 거른다.
       3. 나머지 낱말은 데이터에 실제로 있는 것만 남긴다 (`_word_exists`).
       쓸 낱말이 하나도 안 남고 순위 필터도 없으면 조건 없이(=기본 목록,
       최근·상위 순) 돌려준다 — 빈 결과보다 낫다.

    ⚠️ **상한(`[:8]`·`[:5]`)에 걸려 검토·적용되지 못한 낱말은 `capped` 로 따로
       담는다** (2026-09-07 최종 리뷰 Fix 4). `dropped`(데이터에 없어서 뺀 것)와
       뜻이 다르다 — 9번째 낱말은 `_word_exists` 조차 안 돌았고, 6번째 "있는" 낱말은
       확인은 됐지만 필터에 못 걸렸다. 둘을 같은 문구로 뭉개면 "자료에 없다" 는
       거짓 이유가 실제로 있는 낱말에 붙는다.
    """
    rank_filter, text = _extract_rank_filter(term)
    words = [w for w in re.split(r"\s+", (text or "").strip()) if len(w) >= 2]

    kept: List[str] = []
    dropped: List[str] = []
    capped: List[str] = list(words[8:])  # ⛔ 9번째부터는 데이터 확인조차 안 됐다
    for w in words[:8]:
        if w in _GENERIC_NOUNS:
            dropped.append(w)
        elif _word_exists(w):
            kept.append(w)
        else:
            dropped.append(w)

    where = "1=1"
    params: List[Any] = []
    if rank_filter is not None:
        where += " AND rank_value = %s"
        params.append(rank_filter)
    applied, overflow = kept[:5], kept[5:]
    capped += overflow  # ⛔ 데이터에 있는 낱말인데 5개 상한 때문에 조건에 못 걸렸다
    for w in applied:
        where += " AND (" + " OR ".join(f"{c} LIKE %s" for c in _SEARCH_COLS) + ")"
        params += [f"%{w}%"] * len(_SEARCH_COLS)

    total = int((fetch_one(f"SELECT COUNT(*) n FROM awards_rankings WHERE {where}",
                           tuple(params)) or {}).get("n") or 0)
    rows = fetch_all(
        f"SELECT * FROM awards_rankings WHERE {where} "
        "ORDER BY (rank_value IS NULL), rank_value ASC, award_date DESC LIMIT %s",
        tuple(params) + (limit,))
    stamp = (fetch_one("SELECT MAX(synced_at) s FROM awards_rankings") or {}).get("s")
    # ⛔ 2026-09-07 최종 리뷰 Fix 3: 테이블 전체가 0행("아직 적재 안 됨")과 조건에
    #    맞는 게 0행("그런 수상이 없음")은 다른 사실이다. `MAX(synced_at)` 가 NULL
    #    이면 — WHERE 절과 무관하게 — 한 번도 적재된 적이 없다는 뜻이라 이 하나의
    #    조회로 판정한다(추가 COUNT 조회를 늘리지 않는다). `ensure_awards_table()`
    #    이 기동 시 테이블 자체는 만들어 두므로 이 SELECT 는 실패하지 않는다.
    return {"rows": rows or [], "total": total, "synced_at": str(stamp or "-"),
            "synced_at_raw": stamp, "table_empty": stamp is None,
            "dropped": dropped, "capped": capped, "rank_filter": rank_filter}


#: 실측 최대 73자(2026-09-07) — 짧은 편이라 대부분 안 잘리지만, 늘어날 수 있으니
#: 자르는 코드는 남겨 두되 **자른 사실이 보이게**(`…`) 한다.
_DETAIL_MAX = 60
#: `self_check._check_awards_sheet_freshness()` 와 같은 30시간 기준. 상수를 여기
#: 또 두는 이유 — `self_check` 는 여러 도메인을 훑는 얕은 층이라 거꾸로
#: `awards` 를 import 하면 방향이 뒤집힌다. 바꿀 땐 두 곳을 함께 고칠 것.
_STALE_HOURS = 30


def _cell(v: Any) -> str:
    """마크다운 표 셀로 안전하게 만든다.

    ⛔ `|`·개행이 든 값 하나가 표 전체를 깨뜨린다 (2026-09-07 최종 리뷰 Minor).
       `inventory` 의 재고 표가 이미 같은 방식(`|`→`/`)을 쓴다 — 그대로 맞춘다.
    """
    s = str(v) if v is not None else ""
    return s.replace("|", "/").replace("\r", " ").replace("\n", " ").strip()


def _truncated_detail(detail: Any) -> str:
    s = _cell(detail)
    return s if len(s) <= _DETAIL_MAX else s[:_DETAIL_MAX].rstrip() + "…"


def format_answer(result: Dict[str, Any]) -> str:
    """표 + 활용 표기. ⛔ '사용 가능합니다' 같은 단정을 만들지 않는다."""
    rows = result.get("rows") or []
    if not rows:
        # ⛔ 2026-09-07 최종 리뷰 Fix 3: 테이블이 통째로 비어 있는 것("아직 적재
        #    안 됐다")과 조건에 맞는 행이 없는 것("그런 수상이 없다")은 다른
        #    사실이다. 기동 직후~첫 적재(04:40) 사이의 질문이 후자로 읽히면
        #    "수상이 없다" 로 오인된다 — 프로모션 캘린더·물류 보유구간과 같은 함정.
        if result.get("table_empty"):
            return ("수상·랭킹 자료가 아직 적재되지 않았습니다 "
                    "(하루 한 번 04:40 적재 — 잠시 후 다시 시도해 주세요).")
        return "조건에 맞는 수상·랭킹 기록을 찾지 못했습니다."
    out = ["| 구분 | 브랜드 | 주최사 | 수상명 | 제품 | 상세 | 순위 | 국가 | 일자 | 활용 표기 |",
           "|---|---|---|---|---|---|---:|---|---|---|"]
    notes: List[str] = []
    for r in rows:
        flag = (r.get("usage_flag") or "").strip()
        out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            _cell(r.get("category")), _cell(r.get("brand")), _cell(r.get("organizer")),
            _cell(r.get("title")), _cell(r.get("product")),
            _truncated_detail(r.get("detail")),
            _cell(r.get("rank_raw")) or "-",
            _cell(r.get("country")),
            _cell(r.get("award_date") or r.get("award_start")),
            _cell(flag) or "표기 없음"))
        cond = " ".join(x for x in (r.get("usage_region"), r.get("usage_start"),
                                    r.get("usage_end")) if x and x != "-")
        # ⛔ 2026-09-07 최종 리뷰 Fix 2: 조건부(△) 65행 중 86%는 `usage_region` 이
        #    비어 있어 "왜 조건부인지" 표에서 사라진다. `detail`(수상 부문·근거,
        #    최대 73자)이 채울 수 있는 유일한 값이다. **비어 있고 △일 때만**
        #    보완한다 — O·X·논의중은 조건이 아니라 확정 판정이라, 거기 detail 을
        #    붙이면 없던 조건이 생긴 것처럼 읽힌다 (판단 근거는 리포트에도 적었다).
        if not cond and flag == "△" and (r.get("detail") or "").strip():
            cond = "(상세) " + _truncated_detail(r.get("detail"))
        if cond:
            notes.append(f"- {_cell(r.get('title'))}: {cond}")

    seen = {(r.get("usage_flag") or "").strip() for r in rows}
    legend = " · ".join(f"`{k or '빈칸'}` {v}" for k, v in USAGE_LEGEND.items() if k in seen)
    out += ["", f"활용 표기: {legend}",
            "⚠️ 위 표기는 **시트에 적힌 원문**입니다. 실제 사용 가부는 담당자 확인이 필요합니다."]
    if notes:
        out += ["", "조건:"] + notes
    # ⛔ 조용히 좁히지 마라 — rank_value 필터가 걸린 사실도 dropped 만큼 공시한다.
    #    없으면(None) 아무 문구도 만들지 않는다 — 매번 뜨는 안내는 곧 아무도 안 읽는다.
    rank_filter = result.get("rank_filter")
    if rank_filter is not None:
        out.append(f"\n순위 {rank_filter}위로 좁혔습니다.")
    # ⛔ 안 쓴 말로 찾은 결과를 그대로 주면 사용자가 그 조건까지 맞는 줄 읽는다.
    #    2026-09-07 최종 리뷰 Minor — "자료에 없는" 은 `_GENERIC_NOUNS`(제품·브랜드…)
    #    에는 거짓이다(실제로 "신제품" 안에 있다). 이유를 밝히지 않는 참인 문구로 바꿨다.
    dropped = result.get("dropped") or []
    if dropped:
        out.append("\n검색어 중 다음은 필터로 쓰지 않고 찾았습니다: " + ", ".join(dropped))
    # ⛔ 2026-09-07 최종 리뷰 Fix 4: 상한(9번째 낱말·6번째 이후 "있는" 낱말) 때문에
    #    검토·적용되지 못한 낱말은 `dropped`(데이터에 없어서 뺀 것)와 이유가 달라
    #    따로 공시한다 — 안 그러면 있는 낱말이 "자료에 없다" 는 거짓 이유를 뒤집어쓴다.
    capped = result.get("capped") or []
    if capped:
        out.append("\n낱말이 많아 다음은 조건에 반영하지 못했습니다: " + ", ".join(capped))
    if result.get("total", 0) > len(rows):
        out.append(f"\n총 {result['total']}건 중 {len(rows)}건만 표시했습니다.")

    stamp_display = result.get("synced_at", "-")
    raw_stamp = result.get("synced_at_raw")
    stale = (isinstance(raw_stamp, datetime)
             and (datetime.now() - raw_stamp).total_seconds() / 3600 > _STALE_HOURS)
    if stale:
        # ⛔ 2026-09-07 최종 리뷰 Minor — "낡으면 표보다 먼저 말한다" (사람은 표를
        #    보지 각주를 안 본다, 수출 물류 이상치 공시와 같은 자리). 호출부가
        #    `synced_at_raw` 를 안 주면(대부분의 기존 테스트) `stale` 은 항상
        #    False 라 옛 자리(맨 끝)를 그대로 쓴다 — 하위 호환이 깨지지 않는다.
        out.insert(0, f"⚠️ 마지막 적재가 {stamp_display} 로 오래됐습니다 "
                      "— 최신 수상/랭킹이 반영되지 않았을 수 있습니다.\n")
    else:
        out.append(f"\n*기준: {stamp_display} 적재분*")
    return "\n".join(out)


# ── 라우팅 의도 판정 (Task 4) ──────────────────────────────────────────
#: 이 도메인에서만 쓰이는 낱말 — 단독으로 켜도 안전하다.
_STRONG = ("수상", "어워드", "awards", "수상이력", "수상 이력")
#: ⛔ `랭킹`·`순위` 는 매출 질문에도 흔하다. **축 낱말과 함께**일 때만 켠다
#:    (물류가 `발주` 를 단독으로 쓰지 않고 튜플로 건 것과 같은 방식).
#: ⛔ `"top"` 을 라틴 세 글자 단독으로 넣지 마라 — `desktop`·`laptop`·`stop` 안에
#:    그대로 걸린다 (`eta` 가 `meta`·`beta` 안에 걸린 사고와 같은 패턴. 낱말 경계
#:    방어 `textmatch.standalone()` 은 **앞 글자가 한글일 때만** 본다). 뺐다 —
#:    "쇼피 Top Item 랭킹" 은 `랭킹` 한글 낱말만으로 이미 잡힌다.
_WEAK = ("랭킹", "순위", "1위")
_AXIS = ("화해", "글로우픽", "picky", "쇼피", "올리브영 글로벌", "주최사", "어워드",
         "수상", "한국소비자포럼", "female daily", "예스스타일")
#: 이 낱말이 있으면 매출·재고 질문이다 — 켜지 않는다.
_BLOCK = ("매출", "판매", "재고", "광고", "물류", "발주", "출고")

_STOP = ("알려줘", "보여줘", "뭐", "있어", "우리", "관련", "정보", "목록", "리스트")


def awards_intent(query: str, explicit: bool = False) -> Optional[str]:
    """이 경로가 맞으면 검색어를, 아니면 None.

    ⛔ `explicit=True` 는 빈 문자열을 돌려줄 수 있다 — 빈 문자열은 falsy 라
       호출부가 `if _awd_term:` 로 받으면 `@@수상` 만 찍은 사용자가 경로를 못 타고
       **에러 없이 일반 답변**을 받는다. 호출부는 반드시 `is not None` 으로 볼 것.

    ⛔ **`_STRONG` 이 `_BLOCK` 을 이긴다 — 그 반대가 아니다** (2026-09-07 리뷰 실측
       사고: `"판매 1위 수상 이력 알려줘"`·`"매출 1위 어워드 받았어?"` 가 `_BLOCK`
       의 `판매`·`매출` 에 먼저 걸려 `None` 이 됐고, 그 질문은 BigQuery 로 새서
       **엉뚱한 매출 숫자로 자신 있게** 답했다 — 여기서 막는 것보다 나쁜 실패다.
       `수상`·`어워드` 라는 말 자체가 이 질문의 주제를 결정적으로 밝히므로
       매출 낱말이 같이 있어도 이 경로를 켠다.
    ⚠️ **`_WEAK + _AXIS` 경로에서는 `_BLOCK` 이 그대로 이긴다** — 비대칭이 핵심이다.
       축 낱말이 판매 채널과 겹친다 (`쇼피`·`올리브영 글로벌` 은 수상 주최사이면서
       판매 채널이다). `"쇼피 매출 순위"` 는 `수상`·`어워드` 가 없으니 계속 매출
       질문으로 남아야 한다 — `_WEAK` 만으로 `_BLOCK` 을 이기게 하면 그 반대가 샌다.
    """
    q = (query or "").strip()
    if explicit:
        return _search_term(q)
    low = q.lower()
    if any(s in low for s in _STRONG):
        return _search_term(q)
    if any(b in low for b in _BLOCK):
        return None
    if any(w in low for w in _WEAK) and any(a in low for a in _AXIS):
        return _search_term(q)
    return None


def _search_term(q: str) -> str:
    words = [w for w in re.split(r"\s+", q) if w and w not in _STOP]
    return " ".join(words)[:120]
