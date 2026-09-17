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
_SOURCE_LINK = f"[수상·랭킹 원본 시트]({SHEET_URL})"
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


#: 신선도 임계(시간). 적재가 하루 한 번(04:40)이라 여유를 둔다.
#: 자가 점검과 신선도 탐침이 **같은 값**을 써야 화면과 답변이 어긋나지 않는다.
FRESHNESS_MAX_HOURS = 30


def status() -> Dict[str, Any]:
    """적재 상태 한 줄 — 행 수와 마지막 적재 시각.

    `data_freshness._probe_awards` 가 쓴다. 자가 점검은 자기 조회를 그대로 두되
    임계는 `FRESHNESS_MAX_HOURS` 를 공유한다 — 숫자가 갈리면 화면과 답변이 어긋난다.
    """
    row = fetch_one("SELECT MAX(synced_at) s, COUNT(*) n FROM awards_rankings") or {}
    return {"count": int(row.get("n") or 0), "synced_at": row.get("s")}


#: ⛔ 기호를 문장으로 바꾸지 않는다 — **뜻풀이**만 붙인다. 판단은 사람이 한다.
USAGE_LEGEND = {
    "O": "사용 승인 표기",
    "△": "조건부 표기 — 조건을 확인해야 한다",
    "X": "사용 불가 표기",
    "논의중": "논의중 표기",
    "": "표기 없음",
}

#: 활용 가부를 묻는 말. 공백을 지우고 맞춘다 (`써도 돼` / `써도돼` 둘 다 온다).
#: ⛔ 이건 **검색어가 아니라 질문의 종류**를 가른다 — 여기 걸리면 조회하지 않고
#:    고정 안내를 돌려준다. 조회하면 안 쓴 낱말로 기본 목록 40행이 나가는데,
#:    "써도 되나" 라는 물음에 표를 들이미는 것은 답이 아니라 잡음이다.
_USAGE_ASK = (
    "써도되", "써도돼", "써도괜찮", "써도무방",
    "쓸수있", "쓸수없", "쓸수있나", "사용해도", "활용해도",
    "사용가능", "활용가능", "사용할수있", "활용할수있",
    "사용권한", "활용권한", "저작권", "라이선스",
)


def asks_usage_permission(text: str) -> bool:
    """활용 가부를 묻는 말인가 (수상 맥락에서만 쓴다)."""
    compact = re.sub(r"\s+", "", (text or "")).lower()
    return any(w in compact for w in _USAGE_ASK)


def usage_permission_answer() -> str:
    """활용 가부 질문에 대한 **고정 안내**. 조회도 LLM 도 끼지 않는다.

    ⛔ 이 경로가 존재하는 이유가 이것이다. 수상 표를 받은 뒤 "이거 광고에 써도 돼?"
       라고 물으면 예전에는 `direct` 로 떨어져 **대화 맥락에 남은 △ 표를 보고 LLM 이
       "조건부라 사용 가능합니다" 를 지어낼 수 있었다.** 활용 가부는 법적 판단이고,
       못 쓰는 수상을 쓸 수 있다고 답하면 실제 문제가 된다.
       그래서 여기서는 **문장을 만들지 않는다** — 표기의 뜻만 풀어 주고 사람에게 넘긴다.
    """
    legend = "\n".join(f"- `{k or '빈칸'}` — {v}" for k, v in USAGE_LEGEND.items())
    return (
        "수상·랭킹의 **마케팅 활용 가부는 제가 판단하지 않습니다.**\n\n"
        "시트에 건별로 표기가 적혀 있고, 답변의 `활용 표기` 열이 그 원문입니다.\n\n"
        f"{legend}\n\n"
        "조건이 붙은 건은 `조건:` 줄에 원문이 함께 나옵니다 "
        "(예: 사용 지역·기간, 검수 필요 여부).\n\n"
        "⚠️ **실제 사용 가부는 담당자 확인이 필요합니다** — 유료 수상이거나 "
        "사용 지역·기간이 제한된 건이 있습니다.\n\n"
        "어느 수상인지 알려주시면 그 건의 표기와 조건을 찾아 드리겠습니다.\n\n"
        f"{_SOURCE_LINK}"
    )


_SEARCH_COLS = ("title", "product", "organizer", "country", "detail", "brand", "category")

