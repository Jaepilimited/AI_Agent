"""셀라 알림 → 잔디. 출근 브리핑과 **같은 대기열·같은 릴레이**를 쓴다.

**왜 만들었나** (2026-08-26 실측):

    AD 500명 → 가입 62명 (12%) · 최근 7일 질문 17명 (전사의 3.4%)
    브리핑 334건 발송 → **27건 열람 (8.1%)**
    그런데 **열어본 날 질문 전환은 23.7%**

콘텐츠가 아니라 **도달**이 병목이다. 앱 안에만 두면 안 열린다 — 사람들이 이미 보고 있는
곳(잔디)으로 밀고, **매번 셀라로 돌아오는 문(링크)을 함께 준다.**

⛔ 보내는 알림을 늘리지 마라. 매일 같은 알림은 곧 무시당하고, 그러면 정작 답을 기다리던
   공유·의견 회신까지 함께 묻힌다 (`briefing.py` 옵트아웃 주석과 같은 이유).
   그래서 **아직 안 읽은 것만**, 그리고 **한 번만** 보낸다 (`dedup_key`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import structlog

from app.config import get_settings
from app.core import jandi_briefing

logger = structlog.get_logger()

#: 한 번에 밀어 넣을 최대 건수. 밀린 알림이 한꺼번에 쏟아지면 그것도 소음이다.
MAX_PER_USER = 5


def base_url() -> str:
    return (get_settings().public_base_url or "").rstrip("/")


def link_for(kind: str, item: dict[str, Any]) -> str:
    """셀라로 돌아오는 문. ⚠️ 주소가 설정돼 있지 않으면 빈 문자열이다 (거짓 링크 금지)."""

    root = base_url()
    if not root:
        return ""
    if kind == "report_share" and item.get("report_id"):
        return f"{root}/api/reports/{item['report_id']}"
    return root


def _shares(user_id: int) -> list[dict[str, Any]]:
    from app.reports import store

    rows = store.list_notifications(user_id) or []
    return [{
        "kind": "report_share",
        "dedup_key": f"report_share:{row['report_id']}",
        "title": str(row.get("title") or row.get("question") or "보고서")[:120],
        "from_name": str(row.get("from_name") or ""),
        "report_id": row["report_id"],
        "seen": bool(row.get("seen_at")),
    } for row in rows]


def _feedbacks(user_id: int) -> list[dict[str, Any]]:
    from app.core import feedback_inbox

    rows = feedback_inbox.my_feedback(user_id) or []
    out = []
    for row in rows:
        # ⛔ 처리되지 않은 것은 보내지 않는다 — "접수됨" 을 잔디로 또 알리면 소음이다.
        if not row.get("handled_at"):
            continue
        out.append({
            "kind": "feedback",
            "dedup_key": f"feedback:{row['id']}",
            "title": str((row.get("comment") or "").strip()
                         or (row.get("question") or "").strip() or "이전 답변")[:120],
            "note": str(row.get("handled_note") or "").strip(),
            "seen": not bool(row.get("unseen")),
        })
    return out


def _announcements(user_id: int) -> list[dict[str, Any]]:
    from app.core import announcements

    rows = announcements.for_user(user_id) or []
    return [{
        "kind": "announcement",
        "dedup_key": f"announcement:{row.get('id') or row.get('title')}",
        "title": str(row.get("title") or "")[:120],
        "note": str(row.get("body") or "").strip(),
        "seen": not bool(row.get("unseen")),
    } for row in rows]


def pending_items(user_id: int) -> list[dict[str, Any]]:
    """아직 안 읽은 알림만. 각 수집이 실패해도 나머지는 나간다."""

    items: list[dict[str, Any]] = []
    for collect in (_shares, _feedbacks, _announcements):
        try:
            items += [row for row in collect(user_id) if not row.get("seen")]
        except Exception as exc:
            logger.warning("jandi_notify_source_failed",
                           source=collect.__name__, error_type=type(exc).__name__)
    return items[:MAX_PER_USER]


def render(item: dict[str, Any]) -> tuple[str, str]:
    """(제목, 본문). 잔디는 평문이라 줄바꿈과 들여쓰기만으로 위계를 만든다."""

    label = jandi_briefing.KIND_META.get(item["kind"], ("알림", ""))[0]
    lines = [label, ""]
    if item["kind"] == "report_share":
        who = item.get("from_name")
        lines.append(f"  {item['title']}")
        if who:
            lines.append(f"  보낸 사람 · {who}")
    elif item["kind"] == "feedback":
        lines.append(f"  \"{item['title']}\"")
        if item.get("note"):
            lines.append(f"  → {item['note']}")
    else:
        lines.append(f"  {item['title']}")
        if item.get("note"):
            lines.append(f"  {item['note'][:400]}")
    return item["title"], "\n".join(lines).strip()


def enqueue_for_user(user_id: int, webhook_url: str, today: date | None = None) -> int:
    """안 읽은 알림을 대기열에 넣는다. 이미 넣은 것은 `dedup_key` 가 막는다."""

    day = today or date.today()
    queued = 0
    for item in pending_items(user_id):
        title, body = render(item)
        link = link_for(item["kind"], item)
        if link:
            body += f"\n\n  이어서 물어보기 · {link}"
        if jandi_briefing.enqueue(
            user_id, day, webhook_url, body,
            kind=item["kind"], dedup_key=item["dedup_key"], title=title, link=link,
        ):
            queued += 1
    return queued


def run() -> dict[str, int]:
    """잔디를 등록한 사람들의 안 읽은 알림을 대기열에 넣는다 (발송은 DB_PC 릴레이)."""

    recipients = jandi_briefing.enabled_recipients()
    queued = 0
    for row in recipients:
        try:
            queued += enqueue_for_user(int(row["user_id"]), str(row["webhook_url"]))
        except Exception as exc:
            logger.warning("jandi_notify_user_failed", user_id=row.get("user_id"),
                           error_type=type(exc).__name__)
    logger.info("jandi_notify_done", recipients=len(recipients), queued=queued)
    return {"recipients": len(recipients), "queued": queued}
