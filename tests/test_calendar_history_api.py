"""Calendar history must distinguish a complete result from a partial read."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core import google_workspace


SEOUL = timezone(timedelta(hours=9))
START = datetime(2025, 1, 1, tzinfo=SEOUL)
END = datetime(2026, 1, 1, tzinfo=SEOUL)


def _calendar_api(monkeypatch, pages):
    """Replace only the external API; route pages by the requested token."""
    calls = []

    class Request:
        def __init__(self, page):
            self.page = page

        def execute(self):
            if isinstance(self.page, Exception):
                raise self.page
            return deepcopy(self.page)

    class Calendar:
        def events(self):
            return self

        def list(self, **kwargs):
            calls.append(kwargs)
            return Request(pages[kwargs.get("pageToken")])

    monkeypatch.setattr(google_workspace, "build", lambda *_a, **_k: Calendar())
    return calls


def test_history_uses_exact_utc_bounds_and_retains_counting_metadata(monkeypatch):
    calls = _calendar_api(monkeypatch, {None: {"items": [{
        "id": "overlap",
        "summary": "Quarterly review",
        "start": {"dateTime": "2024-12-31T23:00:00+09:00"},
        "end": {"dateTime": "2025-01-01T01:00:00+09:00"},
        "status": "confirmed",
        "eventType": "default",
        "attendees": [
            {"email": "me@example.com", "self": True, "responseStatus": "accepted"},
            {"email": "room@example.com", "resource": True, "responseStatus": "accepted"},
        ],
        "attendeesOmitted": False,
        "organizer": {"email": "me@example.com", "self": True},
    }]}})

    result = google_workspace.list_calendar_history(object(), START, END)

    assert result["truncated"] is False
    assert result["items"][0]["id"] == "overlap"
    # Calendar overlap semantics remain visible; the caller counts by start.
    assert result["items"][0]["start"] == {"dateTime": "2024-12-31T23:00:00+09:00"}
    assert result["items"][0]["attendees"][0]["responseStatus"] == "accepted"
    assert result["items"][0]["attendees"][1]["resource"] is True
    assert result["items"][0]["attendeesOmitted"] is False
    assert result["items"][0]["organizer"]["self"] is True
    request = calls[0]
    assert request["calendarId"] == "primary"
    assert request["timeMin"] == "2024-12-31T15:00:00+00:00"
    assert request["timeMax"] == "2025-12-31T15:00:00+00:00"
    assert request["singleEvents"] is True
    assert request["orderBy"] == "startTime"
    assert request["showDeleted"] is False
    assert request["maxResults"] == 2500
    assert request["fields"] == (
        "nextPageToken,items(id,summary,start,end,status,eventType,"
        "attendees(email,self,resource,responseStatus),attendeesOmitted,"
        "organizer(email,self),recurringEventId,originalStartTime)"
    )


def test_history_reads_all_pages_without_merging_recurring_instances(monkeypatch):
    calls = _calendar_api(monkeypatch, {
        None: {"items": [
            {"id": "series_20250101", "recurringEventId": "series",
             "originalStartTime": {"dateTime": "2025-01-01T09:00:00+09:00"}},
            {"id": "single"},
        ], "nextPageToken": "second"},
        "second": {"items": [
            {"id": "single"},
            {"id": "series_20250108", "recurringEventId": "series",
             "originalStartTime": {"dateTime": "2025-01-08T09:00:00+09:00"}},
        ], "nextPageToken": "third"},
        "third": {"items": [{"id": "last"}]},
    })

    result = google_workspace.list_calendar_history(object(), START, END)

    assert [event["id"] for event in result["items"]] == [
        "series_20250101", "single", "series_20250108", "last",
    ]
    assert result["items"][2]["originalStartTime"] == {
        "dateTime": "2025-01-08T09:00:00+09:00",
    }
    assert result["truncated"] is False
    assert [call.get("pageToken") for call in calls] == [None, "second", "third"]
    assert {call["timeMin"] for call in calls} == {"2024-12-31T15:00:00+00:00"}
    assert {call["timeMax"] for call in calls} == {"2025-12-31T15:00:00+00:00"}


def test_history_continues_after_an_empty_page_with_a_token(monkeypatch):
    calls = _calendar_api(monkeypatch, {
        None: {"items": [{"id": "first"}], "nextPageToken": "empty"},
        "empty": {"items": [], "nextPageToken": "third"},
        "third": {"items": [{"id": "last"}]},
    })

    result = google_workspace.list_calendar_history(object(), START, END)

    assert [event["id"] for event in result["items"]] == ["first", "last"]
    assert result["truncated"] is False
    assert len(calls) == 3


@pytest.mark.parametrize("max_pages, expected_pages", [(-2, 1), (0, 1), (2, 2), (1000, 20)])
def test_history_marks_the_result_partial_when_the_page_limit_is_reached(
    monkeypatch, max_pages, expected_pages,
):
    pages = {
        (None if page == 0 else f"page-{page}"): {
            "items": [{"id": f"event-{page}"}], "nextPageToken": f"page-{page + 1}",
        }
        for page in range(21)
    }
    calls = _calendar_api(monkeypatch, pages)

    result = google_workspace.list_calendar_history(object(), START, END, max_pages=max_pages)

    assert len(result["items"]) == expected_pages
    assert result["truncated"] is True
    assert len(calls) == expected_pages


def test_history_does_not_mark_a_complete_last_page_partial(monkeypatch):
    _calendar_api(monkeypatch, {
        None: {"items": [{"id": "first"}], "nextPageToken": "last"},
        "last": {"items": [{"id": "last"}]},
    })

    result = google_workspace.list_calendar_history(object(), START, END, max_pages=2)

    assert result == {"items": [{"id": "first"}, {"id": "last"}], "truncated": False}


@pytest.mark.parametrize("repeated_token", ["second", "third"])
def test_history_stops_token_cycles_and_marks_the_result_partial(monkeypatch, repeated_token):
    calls = _calendar_api(monkeypatch, {
        None: {"items": [{"id": "first"}], "nextPageToken": "second"},
        "second": {"items": [{"id": "second"}], "nextPageToken": "third"},
        "third": {"items": [{"id": "third"}], "nextPageToken": repeated_token},
    })

    result = google_workspace.list_calendar_history(object(), START, END)

    assert [event["id"] for event in result["items"]] == ["first", "second", "third"]
    assert result["truncated"] is True
    assert len(calls) == 3


@pytest.mark.parametrize("fail_after_a_page", [False, True])
def test_history_propagates_api_failure_instead_of_returning_a_partial_count(
    monkeypatch, fail_after_a_page,
):
    failure = TimeoutError("calendar unavailable")
    pages = {None: failure}
    if fail_after_a_page:
        pages = {
            None: {"items": [{"id": "first"}], "nextPageToken": "failed"},
            "failed": failure,
        }
    _calendar_api(monkeypatch, pages)

    with pytest.raises(TimeoutError, match="calendar unavailable"):
        google_workspace.list_calendar_history(object(), START, END)


def test_history_can_return_a_confirmed_empty_calendar(monkeypatch):
    _calendar_api(monkeypatch, {None: {}})

    assert google_workspace.list_calendar_history(object(), START, END) == {
        "items": [], "truncated": False,
    }


@pytest.mark.parametrize("start, end", [
    (datetime(2025, 1, 1), END),
    (START, datetime(2026, 1, 1)),
    (START, START),
    (END, START),
    (START, datetime(2024, 12, 31, 15, tzinfo=timezone.utc)),
])
def test_history_rejects_naive_or_nonpositive_windows_before_api_access(monkeypatch, start, end):
    def unexpected_api(*_args, **_kwargs):
        pytest.fail("Invalid windows must not reach Google Calendar")

    monkeypatch.setattr(google_workspace, "build", unexpected_api)

    with pytest.raises(ValueError):
        google_workspace.list_calendar_history(object(), start, end)


def test_history_compares_real_instants_across_a_daylight_saving_fold(monkeypatch):
    calls = _calendar_api(monkeypatch, {None: {"items": []}})
    new_york = ZoneInfo("America/New_York")

    result = google_workspace.list_calendar_history(
        object(),
        datetime(2025, 11, 2, 1, 45, tzinfo=new_york, fold=0),
        datetime(2025, 11, 2, 1, 15, tzinfo=new_york, fold=1),
    )

    assert result["truncated"] is False
    assert calls[0]["timeMin"] == "2025-11-02T05:45:00+00:00"
    assert calls[0]["timeMax"] == "2025-11-02T06:15:00+00:00"