# `N위` 는 텍스트가 아니라 숫자 필터다 — 시트마다 '1위 선정'·'TOP10 진입' 처럼
# 표기가 갈려, 문자열 AND 로는 화해 84행 중 정답 10행 중 1행만 걸린다 (실측).
_RANK_TOKEN = re.compile(r"(\d+)\s*위")

# ⛔ 데이터에는 있지만 필터로 쓰면 답을 망가뜨리는 일반 명사 (2026-09-07 controller
#    ruling — 실측 사고).
#    자료 확인은 "이 낱말이 든 행이 하나라도 있는가" 만 본다. `제품` 은
#    206행 중 6행에만("신제품" 안에) 들어 있어서 그 검사를 **통과한다** — 그런데
#    AND 필터로 쓰면 화해 1위 10행 중 9행을 날린다("화해 뷰티 어워드에서 1위 한
#    우리 제품 알려줘" → 정답 10행이 1행으로 줄었다). OP 재고의 `usable_words` 가
#    막던 것과 같은 함정("유통기한 임박한 제품" 이 품목명에 '제품' 든 3건만 찾음).
#    ⚠️ **`_STOP`(질문 형태 낱말, 예: OP 재고의 "얼마나"·"남았어") 과는 목적이
#    다르다.** `_STOP` 류는 "질문투라 애초에 검색어가 아니다" 이고, 이 목록은
#    "데이터에 실제로 있지만(다른 낱말 **안에** 우연히 끼어) 필터로 쓰면 위험하다"
#    는 뜻이라 자료 확인 **전에** 뗀다 — 확인 결과와 무관하게 무조건 뺀다.
#    ⛔ `수상`·`랭킹` 은 넣지 마라 — 그건 `구분` 을 고르는 뜻 있는 낱말이다.
_GENERIC_NOUNS = frozenset({"제품", "상품", "브랜드", "회사", "자사"})


def _extract_rank_filter(term: str) -> "tuple[Optional[int], str]":
    """`N위` 를 뽑아 숫자 필터로 돌려주고, 그 토큰은 텍스트 검색 대상에서 뗀다."""
    m = _RANK_TOKEN.search(term or "")
    if not m:
        return None, term or ""
    return int(m.group(1)), (term[:m.start()] + " " + term[m.end():])


# `2026년` 은 어느 컬럼에도 **문자열로 없다** — 날짜는 `2026-01-15` 로 들어 있다.
# 그래서 텍스트 낱말로 걸면 자료 확인이 False 를 주고 조용히 버려진다.
# 실측(2026-09-09 프로덕션): "2026년 수상 알려줘" → 연도가 통째로 무시돼 2017~2026
# 전 기간 52행이 나갔다. `N위` 와 같은 계열로 **날짜 필터**로 뺀다.
_YEAR_TOKEN = re.compile(r"(20\d\d)\s*년")
#: ⛔ `년` 없는 맨 네 자리도 연도로 읽는다 — "2026 수상" 은 흔한 말투다.
#:    단 `N위`·`N월` 을 먼저 뗀 뒤에 본다 (아래 호출 순서).
_YEAR_BARE = re.compile(r"(?<!\d)(20\d\d)(?!\d)")
#: `6월` — 연도와 **따로** 온다("6월에 받은 거"). 12를 넘는 숫자는 월이 아니다.
_MONTH_TOKEN = re.compile(r"(?<!\d)([1-9]|1[0-2])\s*월")

#: 기간을 판정할 날짜 — `award_date` 가 비었거나 `-` 면(68/206) 수상 시작일을 쓴다.
#:
#: ⚠️ **두 가지 표기가 섞여 있다**: `2026-01-15`(205행) 와 `3/9/2026`(1행).
#:    앞의 것만 읽으면 그 한 행이 기간 조회에서 조용히 빠진다 — 하필 2026년 행이다.
#: ⚠️ 실측(2026-09-09): 이 식으로 파싱 실패는 **1행**뿐이다(`award_start='진행중'`).
#:    연도별 집계가 문자열 방식과 **완전히 일치**함을 확인하고 바꿨다.
#: ⛔ **`%` 를 두 번 쓴다.** pymysql 은 params 가 빈 튜플이어도 `query % params` 를
#:    돌리므로 홑 `%` 는 그 자리에서 터진다 (CLAUDE.md 에 적힌 그 함정).
_DATE_CELL = "IF(TRIM(award_date) IN ('', '-'), award_start, award_date)"
_DATE_SQL = ("COALESCE(STR_TO_DATE({c}, '%%Y-%%m-%%d'), "
             "STR_TO_DATE({c}, '%%c/%%e/%%Y'))").format(c=_DATE_CELL)

