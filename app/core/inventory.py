# -*- coding: utf-8 -*-
"""OP 재고 — 운영팀 구글 시트를 매일 MariaDB 로 적재하고 **조회로** 답한다.

왜 벡터가 아니라 표인가 (2026-08-25 결정):
    이 시트는 `SKU | 품목명칭 | 창고별 수량` 표다. 벡터 검색은 **의미가 비슷한 문장**을
    찾는 방식이라 여기에 맞지 않는다:
      · 숫자는 임베딩에 거의 담기지 않는다 — `104` 와 `1,040` 이 벡터상 구분되지 않는다
      · 6천 행을 청킹하면 한 조각에 SKU 수십 개가 섞여 **어느 숫자가 어느 제품 것인지**
        서술 단계에서 뒤섞인다
      · 매일 바뀌는 값이라 하루만 지나도 **틀린 재고를 자신 있게** 답하게 된다
    전성분(`ingredients.py`)에서 제품명 문자열 매칭이 오답을 낸 것과 같은 부류다.
    그래서 **적재 → SQL 조회**로 간다. 재고는 틀리게 답하는 것이 못 답하는 것보다 나쁘다.

⛔ **`SF 재고합` 컬럼은 적재하지 않는다.** 파생값이다 — SF 계열만 합산하고 다른 창고는
   빼고 센다 (실측: `AASKA019` 는 CG ETC 3,200 인데 재고합 0). 저장해 두면 언젠가
   `SUM(qty)` 에 함께 더해져 이중계상이 난다. 합계는 **조회할 때** 만든다
   (`Production_Cost2` 가 FOC 를 이미 포함하던 것과 같은 함정).

⚠️ 창고 컬럼은 시트에서 늘거나 이름이 바뀐다. 그래서 **긴 형식(long)** 으로 적재한다
   (`sku · location · qty`). 컬럼을 표 스키마에 박아 두면 시트가 바뀔 때 조용히 어긋난다.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List

import structlog

from app.db.mariadb import execute, fetch_all, fetch_one

logger = structlog.get_logger(__name__)

# 운영팀 재고 확인 시트 (2026-08-25 사용자 지정)
SHEET_ID = "1eU6ZHgxM_9NEgmWGvRfQQx1X7n4WD0kfLZ7byNoLFcM"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=1098259130"
SHEET_TAB = "SK/HQ_NEW"
HEADER_ROW = 2          # 1행은 "마지막 업데이트 일시" 머리말
DATA_START_ROW = 3
MAX_ROWS = 8000

# ⛔ 적재하지 않는 컬럼 — 파생 합계다 (위 주석 참조)
_DERIVED_COLS = {"SF 재고합"}

_DDL = """
CREATE TABLE IF NOT EXISTS op_inventory (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku VARCHAR(64) NOT NULL,
    item_name VARCHAR(255) NOT NULL DEFAULT '',
    location VARCHAR(80) NOT NULL,
    qty INT NOT NULL DEFAULT 0,
    sheet_updated_at VARCHAR(64) NOT NULL DEFAULT '',
    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_sku_loc (sku, location),
    INDEX idx_sku (sku),
    INDEX idx_name (item_name),
    INDEX idx_loc (location)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_inventory_table() -> None:
    """테이블 생성 (idempotent — 앱 기동 시 호출)."""
    try:
        execute(_DDL)
    except Exception as e:
        logger.warning("op_inventory_ddl_failed", error=str(e)[:160])


def _to_int(v: Any) -> int:
    """'3,200' · '' · '-' 를 정수로. 숫자가 아니면 0."""
    s = str(v or "").strip().replace(",", "").replace(" ", "")
    if not s or s in ("-", "#N/A", "#REF!", "#VALUE!"):
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _read_sheet() -> Dict[str, Any]:
    """시트를 읽어 (헤더 기준) 긴 형식 행 목록으로 돌려준다."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    values = (
        svc.spreadsheets().values()
        .get(spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'!A1:Z{MAX_ROWS}")
        .execute().get("values", [])
    )
    if len(values) < DATA_START_ROW:
        return {"rows": [], "locations": [], "sheet_updated_at": ""}

    # 1행 어딘가에 "마지막 업데이트 일시" 가 적혀 있다 — 신선도 판정에 쓴다
    head = values[0] if values else []
    sheet_updated = ""
    for cell in head:
        s = str(cell).strip()
        if s and s[0].isdigit() and len(s) > 8:
            sheet_updated = s[:64]
            break

    header = values[HEADER_ROW - 1]
    # 0=SKU, 1=품목명칭, 나머지가 창고 — 파생 합계는 뺀다
    locations = [(i, str(h).strip()) for i, h in enumerate(header)
                 if i >= 2 and str(h).strip() and str(h).strip() not in _DERIVED_COLS]

    rows: List[Dict[str, Any]] = []
    for r in values[DATA_START_ROW - 1:]:
        if not r:
            continue
        sku = str(r[0]).strip() if len(r) > 0 else ""
        if not sku:
            continue
        name = str(r[1]).strip() if len(r) > 1 else ""
        for idx, loc in locations:
            rows.append({
                "sku": sku[:64], "item_name": name[:255],
                "location": loc[:80], "qty": _to_int(r[idx] if len(r) > idx else 0),
            })
    return {"rows": rows, "locations": [l for _, l in locations],
            "sheet_updated_at": sheet_updated}


def sync_inventory(dry_run: bool = False) -> Dict[str, Any]:
    """시트 → `op_inventory`. 매일 11:20·16:20 (`op_inventory_sync_daily`).

    ⛔ **갱신 시각 뒤에 돌아야 한다.** 시트는 오전 10시경 갱신되는데 처음엔 04:10 에
       걸어 **매일 전날 데이터를 읽고 있었다** (2026-08-25). 적재는 성공하고 숫자만
       하루 낡는다 — 에러가 없어 시트의 `마지막 업데이트 일시` 를 대조하기 전엔 모른다.
    """
    ensure_inventory_table()
    data = _read_sheet()
    rows, locs = data["rows"], data["locations"]
    stat = {"rows": len(rows), "skus": len({r["sku"] for r in rows}),
            "locations": locs, "sheet_updated_at": data["sheet_updated_at"],
            "written": 0, "dry_run": dry_run}
    if not rows:
        logger.warning("op_inventory_empty_sheet", tab=SHEET_TAB)
        return stat
    if dry_run:
        return stat

    # ⛔ **마이크로초를 버린다.** MariaDB DATETIME 은 초 단위라 저장하면서 잘리는데,
    #    비교값에는 마이크로초가 남아 방금 넣은 행이 전부 `synced_at < now` 에 걸린다.
    #    실제로 12,166행을 넣고 12,166행을 지워 **테이블이 빈 채로 성공했다**
    #    (2026-08-25). 에러가 없어서 status() 를 안 봤으면 못 잡았다.
    now = datetime.now().replace(microsecond=0)
    # ⚠️ 지우고 다시 넣지 않는다 — 중간에 실패하면 재고가 통째로 빈다.
    #    upsert 하고, **이번에 안 들어온 조합만** 뒤에 지운다.
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        sql = ("INSERT INTO op_inventory "
               "(sku, item_name, location, qty, sheet_updated_at, synced_at) VALUES "
               + ",".join(["(%s,%s,%s,%s,%s,%s)"] * len(chunk))
               + " ON DUPLICATE KEY UPDATE item_name=VALUES(item_name), "
                 "qty=VALUES(qty), sheet_updated_at=VALUES(sheet_updated_at), "
                 "synced_at=VALUES(synced_at)")
        params: list = []
        for r in chunk:
            params += [r["sku"], r["item_name"], r["location"], r["qty"],
                       data["sheet_updated_at"], now]
        execute(sql, tuple(params))
        stat["written"] += len(chunk)

    # 시트에서 사라진 SKU·창고 조합 정리 (이번 회차에 안 닿은 행)
    # ⛔ **정리가 이번에 넣은 것보다 많으면 무언가 잘못된 것이다.** 그냥 지우면
    #    재고가 통째로 비고, 그 상태로도 앱은 에러 없이 "재고 없음" 을 답한다.
    try:
        stale = fetch_one("SELECT COUNT(*) n FROM op_inventory WHERE synced_at < %s",
                          (now,)) or {}
        n_stale = int(stale.get("n") or 0)
        if n_stale >= stat["written"]:
            logger.error("op_inventory_cleanup_refused", stale=n_stale,
                         written=stat["written"],
                         note="정리 대상이 적재분 이상 — 지우지 않는다")
            stat["cleanup_refused"] = n_stale
        elif n_stale:
            execute("DELETE FROM op_inventory WHERE synced_at < %s", (now,))
            stat["removed"] = n_stale
    except Exception as e:
        logger.warning("op_inventory_cleanup_failed", error=str(e)[:160])

    logger.info("op_inventory_synced", **{k: v for k, v in stat.items()
                                          if k != "locations"})
    return stat


# ── 조회 ─────────────────────────────────────────────────────────────────────

def _usable_words(words: List[str]) -> tuple[List[str], List[str]]:
    """품목에 실제로 있는 낱말만 남긴다 — 나머지는 질문의 군더더기다.

    ⛔ 낱말을 AND 로 걸기 때문에 **하나라도 품목에 없으면 통째로 0건**이 난다.
       실측(2026-08-25 프로덕션): "센텔라 앰플 재고 얼마나 남았어?" 가
       `센텔라 앰플 얼마나` 로 검색돼 **0건**이었다. 재고는 넉넉히 있는데
       "품목을 찾지 못했습니다" 가 나갔다 — 에러가 아니라 빈손이라 조용하다.

    ⛔ 불용어 목록을 늘리는 방식으로는 끝이 없다 (얼마나·남았어·몇 개·있나…).
       대신 **데이터에 물어본다**: 그 낱말이 든 품목이 하나도 없으면 제품 이름이
       아니라 질문의 말이다. 목록 관리가 필요 없고 새 말투에도 저절로 맞는다.

    조사도 여기서 함께 푼다 — `클레이` 가 `클레` 로 잘려 있어도(끝의 '이' 를 조사로
    본다) 원형이 맞으면 원형을 쓴다.
    """
    ensure_inventory_table()
    keep: List[str] = []
    drop: List[str] = []
    for w in words:
        if not w:
            continue
        chosen = None
        for cand in (w, w[:-1] if len(w) > 2 else None):
            if not cand:
                continue
            like = f"%{cand}%"
            hit = fetch_all(
                "SELECT 1 FROM op_inventory "
                "WHERE sku LIKE %s OR item_name LIKE %s LIMIT 1", (like, like))
            if hit:
                chosen = cand
                break
        (keep.append(chosen) if chosen else drop.append(w))
    return keep, drop


def search(term: str, limit: int = 30) -> List[Dict[str, Any]]:
    """제품명·SKU 로 찾아 창고별 수량과 합계를 돌려준다.

    ⚠️ 합계는 여기서 만든다 — 시트의 `SF 재고합` 은 적재하지 않는다 (파생·부분합).
    """
    ensure_inventory_table()
    raw = (term or "").strip()
    if not raw:
        return []
    # ⛔ 품목명이 `마다가스카르센텔라앰플100ml` 처럼 **붙어** 있다. 공백이 든 검색어를
    #    통째로 LIKE 하면 0건이 난다 ("센텔라 앰플" → 0건, 2026-08-25 실측).
    #    낱말마다 조건을 만들어 AND 로 건다 — 드라이브 검색에서 쓴 방식과 같다.
    words, dropped = _usable_words(raw.split())
    if not words:
        return []
    conds, params = [], []
    for w in words:
        like = f"%{w}%"
        conds.append("(sku LIKE %s OR item_name LIKE %s)")
        params += [like, like]
    if dropped:
        logger.info("inventory_search_dropped", words=",".join(dropped))
    return fetch_all(
        "SELECT sku, item_name, SUM(qty) AS total_qty, "
        "       GROUP_CONCAT(CONCAT(location, ':', qty) ORDER BY qty DESC "
        "                    SEPARATOR ' | ') AS by_location, "
        "       MAX(sheet_updated_at) AS sheet_updated_at "
        "FROM op_inventory WHERE " + " AND ".join(conds) +
        " GROUP BY sku, item_name ORDER BY total_qty DESC LIMIT %s",
        (*params, int(limit))) or []


def status() -> Dict[str, Any]:
    """System Status 용 한 줄 — 적재량·신선도."""
    ensure_inventory_table()
    row = fetch_one(
        "SELECT COUNT(*) n, COUNT(DISTINCT sku) skus, COUNT(DISTINCT location) locs, "
        "       MAX(synced_at) last_sync, MAX(sheet_updated_at) sheet_at "
        "FROM op_inventory") or {}
    return {
        "rows": int(row.get("n") or 0),
        "skus": int(row.get("skus") or 0),
        "locations": int(row.get("locs") or 0),
        "last_sync": row.get("last_sync"),
        "sheet_updated_at": row.get("sheet_at") or "",
        "url": SHEET_URL,
    }


# ── 질문 판정 ────────────────────────────────────────────────────────────────
# ⛔ LLM 에 맡기지 않는다. 재고는 **틀리게 답하면 안 되는** 숫자라, 어떤 질문을
#    재고로 볼지 규칙이 정한다 (성분 질의를 결정적으로 가른 것과 같은 사상).

import re as _re

_STOCK_WORDS = ("재고", "잔여수량", "잔고", "남은 수량", "남은수량", "보유수량", "stock",
                "inventory", "몇 개 남", "몇개 남")
# ⚠️ '재고'가 들어가도 재고 조회가 아닌 것들 — 규정·정의를 묻는 말이다
_NOT_STOCK = ("재고 관리 방법", "재고관리 방법", "재고 정책", "재고관리 규정",
              "재고가 뭐", "재고란", "재고 시트 어디", "재고시트 어디")
# ⛔ 검색어 추출은 **`query_keywords.extract` 한 곳**에서만 한다 (CLAUDE.md 규칙).
#    직접 정규식을 짰다가 `포어마이징` 의 `이`, `얼마야` 의 `야` 를 낱말 **안쪽에서**
#    잘라 `포어마 징` 이 됐다 (2026-08-25). `라인` ⊂ `가이드라인` 과 같은 함정이다.
#    조사는 `textmatch.strip_particle` 이 어절 단위로 뗀다.
_STOP = ("재고", "잔여수량", "잔고", "잔량", "보유수량", "수량", "현황", "stock",
         "inventory", "얼마", "얼마야", "몇개", "개", "남았어", "남아", "남은",
         "확인", "확인해줘", "알려줘", "보여줘", "조회", "조회해줘", "해줘", "줘")


def inventory_intent(query: str, explicit: bool = False) -> str | None:
    """재고 질문이면 **찾을 제품어**를 돌려준다. 아니면 None.

    `explicit` 은 `@@OP` 로 소스를 지정한 경우다 — 그때는 낱말을 안 봐도 재고로 본다.
    """
    q = (query or "").strip()
    if not q:
        return None
    low = q.lower()
    if any(bad in low for bad in _NOT_STOCK):
        return None
    if not explicit and not any(w in low for w in _STOCK_WORDS):
        return None

    from app.core.query_keywords import extract
    words = extract(q, extra_stop=_STOP)
    # 제품어가 안 남으면 전체 재고 요약을 뜻한다
    return " ".join(words) if words else "*"


# ── 신선도 ───────────────────────────────────────────────────────────────────
# ⛔ 재고는 매일 바뀐다. 시각을 **적어 두는 것만으로는 부족하다** — 사람은 표를 보지
#    각주를 안 본다. 낡았으면 답변이 **먼저 그 사실을 말해야** 한다.
#    시트 안내: "1일 1회 업데이트 (오후 2시 전후)". 적재는 11:20·16:20 에 돈다.
_STALE_HOURS = 20


def freshness() -> Dict[str, Any]:
    """적재가 얼마나 묵었나 → {stale: bool, hours: float, note: str}."""
    st = status()
    last = st.get("last_sync")
    if not last:
        return {"stale": True, "hours": None,
                "note": "재고 데이터가 아직 적재되지 않았습니다."}
    hours = (datetime.now() - last).total_seconds() / 3600
    if hours <= _STALE_HOURS:
        return {"stale": False, "hours": hours, "note": ""}
    return {"stale": True, "hours": hours,
            "note": ("⚠️ 이 재고는 **{:.0f}시간 전**에 적재된 값입니다 "
                     "(시트 기준 {}). 그 뒤 입출고가 반영되지 않았을 수 있으니 "
                     "중요한 건이면 원본 시트를 확인해 주세요.").format(
                         hours, st.get("sheet_updated_at") or "시점 미상")}
