"""잔디 출근 브리핑 — 사용자별 웹훅 등록 + DB_PC 릴레이용 내부 엔드포인트.

내부 엔드포인트의 방어선은 셋이다 (하나가 뚫려도 나머지가 막는다):
  1. `BRIEFING_RELAY_TOKEN` 이 비어 있으면 **경로 자체가 404** — 기본값으로 열려 있지 않다
  2. 토큰 상수시간 비교
  3. 호출자가 루프백이어야 한다 — 릴레이는 SSH 터널로 들어오므로 127.0.0.1 이다.
     nginx 를 거친 실사용자 요청은 여기에 닿지 못한다
"""

from __future__ import annotations

import hmac
from datetime import date, timedelta

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.auth_middleware import get_current_user
from app.config import get_settings
from app.core import jandi_briefing
from app.db.models import User

logger = structlog.get_logger()

router = APIRouter(tags=["jandi-briefing"])
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


# ── 사용자 설정 ──────────────────────────────────────────────────────────────

def _send_at_label(value) -> str:
    """저장된 시각을 화면 표기로. 읽다가 깨져도 첫 회차로 답한다 — 설정 화면이
    통째로 안 뜨는 것보다 낫다."""

    try:
        return jandi_briefing.normalize_send_at(value).strftime("%H:%M")
    except ValueError:
        return jandi_briefing.DEFAULT_SEND_AT.strftime("%H:%M")


@router.get("/api/personal-briefing/jandi")
async def get_my_webhook(user: User = Depends(get_current_user)) -> dict:
    # ⛔ 선택지는 **서버가 단일 소스**다. 프론트가 따로 목록을 갖고 있으면 릴레이
    #    회차가 바뀔 때 조용히 갈린다 (`@@` 데이터소스 목록이 갈렸던 그 사고).
    row = jandi_briefing.get_webhook(user.id)
    # ⛔ 항목 목록도 **서버가 단일 소스**다. 프론트에 사본을 두면 절이 하나 늘 때
    #    화면에서 통째로 사라진다 (`@@` 목록이 갈렸던 그 사고와 같은 부류).
    muted = jandi_briefing.parse_muted(row.get("muted_sections") if row else "")
    base = {
        "send_time_choices": jandi_briefing.SEND_TIME_CHOICES,
        "send_at": jandi_briefing.DEFAULT_SEND_AT.strftime("%H:%M"),
        "sections": [
            {"key": key, "label": label, "group": group, "enabled": key not in muted}
            for key, label, group in jandi_briefing.SECTIONS
        ],
    }
    if not row:
        return {**base, "registered": False, "enabled": False, "masked": "",
                "last_sent_at": "", "last_error": ""}
    return {
        **base,
        "registered": True,
        "enabled": bool(row["enabled"]),
        "send_at": _send_at_label(row.get("send_at")),
        # ⛔ 저장된 URL 을 그대로 돌려주지 않는다. 토큰이 곧 발송 권한이다.
        "masked": jandi_briefing.mask(str(row["webhook_url"])),
        "last_sent_at": str(row["last_sent_at"] or ""),
        "last_error": str(row["last_error"] or ""),
    }


@router.put("/api/personal-briefing/jandi")
async def put_my_webhook(
    payload: dict = Body(...), user: User = Depends(get_current_user),
) -> dict:
    url = str(payload.get("webhook_url", "")).strip()
    if not url:
        # ⛔ 시각만 바꾸려는 사람에게 주소를 다시 붙여넣게 하지 마라 — 서버는 주소를
        #    가려서 내려주므로(토큰이 곧 발송 권한) 사용자에게는 되붙일 방법이 없다.
        #    비워서 보내면 이미 저장된 주소를 그대로 쓴다.
        existing = jandi_briefing.get_webhook(user.id)
        if not existing:
            raise HTTPException(
                status_code=400,
                detail="먼저 잔디 인커밍 웹훅 주소를 등록해 주세요.",
            )
        url = str(existing["webhook_url"])
    if not jandi_briefing.is_valid_webhook(url):
        raise HTTPException(
            status_code=400,
            detail="잔디 인커밍 웹훅 주소만 등록할 수 있습니다 (https://wh.jandi.com/connect-api/webhook/…).",
        )
    # 시각은 여기서 한 번, `set_webhook` 에서 또 한 번 본다. 방어선을 줄이지 않는다 —
    # 여기가 있어야 사용자가 읽을 수 있는 이유를 돌려주고, 저쪽이 있어야 검증을
    # 빠뜨린 다른 호출부가 생겨도 막힌다.
    send_at = payload.get("send_at")
    if send_at is not None:
        try:
            send_at = jandi_briefing.normalize_send_at(send_at)
        except ValueError:
            first = jandi_briefing.SEND_TIME_CHOICES[0]
            last = jandi_briefing.SEND_TIME_CHOICES[-1]
            raise HTTPException(
                status_code=400,
                detail=f"받을 시각은 {first}~{last} 사이 30분 단위로만 고를 수 있습니다 "
                       "(그 시각에만 잔디로 꺼내 갑니다).",
            )
    # ⚠️ 키가 아예 없으면 **안 바꾼다** (None). 빈 목록을 보내는 것은 "전부 받기" 라
    #    뜻이 다르다 — 시각만 저장하는 호출이 설정을 지우면 안 된다.
    muted = payload.get("muted_sections")
    if muted is not None:
        unknown = [str(k) for k in muted
                   if str(k) not in jandi_briefing.SECTION_KEYS]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"받지 않을 항목에 모르는 값이 있습니다: {', '.join(unknown[:3])}",
            )
    jandi_briefing.set_webhook(
        user.id, url, enabled=bool(payload.get("enabled", True)), send_at=send_at,
        muted=muted,
    )
    if send_at is not None:
        # 아직 안 나간 오늘 몫도 함께 옮긴다 — 안 그러면 화면이 말하는 시각과
        # 실제 도착 시각이 오늘 하루 어긋난다.
        moved = jandi_briefing.reschedule_pending(user.id, send_at)
        if moved:
            logger.info("jandi_pending_rescheduled", user_id=user.id, rows=moved)
    logger.info("jandi_webhook_registered", user_id=user.id, send_at=str(send_at or ""))
    return await get_my_webhook(user)