#: 구분(`category`) 어휘 — 사람이 쓰는 말투를 시트 값으로 되돌린다.
#:
#: ⚠️ **텍스트로 걸던 것과 결과가 같은 낱말만 넣는다** (2026-09-09 프로덕션 실측).
#:    `수상` 52 = `category='수상'` 52 · `랭킹` 94 = `category='랭킹'` 94 ·
#:    `쇼피 수상` 4 = `쇼피 + category='수상'` 4. 즉 이 셋은 바꿔도 손해가 없고,
#:    대신 자료에 없는 말투(`랭크되있는거`)까지 살아난다.
#: ⛔ **`어워드`·`award` 는 넣지 마라 — 실측으로 17행이 사라진다.**
#:    `어워드` 텍스트 19행 중 `category='수상'` 은 **2행**뿐이다. 나머지는
#:    랭킹 행의 제목에 든 것이다("Daily Vanity Beauty Awards …"). 구분으로
#:    바꾸면 조용히 90%가 날아간다 — `_GENERIC_NOUNS` 가 막던 것과 같은 함정이다.
#: ⛔ `1위`·`상위` 도 넣지 마라 — 그건 구분이 아니라 순위다(`_extract_rank_filter`).
_CATEGORY_WORDS = {"랭킹": "랭킹", "랭크": "랭킹", "rank": "랭킹",
                   "수상": "수상", "설문": "설문"}


#: `올해`·`작년` — 사람은 연도를 숫자로만 말하지 않는다.
#: ⚠️ 실측(2026-09-09): "올해 받은 상 알려줘"·"작년에 받은거" 가 **전체 40행**을 냈다.
#:    `2026년` 은 고쳤는데 이건 남아 있었다 — 같은 결함의 다른 말투다.
_REL_YEAR = {"올해": 0, "금년": 0, "작년": -1, "지난해": -1, "재작년": -2}

#: 주최사 표기 별칭 — **뜻이 비슷한 말이 아니라 같은 이름의 다른 표기**다.
#:
#: ⛔ 실측(2026-09-09) 주최사 29종에 한글·영문이 섞여 있다: `TIKTOK SHOP`·`Qoo10`·
#:    `PICKY`·`Daily Vanity` 는 영문인데 `쇼피`·`화해`·`아마존` 은 한글이다.
#:    그래서 "틱톡샵에서 받은거 알려줘" 가 **전체 40행**을 냈다 — 자료에 `틱톡샵`
#:    이라는 글자가 없기 때문이다. 에러가 아니라 조용한 전체 덤프다.
#: ⛔ **같은 주최사가 자료에서 두 표기로 갈려 있다** — `스타일바나`(1건)와
#:    `STYLEVANA`(1건). 물류 `forwarder` 표기 혼재와 같은 계열이라 둘 다 건다.
#: ⛔ **뜻이 비슷할 뿐인 말은 넣지 마라** (매출/실적/성과 같은 것). 여기 들어갈 수
#:    있는 것은 **표기 변형**뿐이다 — 드라이브 검색 씨앗과 같은 기준이다.
#: ⚠️ **손으로 적은 목록은 낡는다.** 그래서 `_alias_for()` 가 별칭을 쓰기 전에
#:    **자료에 그 표기가 실제로 있는지 확인**한다 — 시트가 바뀌어 표기가 사라지면
#:    별칭은 저절로 꺼지고 원래 낱말로 되돌아간다 (0건을 만들지 않는다).
_ORG_ALIASES = {
    "틱톡샵": ("tiktok shop",), "틱톡": ("tiktok shop",), "tiktokshop": ("tiktok shop",),
    "큐텐": ("qoo10",), "규텐": ("qoo10",),
    "피키": ("picky",),
    "데일리배니티": ("daily vanity",), "데일리버니티": ("daily vanity",),
    "피메일데일리": ("female daily",),
    "코스모프로프": ("cosmoprof",),
    "립스": ("일본 lips",),
    "스타일바나": ("스타일바나", "stylevana"), "스타일베나": ("스타일바나", "stylevana"),
    "stylevana": ("스타일바나", "stylevana"),
}


