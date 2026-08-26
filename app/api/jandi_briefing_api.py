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

@router.get("/api/personal-briefing/jandi")
async def get_my_webhook(user: User = Depends(get_current_user)) -> dict:
    row = jandi_briefing.get_webhook(user.id)
    if not row:
        return {"registered": False, "enabled": False, "masked": "", "last_sent_at": "", "last_error": ""}
    return {
        "registered": True,
        "enabled": bool(row["enabled"]),
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
    if not jandi_briefing.is_valid_webhook(url):
        raise HTTPException(
            status_code=400,
            detail="잔디 인커밍 웹훅 주소만 등록할 수 있습니다 (https://wh.jandi.com/connect-api/webhook/…).",
        )
    jandi_briefing.set_webhook(user.id, url, enabled=bool(payload.get("enabled", True)))
    logger.info("jandi_webhook_registered", user_id=user.id)
    return await get_my_webhook(user)


@router.delete("/api/personal-briefing/jandi")
async def delete_my_webhook(user: User = Depends(get_current_user)) -> dict:
    jandi_briefing.delete_webhook(user.id)
    return {"registered": False, "enabled": False, "masked": "", "last_sent_at": "", "last_error": ""}


@router.post("/api/personal-briefing/jandi/test")
async def queue_test_message(user: User = Depends(get_current_user)) -> dict:
    """지금 대기열에 한 건 넣는다. 실제 발송은 DB_PC 릴레이가 하므로 즉시 도착하지는 않는다."""

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