@router.delete("/api/personal-briefing/jandi")
async def delete_my_webhook(user: User = Depends(get_current_user)) -> dict:
    jandi_briefing.delete_webhook(user.id)
    # ⚠️ 해제 응답에도 선택지를 담는다 — 안 담으면 해제 직후 시각 선택이 화면에서
    #    사라진다 (다시 등록하려는 사람이 고를 수단을 잃는다).
    return await get_my_webhook(user)


@router.post("/api/personal-briefing/jandi/test")
async def queue_test_message(user: User = Depends(get_current_user)) -> dict:
    """지금 대기열에 한 건 넣는다. 실제 발송은 DB_PC 릴레이가 하므로 즉시 도착하지는 않는다.

    ⛔ 여기에는 사용자가 고른 도착 시각을 붙이지 않는다 — 지금 되는지 보려고 누른
       것인데 8시간 뒤에 가면 확인이 되지 않는다. 다음 릴레이 회차에 나간다.
    """

    row = jandi_briefing.get_webhook(user.id)
    if not row or not row["enabled"]:
        raise HTTPException(status_code=400, detail="먼저 잔디 웹훅을 등록해 주세요.")
    from app.core.personal_briefing import get_cached_for_user

    cached = get_cached_for_user(user)
    body = str((cached.get("document") or {}).get("markdown", "")).strip()
    if not body:
        raise HTTPException(status_code=400, detail="아직 만들어진 브리핑이 없습니다. 먼저 첫 화면을 열어 주세요.")
    queued = jandi_briefing.enqueue(
        user.id, date.fromisoformat(cached["for_date"]), str(row["webhook_url"]), body,
    )
    return {"queued": bool(queued), "for_date": cached["for_date"]}


# ── 릴레이 (DB_PC 전용) ──────────────────────────────────────────────────────

def _require_relay(request: Request) -> None:
    token = (get_settings().briefing_relay_token or "").strip()
    if not token:
        # 설정하지 않았으면 이 경로는 존재하지 않는 것으로 취급한다.
        raise HTTPException(status_code=404, detail="Not Found")
    supplied = request.headers.get("x-relay-token", "")
    if not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=404, detail="Not Found")
    client = (request.client.host if request.client else "") or ""
    if client not in _LOOPBACK:
        logger.warning("relay_non_loopback_rejected", client=client)
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/api/internal/briefing-outbox")
async def relay_pending(request: Request, limit: int = 50) -> dict:
    _require_relay(request)
    rows = jandi_briefing.pending(min(max(1, limit), 100))
    return {"items": [{
        "id": int(row["id"]),
        "webhook_url": str(row["webhook_url"]),
        "body": str(row["body"]),
        "for_date": str(row["for_date"]),
        "kind": str(row.get("kind") or "briefing"),
        "title": str(row.get("title") or ""),
        "link": str(row.get("link") or ""),
        "attempts": int(row["attempts"]),
    } for row in rows]}


@router.post("/api/internal/fx")
async def relay_fx(request: Request, payload: dict = Body(...)) -> dict:
    """DB_PC 가 오늘 환율을 밀어 넣는다.

    ⛔ 서버는 환율 API 에 붙지 못한다 (프록시 화이트리스트에 없다, 2026-08-26 실측).
       잔디와 같은 이유·같은 방어선이다.
    """
    _require_relay(request)
    from app.core import fx_rates

    try:
        for_date = date.fromisoformat(str(payload.get("for_date", "")))
    except ValueError:
        raise HTTPException(status_code=400, detail="for_date must be YYYY-MM-DD")
    rows = payload.get("rates")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=400, detail="rates must be a non-empty list")
    saved = fx_rates.put(for_date, rows[:40], str(payload.get("source", "")))
    fx_rates.cleanup(for_date - timedelta(days=90))
    logger.info("fx_relay_saved", for_date=str(for_date), saved=saved)
    return {"saved": saved, "for_date": str(for_date)}


@router.post("/api/internal/briefing-outbox/ack")
async def relay_ack(request: Request, payload: dict = Body(...)) -> dict:
    _require_relay(request)
    sent = 0
    failed = 0
    for entry in (payload.get("results") or [])[:100]:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        if entry.get("ok"):
            jandi_briefing.mark_sent(int(entry["id"]))
            sent += 1
        else:
            jandi_briefing.mark_failed(int(entry["id"]), str(entry.get("error", "unknown")))
            failed += 1
    jandi_briefing.cleanup(date.today() - timedelta(days=14))
    logger.info("jandi_relay_ack", sent=sent, failed=failed)
    return {"sent": sent, "failed": failed}