def _alias_for(word: str, haystack: List[str]) -> str:
    """질문의 말을 **자료에 실제로 있는 표기**로 바꾼다. 없으면 그대로 둔다.

    ⛔ 자료 확인 없이 바꾸면 시트 표기가 바뀐 날 조용히 0건이 된다 —
       손으로 적은 목록이 낡는 그 자리다. 확인해서 쓰므로 스스로 꺼진다.
    """
    from app.core.textmatch import strip_particle

    for cand in (word, strip_particle(word)):
        spellings = _ORG_ALIASES.get((cand or "").lower())
        if not spellings:
            continue
        found = [s for s in spellings if any(s in h for h in haystack)]
        if found:
            return found[0]
    return word


def _extract_month_filter(term: str) -> "tuple[Optional[int], str]":
    """`6월` 을 뽑아 날짜 필터로 돌려주고, 그 토큰은 텍스트에서 뗀다.

    ⛔ **`N위` 를 뗀 뒤에 부른다.** 아니면 순위의 숫자가 월로 읽힐 수 있다.
    ⚠️ 붐따 #173 후속(2026-09-09): "2026년 6월꺼만 보여줘" 에서 `6월꺼만` 이
       통째로 버려져 **2026년 전체 7건**이 나갔다. 정답은 0건이다(그 달엔 기록이
       없다) — 연도만 걸고 월을 흘리면 "6월에 이만큼 받았다" 로 읽힌다.
    """
    m = _MONTH_TOKEN.search(term or "")
    if not m:
        return None, term or ""
    return int(m.group(1)), (term[:m.start()] + " " + term[m.end():])


def _extract_year_filter(term: str) -> "tuple[Optional[int], str]":
    """`2026년`·`2026` 을 뽑아 날짜 필터로 돌려주고, 그 토큰은 텍스트에서 뗀다.

    ⚠️ 남은 조사(`2026년"만"`)는 한 글자라 뒤의 `len(w) >= 2` 에서 저절로 빠진다.
    ⛔ 맨 네 자리(`2026`)는 **`N위`·`N월` 을 뗀 뒤에** 본다 — 순서가 바뀌면
       기간 표현의 숫자를 연도로 잘못 집는다.
    """
    m = _YEAR_TOKEN.search(term or "") or _YEAR_BARE.search(term or "")
    if m:
        return int(m.group(1)), (term[:m.start()] + " " + term[m.end():])
    # 숫자로 안 적은 연도 — `올해`·`작년`. ⚠️ 긴 낱말부터 봐야 `작년` 이
    # `재작년` 안에서 먼저 걸리지 않는다 (권역명 되묻기에서 겪은 그 함정).
    for word in sorted(_REL_YEAR, key=len, reverse=True):
        i = (term or "").find(word)
        if i >= 0:
            year = datetime.now().year + _REL_YEAR[word]
            return year, (term[:i] + " " + term[i + len(word):])
    return None, term or ""


