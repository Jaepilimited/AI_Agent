from datetime import datetime
from zoneinfo import ZoneInfo

from app.core import google_workspace


SEOUL = ZoneInfo("Asia/Seoul")


def test_digest_uses_metadata_not_full_body(monkeypatch):
    calls = []

    class Request:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class Messages:
        def list(self, **kwargs):
            calls.append(("list", kwargs))
            return Request({"messages": [{"id": "m1", "threadId": "t1"}], "nextPageToken": "more"})

        def get(self, **kwargs):
            calls.append(("get", kwargs))
            return Request({
                "id": "m1",
                "threadId": "t1",
                "internalDate": "1787619600000",
                "labelIds": ["INBOX", "UNREAD"],
                "snippet": "결재 확인 부탁드립니다.",
                "payload": {"headers": [
                    {"name": "Subject", "value": "결재 요청"},
                    {"name": "From", "value": "Sender <sender@example.com>"},
                ]},
            })

    class Users:
        def __init__(self, messages):
            self._messages = messages

        def messages(self):
            return self._messages

    class Service:
        def __init__(self, messages):
            self._users = Users(messages)

        def users(self):
            return self._users

    monkeypatch.setattr(google_workspace, "build", lambda *_a, **_k: Service(Messages()))
    result = google_workspace.list_gmail_digest(
        object(),
        datetime(2026, 8, 25, 0, 0, tzinfo=SEOUL),
        datetime(2026, 8, 26, 0, 0, tzinfo=SEOUL),
    )

    get_call = next(kwargs for kind, kwargs in calls if kind == "get")
    assert get_call["format"] == "metadata"
    assert get_call["metadataHeaders"] == ["Subject", "From", "Date"]
    assert result["truncated"] is True
    assert result["items"][0]["unread"] is True
    assert "body" not in result["items"][0]

    list_call = next(kwargs for kind, kwargs in calls if kind == "list")
    assert list_call["maxResults"] == 20
    assert "after:1787583600" in list_call["q"]
    assert "before:1787670000" in list_call["q"]
    for exclusion in ("-in:spam", "-in:trash", "-in:drafts", "-in:sent", "-from:me"):
        assert exclusion in list_call["q"]


def test_calendar_window_uses_exact_bounds_and_reports_truncation(monkeypatch):
    captured = {}

    class Request:
        def execute(self):
            return {"items": [{
                "id": "e1",
                "summary": "종일 행사",
                "start": {"date": "2026-08-25"},
                "end": {"date": "2026-08-26"},
                "htmlLink": "https://www.google.com/calendar/event?eid=e1",
            }], "nextPageToken": "more"}

    class Events:
        def list(self, **kwargs):
            captured.update(kwargs)
            return Request()

    class CalendarService:
        def events(self):
            return Events()

    monkeypatch.setattr(google_workspace, "build", lambda *_a, **_k: CalendarService())
    start = datetime(2026, 8, 25, 0, 0, tzinfo=SEOUL)
    end = datetime(2026, 9, 1, 0, 0, tzinfo=SEOUL)
    result = google_workspace.list_calendar_window(object(), start, end)

    assert captured["timeMin"].startswith("2026-08-24T15:00:00")
    assert captured["timeMax"].startswith("2026-08-31T15:00:00")
    assert captured["maxResults"] == 50
    assert captured["singleEvents"] is True
    assert captured["orderBy"] == "startTime"
    assert result["truncated"] is True


def test_digest_clamps_requested_limit_to_twenty(monkeypatch):
    captured = {}

    class Request:
        def execute(self):
            return {"messages": []}

    class Messages:
        def list(self, **kwargs):
            captured.update(kwargs)
            return Request()

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(google_workspace, "build", lambda *_a, **_k: Service())
    google_workspace.list_gmail_digest(
        object(),
        datetime(2026, 8, 25, 0, 0, tzinfo=SEOUL),
        datetime(2026, 8, 26, 0, 0, tzinfo=SEOUL),
        max_results=999,
    )

    assert captured["maxResults"] == 20
