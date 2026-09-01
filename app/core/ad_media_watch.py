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

**원인 (2026-08-31 데이터팀 확인)**: 이 테이블은 **하루 두 번** 적재되는데
**오후 적재가 오류로 실패**해 매체가 빠졌다. 재실행으로 복구됐다
(KakaoMoments 816행 · 2023-08-12~2026-08-27 · 5,201만원, 매체 19→20종).

**감지 방식**: 하루 두 번 매체 목록을 스냅샷으로 남기고 직전 스냅샷과 비교한다.
  - 사라진 매체 → 실패로 올린다 (조용한 데이터 유실이다)
  - 새 매체 → 알리기만 한다 (정상적인 신규 집행일 수 있다)
  - 매체별 마지막 적재일도 스냅샷에 남긴다 (사후에 시차를 확인할 수 있어야 한다)

⛔ **"최신일이 안 움직였다" 는 감시하지 않는다.** 집행이 없던 날은 정상이라
   매일 대부분의 매체가 걸린다 — 처음에 넣어 봤더니 19종 중 19종이 떴다.

⛔ **한 번 안 보인다고 유실이 아니다.** 적재가 하루 두 번이라 스냅샷이 적재
   도중에 찍힐 수 있다 — **두 번 연속** 없을 때만 실패로 올린다.

⚠️ **"오늘 집행이 없는 것"과 "매체가 사라진 것"은 다르다.** 그래서 오늘 행 수가
   아니라 **전체 기간의 존재 여부**를 본다 — 과거 집행분까지 없어져야 유실이다.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import structlog

from app.db.mariadb import execute, fetch_all

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


def _load_recent(limit: int = 2) -> List[Tuple[Dict[str, Dict[str, Any]], str]]:
    """최근 스냅샷을 새것부터. **두 개가 필요하다** — 아래 `run()` 참조."""
    try:
        rows = fetch_all(
            "SELECT payload, taken_at FROM ad_media_snapshot "
            "ORDER BY id DESC LIMIT %s", (limit,)) or []
    except Exception as e:
        logger.warning("ad_media_watch_load_failed", error=str(e)[:160])
        return []
    out = []
    for row in rows:
        try:
            out.append((json.loads(row["payload"]), str(row["taken_at"])))
        except Exception:
            continue
    return out


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
         cur: Dict[str, Dict[str, Any]],
         older: Dict[str, Dict[str, Any]] | None = None) -> Dict[str, List[str]]:
    """사라진 매체 / 새 매체.

    ⛔ **"최신일이 안 움직인 매체" 는 세지 않는다.** 그 매체에 집행이 없던 날은
       정상이라 매일 대부분의 매체가 걸린다 — 매일 뜨는 경보는 곧 아무도 안 본다.
       적재 시차 자체는 실제로 쓰는 중일 때만 공시하고(`safety.recent_load_notice_for_sql`),
       질문별 원인은 `zero_row.diagnose()` 가 짚는다. 여기는 **유실**만 본다.
    """
    def _label(name: str, info: Dict[str, Any]) -> str:
        return (f"{name} ({info.get('rows', 0):,}행 · "
                f"최신 {info.get('latest') or '?'})")

    # ⛔ **`prev` 만 순회하면 이미 사라진 매체를 못 본다.** 두 번째 스냅샷에서는
    #    그 매체가 `prev` 에 없으므로 루프가 아예 닿지 않는다 — 확정이 영영 안 된다
    #    (처음에 이렇게 짜서 "두 번 연속 없음" 이 정상으로 통과했다).
    #    확정 조건은 **`older` 에 있었고 `prev`·`cur` 둘 다에 없는 것** 이다.
    gone = [_label(n, i) for n, i in (older or {}).items()
            if n not in prev and n not in cur]
    # 이번에 처음 안 보이는 것 — 적재 도중일 수 있어 아직 확정하지 않는다
    suspected = [_label(n, i) for n, i in prev.items() if n not in cur]
    added = [f"{n} ({i.get('rows', 0):,}행)" for n, i in cur.items() if n not in prev]
    return {"gone": gone, "suspected": suspected, "added": added}


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

    recent = _load_recent(2)
    if save:
        _save(cur)
    if not recent:
        return {"ok": True, "baseline": True, "media": len(cur),
                "detail": f"기준선 저장 ({len(cur)}개 매체)"}

    prev, taken = recent[0]
    # ⛔ **한 번 안 보인다고 유실이 아니다** (2026-08-31, 데이터팀 확인).
    #    이 테이블은 **하루 두 번** 적재된다. 스냅샷이 적재 도중에 찍히면 멀쩡한
    #    매체가 잠깐 안 보이고, 그것을 유실로 올리면 오탐이 된다. 오탐이 쌓인
    #    경보는 곧 아무도 안 본다 — 그러면 진짜 유실도 함께 묻힌다.
    #    → **두 번 연속** 없을 때만 실패로 올린다 (한 번만 없으면 `suspected`).
    older = recent[1][0] if len(recent) > 1 else None
    d = diff(prev, cur, older)
    if d["gone"]:
        # ⛔ 조용한 데이터 유실이다 — 답변은 "집행이 없었다" 로 나간다
        logger.warning("ad_media_disappeared", since=taken, gone=d["gone"][:10],
                       media_now=len(cur), media_before=len(prev))
    elif d["suspected"]:
        logger.warning("ad_media_missing_once", since=taken,
                       suspected=d["suspected"][:10],
                       note="적재 중일 수 있어 다음 스냅샷에서 확정한다")
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
    if result.get("suspected"):
        # ⚠️ 실패로 올리지 않는다 — 적재 도중일 수 있다. 다음 스냅샷이 확정한다.
        notes.append("한 번 안 보임(적재 중일 수 있음) "
                     + ", ".join(result["suspected"][:3]))
    if result.get("added"):
        notes.append("신규 " + ", ".join(result["added"][:3]))
    return True, " · ".join(notes)