def _period_hint(year: Optional[int], month: Optional[int]) -> str:
    """기간으로 좁혔는데 0건일 때 — **"안 받았다" 와 "안 적혔다" 를 가른다.**

    ⛔ "찾지 못했습니다" 만으로는 그 달에 **수상이 없었던 것**인지 **시트에 아직
       안 적힌 것**인지 알 수 없다. 프로모션 캘린더·수출 물류의 보유 구간과 같은
       함정이다 (일본 최대 달의 메가와리가 캘린더에 없어 "행사 없음" 으로 세던 그것).
       그래서 **기록이 있는 기간을 함께 적는다** — 사람이 판단할 수 있게.
    ⚠️ 0건일 때만 부른다 (조회를 늘리지 않는다).
    """
    try:
        if year is not None and month is not None:
            rows = fetch_all(
                f"SELECT MONTH({_DATE_SQL}) m, COUNT(*) n FROM awards_rankings "
                f"WHERE YEAR({_DATE_SQL}) = %s GROUP BY m HAVING m IS NOT NULL "
                "ORDER BY m", (year,))
            got = [(int(r["m"]), int(r["n"])) for r in rows if r.get("m")]
            if got:
                return (f"{year}년 {month}월에는 기록이 없습니다. "
                        f"{year}년에 기록이 있는 달은 "
                        + " · ".join(f"{m}월 {n}건" for m, n in got) + " 입니다.")
            return f"{year}년에는 기록이 아예 없습니다."
        if year is not None:
            rows = fetch_all(
                f"SELECT YEAR({_DATE_SQL}) y, COUNT(*) n FROM awards_rankings "
                f"GROUP BY y HAVING y > 0 ORDER BY y", ())
            got = [(int(r["y"]), int(r["n"])) for r in rows if r.get("y")]
            if got:
                return (f"{year}년에는 기록이 없습니다. 기록이 있는 해는 "
                        + " · ".join(f"{y}년 {n}건" for y, n in got) + " 입니다.")
        if month is not None:
            rows = fetch_all(
                f"SELECT YEAR({_DATE_SQL}) y, COUNT(*) n FROM awards_rankings "
                f"WHERE MONTH({_DATE_SQL}) = %s GROUP BY y HAVING y > 0 ORDER BY y",
                (month,))
            got = [(int(r["y"]), int(r["n"])) for r in rows if r.get("y")]
            if got:
                return (f"{month}월 기록이 있는 해는 "
                        + " · ".join(f"{y}년 {n}건" for y, n in got) + " 입니다.")
    except Exception as e:   # ⚠️ 안내를 못 만든다고 답변 자체를 죽이지 않는다
        logger.warning("awards_period_hint_failed", error=str(e)[:160])
    return ""


def _haystack() -> List[str]:
    """검색 대상 컬럼을 행마다 한 덩어리로 — `usable_words` 가 물어볼 자료다.

    ⛔ 낱말마다 SELECT 를 날리던 것을 한 번으로 바꿨다. 바꾼 진짜 이유는 성능이
       아니라 **조사**다: 예전에는 `쇼피에서` 가 자료에 없어 그냥 False 였고,
       그러면 그 낱말이 버려져 **조건이 하나도 안 남아 기본 목록 40행**이 나갔다
       (붐따 #173 — "@@수상으로 물어봤는데 그냥 원문만 보여줌", 2026-09-09).
       `query_keywords.usable_words` 는 원형·조사를 뗀 형을 함께 물어 `쇼피` 를
       살려낸다 (실측: 그 낱말 하나로 206행 → 45행).

    ⚠️ **판정 규칙은 `query_keywords.usable_words` 한 곳에만 둔다** — OP 재고·
       제품정보가 이미 같은 함수를 쓴다. 여기서 다시 구현하면 한쪽만 고쳐졌을 때
       경로에 따라 답이 갈린다 (이 저장소가 반복해서 겪은 실패다).
    """
    cols = ", ".join(_SEARCH_COLS)
    rows = fetch_all(f"SELECT {cols} FROM awards_rankings") or []
    return [" ".join(str(r.get(c) or "") for c in _SEARCH_COLS).lower() for r in rows]


