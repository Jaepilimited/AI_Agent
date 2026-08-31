"""셀라 알림 → 잔디. 출근 브리핑과 **같은 대기열·같은 릴레이**를 쓴다.

**왜 만들었나** (2026-08-26 실측):

    AD 500명 → 가입 62명 (12%) · 최근 7일 질문 17명 (전사의 3.4%)
    브리핑 334건 발송 → **27건 열람 (8.1%)**
    그런데 **열어본 날 질문 전환은 23.7%**

콘텐츠가 아니라 **도달**이 병목이다. 앱 안에만 두면 안 열린다 — 사람들이 이미 보고 있는
곳(잔디)으로 밀고, **매번 셀라로 돌아오는 문(링크)을 함께 준다.**

⛔ **잔디로는 그 사람 앞으로 온 것만 보낸다.** 사용자 지시(2026-08-26): "꼭 필요한
   내용만 전달해줘. 너무 많으면 스팸 같아". 여기서 갈리는 기준은 중요도가 아니라
   **수신자가 특정되는가** 다:

     보낸다   보고서 공유(누가 **나를** 지목했다) · 의견 회신(**내가** 물었고 답이 왔다)
     안 보낸다 공지(전원 방송) — 알림함 배지가 맡는다

   ⚠️ 공지가 특히 위험했다. 알림함을 한 번도 안 연 사람은 `announce_seen_at` 이 NULL 이라
      **최근 공지 10건이 전부 '안 읽음'** 이다 — 아무에게도 앞으로 오지 않은 방송이
      한 사람에게 최대 5통씩 꽂힌다. 개수를 줄이는 걸로는 못 고친다. 종류가 틀렸다.

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
MAX_PER_USER = 3

#: 잔디로 밀어 줄 나이 상한. ⛔ **알림함에는 이 창을 걸지 마라** — 회신은 나이와
#: 무관하게 닿아야 한다(CLAUDE.md). 하지만 잔디는 "지금 봐 달라"고 두드리는 채널이라
#: 석 달 된 회신을 울리는 것은 알림이 아니라 소음이다. 앱을 처음 여는 사람에게
#: 묵은 미읽음이 한꺼번에 터지는 것도 여기서 막는다.
PUSH_WINDOW_DAYS = 14


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
        "at": row.get("created_at"),
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
            "at": row.get("handled_at"),
            "seen": not bool(row.get("unseen")),
        })
    return out


def _announcements(user_id: int) -> list[dict[str, Any]]:
    """⛔ **잔디 대기열에서는 부르지 않는다** (`pending_items` 참조). 전원 방송이라
    받는 사람이 특정되지 않고, 알림함을 안 연 사람에게는 지난 공지가 통째로 '안 읽음'
    이라 한꺼번에 쏟아진다. 공지는 알림함 배지가 맡는다.

    남겨 둔 이유: 나중에 '중요 공지만' 같은 선택지를 붙일 자리이고, 지운 이유가
    코드에 남아 있어야 다음 사람이 무심코 되살리지 않는다.
    """
    from app.core import announcements

    rows = announcements.for_user(user_id) or []
    return [{
        "kind": "announcement",
        "dedup_key": f"announcement:{row.get('id') or row.get('title')}",
        "title": str(row.get("title") or "")[:120],
        "note": str(row.get("body") or "").strip(),
        "seen": not bool(row.get("unseen")),
    } for row in rows]


#: ⛔ 여기에 `_announcements` 를 더하지 마라 — 전원 방송은 잔디로 밀지 않는다.
#: 새 종류를 넣기 전에 물어라: **받는 사람이 특정되는가?** 아니면 알림함이 맡는다.
_PERSONAL_SOURCES = (_shares, _feedbacks)


def _fresh(item: dict[str, Any], now: datetime) -> bool:
    """너무 묵은 것은 밀지 않는다. 시각을 모르면 **보낸다** — 모른다고 버리면
    새 알림이 조용히 사라지고, 그게 여기서 가장 나쁜 실패다."""

    at = item.get("at")
    if not isinstance(at, datetime):
        return True
    return (now - at).days <= PUSH_WINDOW_DAYS


def pending_items(user_id: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """**그 사람 앞으로 온**, 아직 안 읽은, 너무 묵지 않은 것만.

    각 수집이 실패해도 나머지는 나간다 — 한 소스가 죽었다고 전부 멈추면
    답을 기다리던 회신까지 함께 묻힌다.
    """

    current = now or datetime.now()
    items: list[dict[str, Any]] = []
    for collect in _PERSONAL_SOURCES:
        try:
            items += [row for row in collect(user_id)
                      if not row.get("seen") and _fresh(row, current)]
        except Exception as exc:
            logger.warning("jandi_notify_source_failed",
                           source=collect.__name__, error_type=type(exc).__name__)
    # 넘치면 새것부터. 밀린 것 중 오래된 쪽을 먼저 울리면 급한 것이 뒤로 밀린다.
    items.sort(key=lambda row: row.get("at") or datetime.min, reverse=True)
    if len(items) > MAX_PER_USER:
        logger.info("jandi_notify_capped", user_id=user_id,
                    pending=len(items), sent=MAX_PER_USER)
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
            # ⛔ 브리핑 본문과 **같은 표기**를 쓴다 — 잔디는 마크다운 링크만 눌린다
            #    (2026-08-31 네 형태를 실제로 보내 확인). 한쪽만 고치면 알림에서만
            #    주소가 글자로 남아 눌리지 않는다.
            body += f"\n\n[셀라에서 이어서 보기]({link})"
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
