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

# 탭 세 개를 쓴다 (2026-08-25 확장). 범위는 넉넉하되 무한정 읽지는 않는다.
TAB_ERP = "🐵ERP 코드 통합 재고현황🎀"
TAB_EXPIRY = "유통기한"
_RANGES = {SHEET_TAB: f"A1:Z{MAX_ROWS}", TAB_ERP: "A1:U2000", TAB_EXPIRY: "A1:Y8000"}

# ERP 탭 창고명 → SK/HQ_NEW 표기.
# ⛔ **이름을 눈으로 맞추지 않았다 — 값으로 맞췄다.** 겹치는 730개 SKU 의 창고별
#    수량을 전부 대조해 6개 모두 **100% 일치**하는 짝만 채택했다 (2026-08-25 실측).
#    `FBI` 가 `SK_FAST BEAUTY(인도네시아)` 인 것은 이름만 봐서는 알 수 없다.
# ⛔ 이 대응이 틀리면 겹치는 SKU 가 **창고 둘로 갈려 이중계상**된다. 표기를 통일해
#    `UNIQUE(sku, location)` 이 자연히 합치게 하는 것이 요점이다.
_ERP_LOCATIONS = {
    "B2B": "[현장] SF_B2B",
    "B2C": "[현장] SF_B2C",
    "CG ETC(미국)": "[현장] CG ETC(미국)",
    "FBI": "[현장] SK_FAST BEAUTY(인도네시아)",
    "플래그십": "[현장] 플래그십 스토어_명동",
    "특별관리품": "[현장] SF_PQ",
}

# ⛔ 적재하지 않는 컬럼 — 파생 합계다 (위 주석 참조).
#    ERP 탭에도 같은 함정이 있다 (`SF가용재고 합계`).
_DERIVED_COLS = {"SF 재고합", "SF가용재고 합계", "SF가용재고합계"}

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