def search(term: str = "", limit: int = 40) -> Dict[str, Any]:
    """낱말을 AND 로 걸되, 데이터에 없는 낱말은 빼고 건다.

    ⛔ 전부 AND 로 걸면 "화해 뷰티 어워드에서 1위 한 제품" 같은 흔한 말투가
       `어워드에서`·`1위` 때문에 0건이 된다 (실측). 대응은 셋:
       1. `N위` 는 `rank_value` 숫자 필터로 뺀다 (`_extract_rank_filter`).
       2. **일반 명사**(`_GENERIC_NOUNS`)는 데이터 확인 없이 먼저 뗀다 — 다른
          낱말 안에 우연히 끼어 있어 자료 확인을 통과해 버리기 때문이다
          (`제품` 이 6/206행에서 "신제품" 안에 있어 필터로 쓰면 정답의 90%가 날아간
          실측 사고, 2026-09-07). "화해"·"뷰티" 처럼 남은 진짜 검색어만 거른다.
       3. 나머지 낱말은 자료에 실제로 있는 것만 남긴다
          (`query_keywords.usable_words` — **조사를 뗀 형도 함께 물어본다**).
       그리고 텍스트로는 걸릴 수 없는 두 축을 따로 뺀다 (2026-09-09, 붐따 #173):
       4. `2026년` 은 날짜 필터다 (`_extract_year_filter`) — 자료에는 `2026-01-15`
          로 들어 있어 문자열로 걸면 **연도가 통째로 무시된다**.
       5. `수상`·`랭킹`·`설문` 은 구분 필터다 (`_CATEGORY_WORDS`) — `랭크되있는거`
          처럼 자료에 없는 말투로 와도 사용자가 고른 구분은 사라지면 안 된다.
       쓸 낱말이 하나도 안 남고 다른 필터도 없으면 조건 없이(=기본 목록,
       최근·상위 순) 돌려주되, **그 사실을 답변 맨 위에서 밝힌다** (`narrowed`).

    ⚠️ **상한(`[:8]`·`[:5]`)에 걸려 검토·적용되지 못한 낱말은 `capped` 로 따로
       담는다** (2026-09-07 최종 리뷰 Fix 4). `dropped`(데이터에 없어서 뺀 것)와
       뜻이 다르다 — 9번째 낱말은 자료 확인조차 안 됐고, 6번째 "있는" 낱말은
       확인은 됐지만 필터에 못 걸렸다. 둘을 같은 문구로 뭉개면 "자료에 없다" 는
       거짓 이유가 실제로 있는 낱말에 붙는다.
    """
    from app.core.query_keywords import usable_words

    # ⛔ 순서가 뜻을 바꾼다: `N위` → `N월` → 연도. 연도의 맨 네 자리 규칙이
    #    먼저 돌면 기간 표현의 숫자를 연도로 집는다.
    rank_filter, text = _extract_rank_filter(term)
    month_filter, text = _extract_month_filter(text)
    year_filter, text = _extract_year_filter(text)
    words = [w for w in re.split(r"\s+", (text or "").strip()) if len(w) >= 2]

    # ⛔ 구분(수상/랭킹/설문)은 **데이터 확인 전에** 뗀다. `랭크되있는거` 는
    #    자료에 없는 말이라 `usable_words` 가 버리는데, 사용자가 고른 구분은
    #    조용히 사라지면 안 된다 (붐따 #173 의 "2026년만 랭크되있는거").
    category_filter: Optional[str] = None
    rest: List[str] = []
    for w in words:
        hit = next((v for k, v in _CATEGORY_WORDS.items() if k in w.lower()), None)
        if hit and category_filter is None:
            category_filter = hit
        else:
            rest.append(w)
    words = rest

    # 일반 명사는 자료 확인 **전에** 뗀다 — 다른 낱말 안에 우연히 끼어 있어
    # 확인을 통과해 버리기 때문이다 (`제품` 이 "신제품" 안에 있던 실측 사고).
    generic = [w for w in words[:8] if w in _GENERIC_NOUNS]
    checkable = [w for w in words[:8] if w not in _GENERIC_NOUNS]
    capped: List[str] = list(words[8:])  # ⛔ 9번째부터는 자료 확인조차 안 됐다
    # ⚠️ 확인할 낱말이 없으면 자료를 읽지 않는다 — "2026년 랭킹" 처럼 구분·연도만
    #    물은 질문이 전체 표를 한 번 훑는 것은 낭비다.
    if checkable:
        hay = _haystack()
        # ⛔ 별칭은 자료 확인 **앞**에 끼운다. 뒤에 두면 `틱톡샵` 이 이미 버려진
        #    뒤라 되살릴 자리가 없다 (자료엔 `TIKTOK SHOP` 으로 있다).
        checkable = [_alias_for(w, hay) for w in checkable]
        kept, missing = usable_words(checkable, hay)
    else:
        kept, missing = [], []
    dropped: List[str] = generic + missing

    where = "1=1"
    params: List[Any] = []
    if rank_filter is not None:
        where += " AND rank_value = %s"
        params.append(rank_filter)
    # ⚠️ 연·월은 **같은 날짜식**을 쓴다. 갈라 두면 언제고 서로 다른 행을 골라
    #    표 안에서 숫자가 어긋나고, 그 어긋남은 티가 안 난다.
    #    실측(2026-09-09): 이 식의 연도별 집계가 예전 문자열 방식과 전 연도 일치한다.
    #    날짜를 못 읽는 행(1행, `award_start='진행중'`)은 기간을 물으면 빠진다 —
    #    모르는 것을 넣지 않는다.
    if year_filter is not None:
        where += f" AND YEAR({_DATE_SQL}) = %s"
        params.append(year_filter)
    if month_filter is not None:
        where += f" AND MONTH({_DATE_SQL}) = %s"
        params.append(month_filter)
    if category_filter is not None:
        where += " AND category = %s"
        params.append(category_filter)
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
            "dropped": dropped, "capped": capped, "rank_filter": rank_filter,
            "year_filter": year_filter, "month_filter": month_filter,
            "category_filter": category_filter,
            # ⛔ 기간으로 좁혀 0건이면 "안 받았다" 와 "안 적혔다" 가 똑같이 보인다.
            #    기록이 있는 기간을 함께 준다 (0건일 때만 조회한다).
            "period_hint": (_period_hint(year_filter, month_filter)
                            if not rows and (year_filter is not None
                                             or month_filter is not None) else ""),
            # ⛔ "아무것도 못 좁혔다" 는 답변 **맨 위**에서 말해야 한다 — 안 그러면
            #    무엇을 물어도 같은 표가 나오고, 사용자는 그것을 원문 덤프로 읽는다.
            "narrowed": bool(rank_filter is not None or year_filter is not None
                             or month_filter is not None
                             or category_filter is not None or applied)}


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
                    "(하루 한 번 04:40 적재 — 잠시 후 다시 시도해 주세요).\n\n"
                    + _SOURCE_LINK)
        # ⛔ "찾지 못했습니다" 만 주면 **그 달에 수상이 없었던 것**인지
        #    **아직 안 적힌 것**인지 구분되지 않는다. 기록이 있는 기간을 함께 적는다.
        hint = (result.get("period_hint") or "").strip()
        return ("조건에 맞는 수상·랭킹 기록을 찾지 못했습니다."
                + (" " + hint if hint else "") + "\n\n" + _SOURCE_LINK)
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
    narrowed_by: List[str] = []
    rank_filter = result.get("rank_filter")
    if rank_filter is not None:
        narrowed_by.append(f"순위 {rank_filter}위")
    # ⚠️ 연도·구분은 텍스트 낱말이 아니라 별도 필터라, 밝히지 않으면 사용자는
    #    좁혀졌는지조차 모른다 (붐따 #173 은 좁혀지지 **않은** 표를 받은 건이다).
    year_filter = result.get("year_filter")
    if year_filter is not None:
        narrowed_by.append(f"{year_filter}년")
    month_filter = result.get("month_filter")
    if month_filter is not None:
        narrowed_by.append(f"{month_filter}월")
    category_filter = result.get("category_filter")
    if category_filter:
        narrowed_by.append(f"구분 {category_filter}")
    if narrowed_by:
        # ⚠️ 앞말에 조사를 직접 붙이지 마라 — `랭킹`(받침 O)·`1위`(받침 X)로 갈려
        #    "구분 랭킹로 좁혔습니다" 가 나간다(프로덕션 실측). 고정 명사(`조건`)를
        #    세워 받침 판정 자체를 없앤다. `blocks._josa` 를 쓰려면 reports 패키지를
        #    core 로 끌어와야 하고, 그 표에는 `으로/로` 도 없다.
        out.append("\n" + " · ".join(narrowed_by) + " 조건으로 좁혔습니다.")
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

    # ⛔ **표보다 먼저 말한다.** 질문의 낱말이 하나도 조건이 되지 못하면 무엇을
    #    물어도 같은 기본 목록이 나간다 — 붐따 #173 이 그것이다("@@수상으로
    #    물어봤는데 그냥 원문만 보여줌"). 사용자는 세 번 다른 질문에 **같은 표**를
    #    받았고, 왜 그런지는 아래 각주에만 있었다 (그마저 표에 밀려 안 읽힌다).
    # ⚠️ `narrowed` 를 안 주는 호출부에서는 뜨지 않는다 — 하위 호환.
    if result.get("narrowed") is False and (result.get("dropped")
                                            or result.get("capped")):
        out.insert(0, "⚠️ 질문의 낱말로 좁히지 못해 **전체 목록**(최근·상위 순)을 "
                      "보여드립니다 — 아래 표는 질문에 대한 답이 아닙니다.\n"
                      "주최사(화해·쇼피·올리브영 글로벌…)·연도(2026년)·"
                      "구분(수상/랭킹/설문)·순위(1위)로 물어보시면 좁혀 드립니다.\n")

    out += ["", _SOURCE_LINK]
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
