"""메일 발송 계층 — 2026-08-19 실측으로 길이 막혀 있음이 확인돼 **꺼진 채로** 만든다.

WAS·APP 양쪽에서 SMTP 25/587 차단, 로컬 MTA 없음, 프록시는 HTTP 전용이라 릴레이 불가.
IT 가 열어주면 `.env` 네 줄로 켜진다 — 코드 배포 없이.

⚠️ 이 테스트가 지키는 것은 두 가지다:
   ① 꺼져 있을 때 **아무 일도 하지 않는다** (실수로 나가지 않는다)
   ② 실패해도 **예외가 밖으로 나가지 않는다** — 메일 때문에 공유·피드백 처리가 깨지면 안 된다
"""

from __future__ import annotations

import pytest

from app.core import mailer


@pytest.fixture
def off(monkeypatch):
    class S:
        mail_enabled = False
        smtp_host = ""
        smtp_port = 587
        smtp_user = ""
        smtp_password = ""
        smtp_from = "no-reply@cravercorp.com"
        smtp_starttls = True
    monkeypatch.setattr(mailer, "get_settings", lambda: S())
    return S


@pytest.fixture
def on(monkeypatch):
    class S:
        mail_enabled = True
        smtp_host = "mail.internal"
        smtp_port = 587
        smtp_user = ""
        smtp_password = ""
        smtp_from = "no-reply@cravercorp.com"
        smtp_starttls = True
    monkeypatch.setattr(mailer, "get_settings", lambda: S())
    return S


def test_disabled_sends_nothing(off, monkeypatch):
    called = []
    monkeypatch.setattr(mailer.smtplib, "SMTP", lambda *a, **k: called.append(a))
    assert mailer.is_enabled() is False
    assert mailer.send("a@b.com", "제목", "본문") is False
    assert not called, "꺼져 있는데 SMTP 를 열었다"


def test_status_says_why_it_is_off(off):
    assert "IT" in mailer.status()      # 화면에서 이유를 알 수 있어야 한다


class _FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.sent, self.tls, self.logged_in = host, port, [], False, None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, u, p):
        self.logged_in = u

    def send_message(self, msg):
        self.sent.append(msg)


def test_enabled_sends_with_starttls(on, monkeypatch):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    assert mailer.send("me@craver.co", "제목", "본문") is True
    s = _FakeSMTP.instances[-1]
    assert (s.host, s.port) == ("mail.internal", 587)
    assert s.tls is True                      # 사내 릴레이라도 평문으로 보내지 않는다
    assert s.logged_in is None                # 인증 설정이 없으면 로그인하지 않는다
    msg = s.sent[0]
    assert msg["To"] == "me@craver.co"
    assert msg["From"] == "no-reply@cravercorp.com"   # 개인이 아니라 시스템 발신자


def test_failure_never_raises(on, monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(mailer.smtplib, "SMTP", boom)
    assert mailer.send("me@craver.co", "제목", "본문") is False   # 예외가 아니라 False


def test_invalid_address_is_ignored(on):
    assert mailer.send("", "제목", "본문") is False
    assert mailer.send("주소아님", "제목", "본문") is False


def test_share_and_feedback_use_the_same_words_as_the_app(on, monkeypatch):
    """메일과 화면이 다른 소리를 하면 "둘 중 뭐가 맞나" 는 신고가 들어온다."""
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    mailer.report_shared("me@craver.co", "임재필", "일본 매출 보고서", "http://10.1.100.5/x")
    body = _FakeSMTP.instances[-1].sent[0].get_content()
    assert "임재필" in body and "일본 매출 보고서" in body and "http://10.1.100.5/x" in body
    assert "지목된 사람만" in body          # 공유 원칙을 메일에도 적는다

    mailer.feedback_handled("me@craver.co", "수치가 이상합니다", "해결됨", "메가와리 기간 교정")
    body = _FakeSMTP.instances[-1].sent[0].get_content()
    assert "해결됨" in body and "메가와리 기간 교정" in body


def test_callers_swallow_mail_errors():
    """공유·피드백 처리는 메일이 실패해도 성공해야 한다."""
    import inspect

    from app.api import reports_api
    from app.core import feedback_inbox

    assert "share_mail_failed" in inspect.getsource(reports_api.add_shares)
    assert "feedback_mail_failed" in inspect.getsource(feedback_inbox.set_status)
