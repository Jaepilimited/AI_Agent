# -*- coding: utf-8 -*-
"""메일 발송 — 사내 SMTP 릴레이.

⛔ **지금은 길이 막혀 있다** (2026-08-19 실측): WAS·APP 양쪽에서 SMTP 25/587 이
   차단돼 있고 로컬 MTA 도 없다. 프록시(10.1.50.2:3128)는 HTTP 전용이라 TCP 릴레이가
   안 된다. IT 가 방화벽을 열고 발신 계정을 주면 그때 켠다.

그래서 이 모듈은 **설정이 없으면 아무 일도 하지 않는다**(no-op). 열리는 날 `.env` 에
네 줄을 넣고 재기동하면 그대로 나간다 — 배포 없이 켜진다.

    MAIL_ENABLED=true
    SMTP_HOST=<릴레이 주소>
    SMTP_PORT=587
    SMTP_FROM=no-reply@cravercorp.com
    # 인증이 필요하면
    SMTP_USER=... / SMTP_PASSWORD=...

⚠️ **메일 실패가 본 기능을 막으면 안 된다.** 공유가 걸렸는데 메일이 안 나갔다고
   공유까지 실패하면 더 나쁘다. 여기서 나는 예외는 전부 삼키고 WARNING 으로 남긴다
   (⛔ 단 원문을 남긴다 — 조용히 삼키면 왜 안 갔는지 영영 모른다).

⚠️ 개인 알림은 **시스템 발신자**(no-reply)로 보낸다. Gmail API(`gmail.send`)로 보내면
   메일이 그 사람 계정에서 나가고, 스코프가 늘어 **연결한 사람 전원이 재동의**해야 한다.
   Gmail 은 나중에 "AI 가 내 메일 초안을 써서 보낸다"는 생산성 기능에서 쓸 자리다.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


def is_enabled() -> bool:
    s = get_settings()
    return bool(getattr(s, "mail_enabled", False) and getattr(s, "smtp_host", ""))


def status() -> str:
    """자가 점검·System Status 에 쓸 한 줄 상태."""
    s = get_settings()
    if not getattr(s, "mail_enabled", False):
        return "꺼짐 (SMTP 미개방 — IT 요청 대기)"
    if not getattr(s, "smtp_host", ""):
        return "설정 불완전 (SMTP_HOST 없음)"
    return f"{s.smtp_host}:{s.smtp_port}"


def send(to: str, subject: str, body: str, html: Optional[str] = None) -> bool:
    """한 통 보낸다. 꺼져 있거나 실패하면 False — **예외를 밖으로 내지 않는다**."""
    if not to or "@" not in to:
        return False
    if not is_enabled():
        logger.info("mail_skipped_disabled", to=to[:40], subject=subject[:60])
        return False

    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from or s.smtp_user or "no-reply@localhost"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(s.smtp_host, int(s.smtp_port), timeout=10) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        logger.info("mail_sent", to=to[:40], subject=subject[:60])
        return True
    except Exception as e:
        # ⛔ 원문을 남긴다. INFO 는 프로덕션에서 통째로 버려진다
        logger.warning("mail_send_failed", to=to[:40], subject=subject[:60],
                       error=f"{type(e).__name__}: {str(e)[:200]}")
        return False


# ── 알림 문구 — 앱 알림함과 **같은 말**을 쓴다 ──────────────────────────────
# 두 곳에서 문구가 갈리면 "메일과 화면이 다른 소리를 한다" 는 신고가 들어온다.

def report_shared(to: str, from_name: str, title: str, url: str) -> bool:
    return send(
        to,
        f"[Cella] {from_name}님이 보고서를 공유했습니다 — {title}",
        f"{from_name}님이 '{title}' 보고서를 공유했습니다.\n\n"
        f"열기: {url}\n\n"
        "지목된 사람만 열 수 있습니다. 사내 네트워크에서 접속해 주세요.\n"
        "— Cella (회신하지 마세요)",
    )


def feedback_handled(to: str, what: str, status_label: str, note: str, ) -> bool:
    body = (f"남겨주신 피드백이 '{status_label}' 처리되었습니다.\n\n"
            f"내용: {what}\n")
    if note:
        body += f"처리 내용: {note}\n"
    body += "\n앱의 사이드바 [알림] 에서도 확인할 수 있습니다.\n— Cella (회신하지 마세요)"
    return send(to, f"[Cella] 피드백 처리 안내 — {status_label}", body)
