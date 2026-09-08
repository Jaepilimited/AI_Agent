# -*- coding: utf-8 -*-
"""노션에 걸어 뒀는데 **학습되지 않은 자료**를 사람보다 먼저 안다 (2026-09-04).

⛔ **왜 만들었나**: 사용자가 제품 라인업 DB 를 DB-HUB 에 걸었는데 학습되지 않았고,
   **사람이 물어봐서야** 알았다. 파이프라인은 매일 `404스킵=20` 을 찍고 있었지만
   그 숫자를 아무도 안 봤다 — 로그는 읽는 사람이 없으면 없는 것과 같다.

⚠️ **건수만으로는 조치할 수 없다.** 누구에게 무엇을 공유해 달라고 할지 모른다.
   그래서 파이프라인이 **누가·어느 팀의·어떤 페이지인지**까지 남긴다.

⛔ **총 건수로 매일 경고하지 않는다.** 지금 20건이 밀려 있는데 그걸로 매일 울리면
   곧 아무도 안 읽는다 (`ad_media_missing`·`new_log_errors` 와 같은 규칙).
   **새로 생긴 것**만 실패로 올린다 — "방금 건 자료가 안 들어왔다" 가 진짜 신호다.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# ⚠️ 값 목록과 같은 캐시 표를 쓴다 (작은 JSON 하나라 표를 더 만들 이유가 없다).
_CACHE_NAME = "NotionUnshared"


def record(skipped: List[dict]) -> int:
    """파이프라인이 건너뛴 페이지 목록을 저장한다. 실행 때마다 덮어쓴다."""
    from app.core.value_lists import ensure_value_cache_table
    from app.db.mariadb import execute

    payload = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pages": [
            {"page_id": str(p.get("page_id") or "").replace("-", ""),
             "team": p.get("team") or "", "title": p.get("title") or "",
             "kind": p.get("kind") or "page"}
            for p in (skipped or [])
        ],
    }
    try:
        ensure_value_cache_table()
        execute(
            "INSERT INTO bq_value_cache (name, payload, n) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE payload = VALUES(payload), n = VALUES(n), "
            "updated_at = CURRENT_TIMESTAMP",
            (_CACHE_NAME, json.dumps(payload, ensure_ascii=False), len(payload["pages"])))
    except Exception as e:
        # ⚠️ 기록 실패가 파이프라인을 죽이면 안 된다 — 색인이 본업이다
        logger.warning("notion_unshared_record_failed", error=str(e)[:140])
        return 0
    logger.info("notion_unshared_recorded", count=len(payload["pages"]))
    return len(payload["pages"])


def latest() -> Optional[Dict]:
    from app.db.mariadb import fetch_one

    try:
        row = fetch_one("SELECT payload, updated_at FROM bq_value_cache WHERE name = %s",
                        (_CACHE_NAME,))
    except Exception as e:
        logger.warning("notion_unshared_read_failed", error=str(e)[:140])
        return None
    if not row or not row.get("payload"):
        return None
    try:
        data = json.loads(row["payload"])
    except (TypeError, ValueError):
        return None
    data["updated_at"] = row.get("updated_at")
    return data


_SEEN_NAME = "NotionUnsharedSeen"


def _seen_ids() -> set:
    from app.db.mariadb import fetch_one

    try:
        row = fetch_one("SELECT payload FROM bq_value_cache WHERE name = %s", (_SEEN_NAME,))
        return set(json.loads(row["payload"])) if row and row.get("payload") else set()
    except Exception:
        return set()


def _remember(ids: set) -> None:
    from app.core.value_lists import ensure_value_cache_table
    from app.db.mariadb import execute

    try:
        ensure_value_cache_table()
        execute(
            "INSERT INTO bq_value_cache (name, payload, n) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE payload = VALUES(payload), n = VALUES(n), "
            "updated_at = CURRENT_TIMESTAMP",
            (_SEEN_NAME, json.dumps(sorted(ids)), len(ids)))
    except Exception as e:
        logger.warning("notion_unshared_remember_failed", error=str(e)[:140])


def newly_unshared() -> Dict:
    """지난번엔 없었는데 **이번에 새로 생긴** 미학습 페이지.

    ⛔ 총 건수로 매일 울리지 않는다 — 밀린 20건 때문에 경보가 소음이 된다.
       새로 생긴 것만 올린다: "방금 건 자료가 안 들어왔다" 가 진짜 신호다.
    ⚠️ 본 것은 기억해 둔다. 그래서 **같은 건으로 두 번 울리지 않는다.**
    """
    data = latest()
    if not data:
        return {"known": False, "total": 0, "new": []}
    pages = data.get("pages") or []
    now_ids = {p["page_id"] for p in pages if p.get("page_id")}
    seen = _seen_ids()
    fresh = [p for p in pages if p.get("page_id") and p["page_id"] not in seen]
    _remember(seen | now_ids)
    return {"known": True, "total": len(pages), "new": fresh,
            "at": data.get("at"), "updated_at": data.get("updated_at")}


def as_lines(pages: List[dict], limit: int = 30) -> List[str]:
    """사람이 바로 조치할 수 있는 형태 — 팀·제목·링크."""
    out = []
    for p in pages[:limit]:
        pid = str(p.get("page_id") or "").replace("-", "")
        title = (p.get("title") or "").strip() or "(제목 없음)"
        out.append(f"[{p.get('team') or '?'}] {title} — https://www.notion.so/{pid}")
    return out
