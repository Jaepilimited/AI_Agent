# -*- coding: utf-8 -*-
"""광고 매체가 **통째로 사라지는 것**을 잡는다 (2026-08-31 실측).

⛔ **실제로 하루 사이에 일어났다.** KBT 광고비 제보를 확인하던 중:

      오전  KakaoMoments 있음 (최신 2026-08-27 · 2026-08 집행 594,843원)
      오후  매체 19종 어디에도 없음

   셀라는 오전에 "KakaoMoments 최신은 8/27" 이라 답했고, 오후엔 "media 컬럼에
   그 값이 존재하지 않는다" 고 답했다. **둘 다 그 시점엔 사실이었다.** 사람이
   두 답을 나란히 놓고 보기 전까지 아무도 몰랐다 — 에러가 없기 때문이다.

**왜 스키마 감시로는 못 잡나**: `schema_watch` 는 테이블·컬럼을 본다. 매체가
사라지는 것은 **행이 사라지는 것**이라 스키마는 그대로다. 점검 감지
(`maintenance_auto_detect_loop`)도 못 잡는다 — 작은 매체가 빠져도 전체 행 수는
5% 임계에 못 미친다 (KakaoMoments 는 594,843원짜리 소액 집행이었다).

**감지 방식**: 매일 매체 목록을 스냅샷으로 남기고 직전 스냅샷과 비교한다.
  - 사라진 매체 → 실패로 올린다 (조용한 데이터 유실이다)
  - 새 매체 → 알리기만 한다 (정상적인 신규 집행일 수 있다)
  - 매체별 마지막 적재일도 스냅샷에 남긴다 (사후에 시차를 확인할 수 있어야 한다)

⛔ **"최신일이 안 움직였다" 는 감시하지 않는다.** 집행이 없던 날은 정상이라
   매일 대부분의 매체가 걸린다 — 처음에 넣어 봤더니 19종 중 19종이 떴다.

⚠️ **"오늘 집행이 없는 것"과 "매체가 사라진 것"은 다르다.** 그래서 오늘 행 수가
   아니라 **전체 기간의 존재 여부**를 본다 — 과거 집행분까지 없어져야 유실이다.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import structlog

from app.db.mariadb import execute, fetch_one

logger = structlog.get_logger(__name__)

TABLE = "`skin1004-319714.marketing_analysis.integrated_ad`"

_DDL = """
CREATE TABLE IF NOT EXISTS ad_media_snapshot (
    id INT AUTO_INCREMENT PRIMARY KEY,
    taken_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload MEDIUMTEXT NOT NULL,
    media_count INT NOT NULL DEFAULT 0,
    INDEX idx_taken_at (taken_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_ad_media_table() -> None:
    try:
        execute(_DDL)
    except Exception as e:
        logger.warning("ad_media_watch_table_error", error=str(e)[:160])


def _current() -> Dict[str, Dict[str, Any]]:
    """매체 → {rows, latest}. 조회 1회로 끝낸다."""
    from app.core.bigquery import get_bigquery_client

    rows = get_bigquery_client().execute_query(
        f"SELECT media, COUNT(*) AS n, MAX(date) AS latest "
        f"FROM {TABLE} GROUP BY media"
    ) or []
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("media") or "").strip()
        if not name:
            continue
        out[name] = {"rows": int(row.get("n") or 0),
                     "latest": str(row.get("latest") or "")[:10]}
    return out


def _load_last() -> Tuple[Dict[str, Dict[str, Any]], str]:
    try:
        row = fetch_one(
            "SELECT payload, taken_at FROM ad_media_snapshot ORDER BY id DESC LIMIT 1")
    except Exception as e:
        logger.warning("ad_media_watch_load_failed", error=str(e)[:160])
        return {}, ""
    if not row:
        return {}, ""
    try:
        return json.loads(row["payload"]), str(row["taken_at"])
    except Exception:
        return {}, ""


def _save(current: Dict[str, Dict[str, Any]]) -> None:
    try:
        execute("INSERT INTO ad_media_snapshot (payload, media_count) VALUES (%s, %s)",
                (json.dumps(current, ensure_ascii=False), len(current)))
        # 보관은 90일이면 충분하다 — 더 오래 두면 조회만 무거워진다
        execute("DELETE FROM ad_media_snapshot "
                "WHERE taken_at < DATE_SUB(NOW(), INTERVAL 90 DAY)")
    except Exception as e:
        logger.warning("ad_media_watch_save_failed", error=str(e)[:160])


def diff(prev: Dict[str, Dict[str, Any]],
         cur: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """사라진 매체 / 새 매체.

    ⛔ **"최신일이 안 움직인 매체" 는 세지 않는다.** 그 매체에 집행이 없던 날은
       정상이라 매일 대부분의 매체가 걸린다 — 매일 뜨는 경보는 곧 아무도 안 본다.
       적재 시차 자체는 답변에서 공시하고(`safety.loading_edge_notice_for_sql`),
       질문별 원인은 `zero_row.diagnose()` 가 짚는다. 여기는 **유실**만 본다.
    """
    gone, added = [], []
    for name, info in prev.items():
        if name not in cur:
            gone.append(f"{name} (직전 {info.get('rows', 0):,}행 · "
                        f"최신 {info.get('latest') or '?'})")
    for name, info in cur.items():
        if name not in prev:
            added.append(f"{name} ({info.get('rows', 0):,}행)")
    return {"gone": gone, "added": added}


def run(save: bool = True) -> Dict[str, Any]:
    """스냅샷을 뜨고 직전과 비교한다. 첫 실행은 기준선만 저장한다."""
    ensure_ad_media_table()
    try:
        cur = _current()
    except Exception as e:
        logger.warning("ad_media_watch_query_failed", error=str(e)[:200])
        return {"ok": False, "detail": f"매체 목록을 읽지 못했다: {type(e).__name__}"}
    if not cur:
        return {"ok": False, "detail": "매체 목록이 비어 있다 (조회 실패로 본다)"}

    prev, taken = _load_last()
    if save:
        _save(cur)
    if not prev:
        return {"ok": True, "baseline": True, "media": len(cur),
                "detail": f"기준선 저장 ({len(cur)}개 매체)"}

    d = diff(prev, cur)
    if d["gone"]:
        # ⛔ 조용한 데이터 유실이다 — 답변은 "집행이 없었다" 로 나간다
        logger.warning("ad_media_disappeared", since=taken, gone=d["gone"][:10],
                       media_now=len(cur), media_before=len(prev))
    elif d["added"]:
        logger.info("ad_media_added", since=taken, added=d["added"][:10])
    return {"ok": True, "since": taken, "media": len(cur), **d}


def summarize(result: Dict[str, Any]) -> Tuple[bool, str]:
    """자가 점검용 (성공 여부, 사람이 읽을 문장).

    ⛔ **사라진 매체만 실패로 올린다.** 새 매체·적재 정체까지 실패로 올리면
       매일 뜨고, 매일 뜨는 경보는 곧 아무도 안 본다.
    """
    if not result.get("ok"):
        return False, str(result.get("detail") or "확인 실패")
    if result.get("baseline"):
        return True, str(result.get("detail"))
    gone = result.get("gone") or []
    if gone:
        return False, ("매체가 사라졌다 — 조회하면 '집행이 없었다' 로 답한다: "
                       + ", ".join(gone[:5]))
    notes = [f"매체 {result.get('media', 0)}종 유지"]
    if result.get("added"):
        notes.append("신규 " + ", ".join(result["added"][:3]))
    return True, " · ".join(notes)