# ⛔ 유통기한은 **재고와 다른 테이블**이다. 로트 단위 잔량이라 창고 재고와 세는
#    기준이 다르고, 한 테이블에 두면 언젠가 `SUM(qty)` 에 함께 더해져 같은 물건을
#    두 번 센다 (`SF 재고합`·`Production_Cost2` 와 같은 부류의 함정).
_DDL_EXPIRY = """
CREATE TABLE IF NOT EXISTS op_inventory_expiry (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku VARCHAR(64) NOT NULL,
    item_name VARCHAR(255) NOT NULL DEFAULT '',
    expiry_date VARCHAR(32) NOT NULL DEFAULT '',
    lot VARCHAR(64) NOT NULL DEFAULT '',
    qty INT NOT NULL DEFAULT 0,
    sheet_updated_at VARCHAR(64) NOT NULL DEFAULT '',
    synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_sku_lot_exp (sku, lot, expiry_date),
    INDEX idx_exp_sku (sku),
    INDEX idx_exp_date (expiry_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_inventory_table() -> None:
    """테이블 생성 (idempotent — 앱 기동 시 호출)."""
    for ddl in (_DDL, _DDL_EXPIRY):
        try:
            execute(ddl)
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


def _sheets_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _fetch(svc, tab: str) -> List[List[Any]]:
    return (svc.spreadsheets().values()
            .get(spreadsheetId=SHEET_ID, range="'{}'!{}".format(tab, _RANGES[tab]))
            .execute().get("values", []))


def _flat(cell: Any) -> str:
    """헤더 셀에 줄바꿈이 들어 있다 (`SKU.no` + 개행 + `(ERP)`)."""
    return str(cell).replace(chr(10), " ").strip()


def _sheet_stamp(values: List[List[Any]], scan_rows: int = 6) -> str:
    """머리말 어딘가에 적힌 "마지막 업데이트 일시" 를 찾는다.

    ⚠️ 탭마다 위치가 다르다 — SK/HQ_NEW 는 1행, ERP 는 5행이다. 행을 박아 두면
       탭이 조금만 바뀌어도 **시각이 빈 문자열이 되고, 그래도 답변은 나간다.**
    """
    for row in values[:scan_rows]:
        for cell in row or []:
            t = str(cell).strip()
            if t and t[0].isdigit() and len(t) > 8:
                return t[:64]
    return ""


def _parse_new(values: List[List[Any]]) -> Dict[str, Any]:
    """`SK/HQ_NEW` — 헤더 2행, 데이터 3행부터. 0=SKU, 1=품목명, 2~=창고."""
    if len(values) < DATA_START_ROW:
        return {"rows": [], "locations": [], "sheet_updated_at": ""}
    header = values[HEADER_ROW - 1]
    locations = [(i, _flat(h)) for i, h in enumerate(header)
                 if i >= 2 and _flat(h) and _flat(h) not in _DERIVED_COLS]
    rows: List[Dict[str, Any]] = []
    for r in values[DATA_START_ROW - 1:]:
        sku = str(r[0]).strip() if r else ""
        if not sku:
            continue
        name = str(r[1]).strip() if len(r) > 1 else ""
        for idx, loc in locations:
            rows.append({"sku": sku[:64], "item_name": name[:255],
                         "location": loc[:80],
                         "qty": _to_int(r[idx] if len(r) > idx else 0)})
    return {"rows": rows, "locations": [l for _, l in locations],
            "sheet_updated_at": _sheet_stamp(values)}


def _parse_erp(values: List[List[Any]]) -> Dict[str, Any]:
    """`ERP 코드 통합 재고현황` — SKU 를 **560개 더** 갖고 있다.

    ⚠️ 헤더 행을 숫자로 박지 않는다. 머리말 안내문이 한 줄 늘면 통째로 어긋나고,
       그때 나는 것은 에러가 아니라 **0건**이다 — `SKU.no` 가 있는 행을 찾는다.
    ⚠️ 키는 **ERP 열**이다. 첫 열은 SCM 코드라 SK/HQ_NEW 와 맞지 않는다.
    """
    hdr_idx = None
    for i, row in enumerate(values[:15]):
        joined = " ".join(_flat(c) for c in (row or []))
        if "SKU.no" in joined and "품목명칭" in joined:
            hdr_idx = i
            break
    if hdr_idx is None:
        logger.warning("op_inventory_erp_header_missing")
        return {"rows": [], "locations": [], "sheet_updated_at": ""}

    header = [_flat(c) for c in values[hdr_idx]]
    try:
        sku_col = next(i for i, h in enumerate(header)
                       if h.startswith("SKU.no") and "ERP" in h)
        name_col = header.index("품목명칭")
    except (StopIteration, ValueError):
        logger.warning("op_inventory_erp_columns_missing", header=header[:8])
        return {"rows": [], "locations": [], "sheet_updated_at": ""}

    # 창고는 **아는 이름만** 가져온다 — 모르는 컬럼(중복 SKU열·영문명)까지 창고로
    # 세면 없는 창고가 생기고 합계가 부푼다
    locs = [(i, _ERP_LOCATIONS[h]) for i, h in enumerate(header)
            if h in _ERP_LOCATIONS and h not in _DERIVED_COLS]
    rows: List[Dict[str, Any]] = []
    for r in values[hdr_idx + 1:]:
        if not r or len(r) <= sku_col:
            continue
        sku = str(r[sku_col]).strip()
        if not sku or sku.startswith("SKU"):
            continue
        name = str(r[name_col]).strip() if len(r) > name_col else ""
        for idx, loc in locs:
            rows.append({"sku": sku[:64], "item_name": name[:255],
                         "location": loc[:80],
                         "qty": _to_int(r[idx] if len(r) > idx else 0)})
    return {"rows": rows, "locations": sorted({l for _, l in locs}),
            "sheet_updated_at": _sheet_stamp(values)}


# ⛔ 시트가 **날짜 형식 두 가지를 섞어** 쓴다: `2027-03-18`(2,630건)과
#    `2029. 6. 14`(567건). 문자열로 정렬하면 같은 해 안에서 순서가 뒤집힌다 —
#    실측(2026-08-26): **43개 SKU** 에서 `2029. 6. 14` 가 `2029-06-15` 뒤로 밀렸다.
#    "임박한 순" 이라고 적어 놓고 임박한 순이 아닌 표를 보여주던 셈이다.
#    ⚠️ 전역 상위 20 은 우연히 맞았다 (점 표기가 전부 2029년이라 위로 안 온다) —
#       그래서 눈으로 봐서는 안 드러난다. 제품별 조회에서만 틀린다.
_UNDATED_FROM = "2099-01-01"


def norm_expiry(value: Any) -> str:
    """`2029. 6. 14` · `2027-03-18` → `2029-06-14` · `2027-03-18`. 못 읽으면 ""."""
    m = _re.match(r"^\s*(\d{4})[-.\s]+(\d{1,2})[-.\s]+(\d{1,2})", str(value or ""))
    if not m:
        return ""
    y, mo, d = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def is_undated(expiry: str) -> bool:
    """⛔ `2099-12-31` 은 **날짜가 아니라 '미지정' 자리표시자**다 (901건·736만개).

    실제 유통기한처럼 표에 찍으면 "2099년까지 괜찮다" 로 읽힌다. 성분에서
    '미상'을 '미포함'으로 쓰면 안 되는 것과 같은 부류 — 모르는 것은 모른다고 적는다.
    """
    return bool(expiry) and expiry >= _UNDATED_FROM


def _parse_expiry(values: List[List[Any]]) -> Dict[str, Any]:
    """`유통기한` — SKU | 상품명 | 유통기한 | LOT | 수량.

    ⛔ **재고 수량에 절대 합산하지 마라.** 로트 단위 잔량이라 창고 재고와 세는 기준이
       다르다. 표도 테이블도 따로 두는 이유가 이것이다.
    """
    hdr_idx = None
    for i, row in enumerate(values[:20]):
        cells = [str(c).strip() for c in (row or [])]
        if "SKU" in cells and "유통기한" in cells:
            hdr_idx = i
            break
    if hdr_idx is None:
        logger.warning("op_inventory_expiry_header_missing")
        return {"rows": [], "sheet_updated_at": ""}
    header = [str(c).strip() for c in values[hdr_idx]]
    col = {h: i for i, h in enumerate(header)}
    i_sku, i_exp = col.get("SKU", 0), col.get("유통기한", 2)
    i_name, i_lot = col.get("상품명", 1), col.get("LOT", 3)
    i_qty = i_lot + 1                  # 수량 열에는 머리말이 없다 ('요약' 이 적혀 있다)

    rows: List[Dict[str, Any]] = []
    last_name = ""
    for r in values[hdr_idx + 1:]:
        if not r or len(r) <= i_exp:
            continue
        sku = str(r[i_sku]).strip()
        exp = norm_expiry(r[i_exp])        # ⛔ 형식이 섞여 있다 — 여기서 통일한다
        if not sku or not exp:
            continue
        # ⚠️ 같은 SKU 의 둘째 줄부터는 상품명이 비어 있다 — 앞 값을 이어받는다
        name = str(r[i_name]).strip() if len(r) > i_name else ""
        last_name = name or last_name
        rows.append({"sku": sku[:64], "item_name": (name or last_name)[:255],
                     "expiry_date": exp[:32],
                     "lot": (str(r[i_lot]).strip() if len(r) > i_lot else "")[:64],
                     "qty": _to_int(r[i_qty] if len(r) > i_qty else 0)})
    return {"rows": rows, "sheet_updated_at": _sheet_stamp(values, scan_rows=3)}


def _read_sheet() -> Dict[str, Any]:
    """재고 두 탭을 읽어 **합쳐서** 돌려준다 (SK/HQ_NEW + ERP 통합).

    ⛔ 두 탭은 창고 표기가 다를 뿐 겹치는 SKU 의 값은 같다 (730개 100% 일치, 실측).
       표기를 통일했으므로 `UNIQUE(sku, location)` 이 자연히 합친다 — 통일하지 않으면
       같은 재고가 창고 둘로 갈려 **합계가 두 배**가 된다.
       겹칠 때는 SK/HQ_NEW 가 이긴다 (뒤에 넣어 덮어쓴다).
    """
    svc = _sheets_service()
    erp = _parse_erp(_fetch(svc, TAB_ERP))
    new = _parse_new(_fetch(svc, SHEET_TAB))
    return {
        "rows": erp["rows"] + new["rows"],
        "locations": sorted(set(erp["locations"]) | set(new["locations"])),
        "sheet_updated_at": new["sheet_updated_at"] or erp["sheet_updated_at"],
        "counts": {"new": len(new["rows"]), "erp": len(erp["rows"])},
    }


def read_expiry() -> Dict[str, Any]:
    """유통기한 탭 원본 — 조회 시점에 읽는다."""
    return _parse_expiry(_fetch(_sheets_service(), TAB_EXPIRY))


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
    try:
        stat["expiry"] = sync_expiry()
    except Exception as e:
        # ⚠️ 유통기한이 실패해도 재고 적재는 성공이다 — 서로 다른 데이터다
        logger.warning("op_expiry_sync_failed", error=str(e)[:160])
    return stat


def sync_expiry() -> Dict[str, Any]:
    """유통기한 탭 → `op_inventory_expiry` (재고 테이블과 **분리**).

    ⛔ 재고와 합치지 마라. 로트 단위 잔량이라 창고 재고와 세는 기준이 다르다.
    """
    ensure_inventory_table()
    data = _parse_expiry(_fetch(_sheets_service(), TAB_EXPIRY))
    rows = data["rows"]
    stat = {"rows": len(rows), "skus": len({r["sku"] for r in rows}),
            "sheet_updated_at": data["sheet_updated_at"], "written": 0}
    if not rows:
        logger.warning("op_expiry_empty_sheet")
        return stat

    now = datetime.now().replace(microsecond=0)      # ⛔ 마이크로초 함정 (위 참조)
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        sql = ("INSERT INTO op_inventory_expiry "
               "(sku, item_name, expiry_date, lot, qty, sheet_updated_at, synced_at) "
               "VALUES " + ",".join(["(%s,%s,%s,%s,%s,%s,%s)"] * len(chunk))
               + " ON DUPLICATE KEY UPDATE item_name=VALUES(item_name), "
                 "qty=VALUES(qty), sheet_updated_at=VALUES(sheet_updated_at), "
                 "synced_at=VALUES(synced_at)")
        params: list = []
        for r in chunk:
            params += [r["sku"], r["item_name"], r["expiry_date"], r["lot"],
                       r["qty"], data["sheet_updated_at"], now]
        execute(sql, tuple(params))
        stat["written"] += len(chunk)

    try:
        stale = fetch_one("SELECT COUNT(*) n FROM op_inventory_expiry "
                          "WHERE synced_at < %s", (now,)) or {}
        n_stale = int(stale.get("n") or 0)
        if n_stale >= stat["written"]:
            logger.error("op_expiry_cleanup_refused", stale=n_stale,
                         written=stat["written"])
            stat["cleanup_refused"] = n_stale
        elif n_stale:
            execute("DELETE FROM op_inventory_expiry WHERE synced_at < %s", (now,))
            stat["removed"] = n_stale
    except Exception as e:
        logger.warning("op_expiry_cleanup_failed", error=str(e)[:160])

    logger.info("op_expiry_synced", **stat)
    return stat


# ── 조회 시점 실시간 읽기 ────────────────────────────────────────────────────
# ⛔ **재고는 매 조회마다 시트에서 직접 읽는다** (2026-08-25 사용자 지시:
#    "op도 숫자가 매일 바뀌므로 빅쿼리처럼 조회해서 답변해야함").
#    하루 두 번 적재한 사본으로 답하면, 그 사이에 일어난 입출고를 **모른 채**
#    자신 있게 옛 숫자를 말한다. 재고는 틀리게 답하는 것이 못 답하는 것보다 나쁘다.
# ⚠️ 실측 지연(프로덕션, 프록시 경유): 재고 두 탭 합쳐 1~2초. 답변 한 번에 한 번만 읽는다.
# ⚠️ 시트를 못 읽으면 **적재본으로 물러서되 그 사실을 답변에 밝힌다** — 조용히
#    옛 숫자를 주는 것이 이 기능에서 가장 나쁜 실패다.
_LIVE_TIMEOUT_SEC = 12.0


def live_stock() -> Dict[str, Any]:
    """지금 시점의 재고. 시트를 직접 읽고, 실패하면 적재본으로 물러선다.

    돌려주는 것: {"rows": [...], "sheet_updated_at": str, "live": bool, "note": str}
    """
    import concurrent.futures

    # ⛔ `with` 로 감싸면 블록을 나갈 때 shutdown(wait=True) 가 걸려 **타임아웃이
    #    무의미해진다** (CLAUDE.md 규칙). 반드시 wait=False 로 내린다.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        data = pool.submit(_read_sheet).result(timeout=_LIVE_TIMEOUT_SEC)
        if data.get("rows"):
            return {"rows": data["rows"], "locations": data["locations"],
                    "sheet_updated_at": data["sheet_updated_at"],
                    "live": True, "note": ""}
        logger.warning("op_inventory_live_empty")
    except Exception as e:
        logger.warning("op_inventory_live_failed", error=str(e)[:200])
    finally:
        pool.shutdown(wait=False)

    st = status()
    rows = fetch_all("SELECT sku, item_name, location, qty FROM op_inventory") or []
    stamp = st.get("sheet_updated_at") or ""
    last = st.get("last_sync")
    return {"rows": rows, "locations": [], "sheet_updated_at": stamp, "live": False,
            "note": ("⚠️ 시트를 지금 읽지 못해 **마지막으로 저장해 둔 재고**로 답합니다"
                     + (" (적재 {})".format(last.strftime("%m-%d %H:%M")) if last else "")
                     + ". 그 뒤의 입출고는 반영돼 있지 않습니다.")}


def _index(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """긴 형식 행 → {sku: {name, locs{loc: qty}}}.

    ⚠️ 같은 (sku, location) 이 두 탭에서 오면 **덮어쓴다 — 더하지 않는다.**
       두 탭은 같은 재고를 다른 표기로 적은 것이라 더하면 두 배가 된다 (실측 확인).
    """
    idx: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        sku = str(r.get("sku") or "")
        if not sku:
            continue
        e = idx.setdefault(sku, {"name": "", "locs": {}})
        # ⚠️ 뒤에 오는 이름이 이긴다 — `_read_sheet` 가 ERP 를 먼저, SK/HQ_NEW 를
        #    뒤에 넣으므로 결과적으로 **원래 탭의 표기**(`(KR)…`)가 남는다.
        #    시장 접두(KR/GL/US,CA)가 OP 업무에서 의미를 갖는다.
        name = str(r.get("item_name") or "")
        if name:
            e["name"] = name
        e["locs"][str(r.get("location") or "")] = int(r.get("qty") or 0)
    return idx


# ── 조회 ─────────────────────────────────────────────────────────────────────

def usable_words(words: List[str],
                 index: Dict[str, Dict[str, Any]] | None = None
                 ) -> tuple[List[str], List[str]]:
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
    hay = _haystack(index)
    keep: List[str] = []
    drop: List[str] = []
    for w in words:
        if not w:
            continue
        # 후보는 **긴 것부터**: 원형 → 조사를 뗀 형 → 한 글자 뺀 형.
        # ⚠️ 원형을 먼저 물어야 `클레이` 가 `클레` 로 잘린 채 남지 않는다
        #    (조사 규칙상 `이` 가 떨어진다). 뜻은 같아도 답변에 잘린 말이 보인다.
        from app.core.textmatch import strip_particle
        chosen = None
        cands = [w, strip_particle(w)] + ([w[:-1]] if len(w) > 2 else [])
        for cand in dict.fromkeys(c for c in cands if c and len(c) >= 2):
            if _hits(hay, cand):
                chosen = cand
                break
        (keep.append(chosen) if chosen else drop.append(w))
    return keep, drop


def _haystack(index: Dict[str, Dict[str, Any]] | None) -> List[str]:
    """낱말 판정에 쓸 문자열 목록 (SKU + 품목명).

    스냅샷이 없으면 적재본에서 만든다 — 배치/테스트 경로용 폴백이다.
    """
    if index is None:
        ensure_inventory_table()
        rows = fetch_all("SELECT DISTINCT sku, item_name FROM op_inventory") or []
        index = {str(r["sku"]): {"name": str(r.get("item_name") or ""), "locs": {}}
                 for r in rows}
    return [(sku + " " + (e.get("name") or "")).lower() for sku, e in index.items()]


def _hits(hay: List[str], word: str) -> bool:
    w = word.lower()
    return any(w in h for h in hay)


def search(term: str, limit: int = 30,
           index: Dict[str, Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
    """제품명·SKU 로 찾아 창고별 수량과 합계를 돌려준다.

    ⚠️ 합계는 **여기서** 만든다 — 시트의 `SF 재고합`·`SF가용재고 합계` 는 파생
       부분합이라 적재하지 않는다. 저장해 두면 언젠가 함께 더해져 이중계상이 난다.
    ⛔ 품목명이 `마다가스카르센텔라앰플100ml` 처럼 **붙어** 있다. 공백이 든 검색어를
       통째로 찾으면 0건이 난다 — 낱말마다 걸어 AND 로 맞춘다.
    """
    raw = (term or "").strip()
    if not raw:
        return []
    if index is None:
        index = _index(live_stock()["rows"])
    words, dropped = usable_words(raw.split(), index)
    # ⚠️ 제품어가 하나도 없으면 **전체에서 많은 순**이다 (`재고 알려줘`·`*`).
    #    예전엔 빈 목록을 돌려줘 "품목을 찾지 못했습니다" 가 나갔다 — 질문은
    #    멀쩡한데 답이 없는 것처럼 보인다.
    if not words:
        rows = [{"sku": k, "item_name": e.get("name") or "",
                 "total_qty": sum((e.get("locs") or {}).values()),
                 "by_location": " | ".join(
                     "{}:{}".format(a, b) for a, b in
                     sorted((e.get("locs") or {}).items(), key=lambda kv: -kv[1]))}
                for k, e in index.items()]
        rows.sort(key=lambda r: -r["total_qty"])
        return rows[:int(limit)]
    if dropped:
        logger.info("inventory_search_dropped", words=",".join(dropped))

    lowered = [w.lower() for w in words]
    out: List[Dict[str, Any]] = []
    for sku, e in index.items():
        hay = (sku + " " + (e.get("name") or "")).lower()
        if all(w in hay for w in lowered):
            locs = e.get("locs") or {}
            out.append({
                "sku": sku,
                "item_name": e.get("name") or "",
                "total_qty": sum(locs.values()),
                "by_location": " | ".join(
                    "{}:{}".format(k, v) for k, v in
                    sorted(locs.items(), key=lambda kv: -kv[1])),
            })
    out.sort(key=lambda r: -r["total_qty"])
    return out[:int(limit)]


# ── 유통기한 ─────────────────────────────────────────────────────────────────
# ⛔ **재고 수량과 절대 합산하지 않는다.** 로트 단위 잔량이라 창고 재고와 세는
#    기준이 다르다. 표도 테이블도 함수도 따로 둔다.

def live_expiry() -> Dict[str, Any]:
    """지금 시점의 유통기한. 실패하면 적재본으로 물러서고 그 사실을 알린다."""
    import concurrent.futures

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        data = pool.submit(read_expiry).result(timeout=_LIVE_TIMEOUT_SEC)
        if data.get("rows"):
            return {"rows": data["rows"], "sheet_updated_at": data["sheet_updated_at"],
                    "live": True, "note": ""}
        logger.warning("op_expiry_live_empty")
    except Exception as e:
        logger.warning("op_expiry_live_failed", error=str(e)[:200])
    finally:
        pool.shutdown(wait=False)

    ensure_inventory_table()
    rows = fetch_all("SELECT sku, item_name, expiry_date, lot, qty, sheet_updated_at "
                     "FROM op_inventory_expiry") or []
    stamp = rows[0].get("sheet_updated_at") if rows else ""
    return {"rows": rows, "sheet_updated_at": stamp or "", "live": False,
            "note": "⚠️ 시트를 지금 읽지 못해 저장해 둔 유통기한으로 답합니다."}


def expiry_search(term: str, limit: int = 40) -> Dict[str, Any]:
    """제품어로 유통기한 로트를 찾는다 → 임박한 순.

    돌려주는 것: {"rows": [...], "shown": str, "dropped": [...], "live": bool, ...}
    """
    snap = live_expiry()
    rows = snap["rows"]
    index = {}
    for r in rows:
        sku = str(r.get("sku") or "")
        index.setdefault(sku, {"name": str(r.get("item_name") or ""), "locs": {}})
    keep, dropped = usable_words((term or "").split(), index)
    lowered = [w.lower() for w in keep]

    hits = []
    for r in rows:
        hay = (str(r.get("sku") or "") + " " + str(r.get("item_name") or "")).lower()
        if not lowered or all(w in hay for w in lowered):
            hits.append(r)
    # 미지정(2099)은 임박한 것이 아니다 — 맨 뒤로 보낸다
    hits.sort(key=lambda r: (is_undated(str(r.get("expiry_date") or "")),
                             str(r.get("expiry_date") or "9999")))
    undated = sum(1 for r in hits if is_undated(str(r.get("expiry_date") or "")))
    return {"rows": hits[:int(limit)], "total": len(hits), "undated": undated,
            "shown": " ".join(keep), "dropped": dropped,
            "sheet_updated_at": snap["sheet_updated_at"],
            "live": snap["live"], "note": snap["note"]}


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
        "expiry_rows": int((fetch_one(
            "SELECT COUNT(*) n FROM op_inventory_expiry") or {}).get("n") or 0),
        "url": SHEET_URL,
    }


# ── 질문 판정 ────────────────────────────────────────────────────────────────
# ⛔ LLM 에 맡기지 않는다. 재고는 **틀리게 답하면 안 되는** 숫자라, 어떤 질문을
#    재고로 볼지 규칙이 정한다 (성분 질의를 결정적으로 가른 것과 같은 사상).

import re as _re

# ⛔ 여기에 **`수량` 을 그냥 넣지 마라** — `판매수량`(BigQuery 매출 질문)을 가로챈다.
#    재고를 뜻하는 말투만 좁게 적는다. 실측(2026-08-25 프로덕션):
#    "마다가스카르 토너 몇 개 있어?" 가 아무 말에도 안 걸려 재고로 안 갔다.
_STOCK_WORDS = ("재고", "재고량", "잔여수량", "잔고", "남은 수량", "남은수량",
                "보유수량", "stock", "inventory",
                "몇 개 남", "몇개 남", "몇 개 있", "몇개 있", "몇 개나 있", "몇개나 있",
                "남았나", "남았니", "남아 있")
# ⚠️ '재고'가 들어가도 재고 조회가 아닌 것들 — 규정·정의를 묻는 말이다
_NOT_STOCK = ("재고 관리 방법", "재고관리 방법", "재고 정책", "재고관리 규정",
              "재고가 뭐", "재고란", "재고 시트 어디", "재고시트 어디")
# ⛔ 검색어 추출은 **`query_keywords.extract` 한 곳**에서만 한다 (CLAUDE.md 규칙).
#    직접 정규식을 짰다가 `포어마이징` 의 `이`, `얼마야` 의 `야` 를 낱말 **안쪽에서**
#    잘라 `포어마 징` 이 됐다 (2026-08-25). `라인` ⊂ `가이드라인` 과 같은 함정이다.
#    조사는 `textmatch.strip_particle` 이 어절 단위로 뗀다.
_STOP = ("재고", "재고량", "잔여수량", "잔고", "잔량", "보유수량", "수량", "현황", "stock",
         "inventory", "얼마", "얼마야", "몇개", "개", "남았어", "남아", "남은",
         "확인", "확인해줘", "알려줘", "보여줘", "조회", "조회해줘", "해줘", "줘",
         "있어", "있나", "있나요", "있니", "남았나", "남았니", "몇",
         # ⛔ 일반 명사는 제품 이름이 아니다. 실측(2026-08-25 프로덕션):
         #    "유통기한 임박한 제품 알려줘" 가 **품목명에 '제품' 이 든 3건**을
         #    찾아 왔다 — 전체에서 임박한 순으로 보여줘야 하는 질문이다.
         #    `usable_words` 는 데이터에 물어 판정하므로 이런 말을 걸러내지 못한다
         #    (실제로 품목명에 들어 있다). 여기서 미리 뺀다.
         "제품", "상품", "품목", "아이템", "리스트", "목록", "전체", "종류")


def _restore(question: str, words: List[str]) -> List[str]:
    """`extract` 가 뗀 조사를 **원형이 있으면** 되돌린다.

    ⚠️ `extract` 는 조사를 떼야 제 몫을 한다 (`매출이` → `매출`). 그런데 제품명에는
       조사처럼 생긴 끝글자가 있다 — `클레이` 가 `클레` 로 잘려 답변에 그대로 보였다
       (2026-08-25 프로덕션). 결과는 맞는데 **잘린 말을 사용자에게 보여준다.**
       여기서 원문 토큰을 되살리고, 진짜 조사인지는 `usable_words` 가 데이터에 물어
       판정한다 — 판정을 한 곳에 모으는 것이 요점이다.
    """
    raw = _re.findall(r"[0-9A-Za-z가-힣]+", question or "")
    out: List[str] = []
    for w in words:
        longer = next((t for t in raw if t != w and t.startswith(w)), None)
        out.append(longer or w)
    return out


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
    words = _restore(q, extract(q, extra_stop=_STOP))
    # 제품어가 안 남으면 전체 재고 요약을 뜻한다
    return " ".join(words) if words else "*"


# ⛔ 유통기한은 **재고와 다른 질문**이다. 로트별 잔량이라 합계도 다르고, 답변도
#    "언제까지" 를 말해야 한다. 신호어가 겹치므로(둘 다 '재고') 먼저 판정한다.
_EXPIRY_WORDS = ("유통기한", "유통 기한", "소비기한", "소비 기한", "유효기간",
                 "expiry", "expiration", "임박", "폐기", "shelf life")


def expiry_intent(query: str, explicit: bool = False) -> str | None:
    """유통기한 질문이면 찾을 제품어를 돌려준다. 아니면 None."""
    q = (query or "").strip()
    if not q:
        return None
    if not any(w in q.lower() for w in _EXPIRY_WORDS):
        return None
    from app.core.query_keywords import extract
    words = _restore(q, extract(q, extra_stop=_STOP + _EXPIRY_WORDS))
    return " ".join(words) if words else "*"


# ── 신선도 ───────────────────────────────────────────────────────────────────
# ⛔ 재고는 매일 바뀐다. 시각을 **적어 두는 것만으로는 부족하다** — 사람은 표를 보지
#    각주를 안 본다. 낡았으면 답변이 **먼저 그 사실을 말해야** 한다.
#    시트 안내: "1일 1회 업데이트 (오후 2시 전후)". 적재는 11:20·16:20 에 돈다.
_STALE_HOURS = 20


def parse_stamp(stamp: str):
    """`2026. 8. 25 오전 10:10:00` → datetime. 못 읽으면 None.

    ⚠️ 시트가 스스로 적어 둔 시각이다. **우리 적재 시각보다 이쪽이 진실에 가깝다** —
       적재가 제때 돌아도 시트가 안 갱신됐으면 숫자는 낡은 것이다.
    """
    m = _re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})"
                   r"(?:\s*(오전|오후)?\s*(\d{1,2}):(\d{2}))?", str(stamp or ""))
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    ampm, hh, mi = m.group(4), m.group(5), m.group(6)
    hour = int(hh) if hh else 0
    minute = int(mi) if mi else 0
    if ampm == "오후" and hour < 12:
        hour += 12
    elif ampm == "오전" and hour == 12:
        hour = 0
    try:
        return datetime(y, mo, d, hour, minute)
    except ValueError:
        return None


def freshness(stamp: str | None = None) -> Dict[str, Any]:
    """이 숫자가 얼마나 묵었나 → {stale, hours, note}.

    ⛔ 시각을 각주로 적어 두는 것만으로는 부족하다 — 사람은 표를 보지 각주를 안 본다.
       낡았으면 답변이 **표보다 먼저** 그 사실을 말해야 한다.
    """
    if stamp is None:
        stamp = status().get("sheet_updated_at") or ""
    at = parse_stamp(stamp)
    if not at:
        return {"stale": False, "hours": None, "note": ""}
    hours = (datetime.now() - at).total_seconds() / 3600
    if hours <= _STALE_HOURS:
        return {"stale": False, "hours": hours, "note": ""}
    return {"stale": True, "hours": hours,
            "note": ("⚠️ 이 재고는 시트가 **{:.0f}시간 전**({})에 갱신한 값입니다. "
                     "시트는 하루 한 번(오전 10시경) 갱신되므로 그 뒤의 입출고는 "
                     "아직 반영돼 있지 않습니다.").format(hours, stamp)}
