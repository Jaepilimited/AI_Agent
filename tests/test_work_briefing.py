"""출근 브리핑 — 근무일 창 · 사실 검증 · 잔디 전달 경계.

여기서 지키는 것은 전부 **조용히 틀리는** 종류다: 창이 어긋나도 에러가 없고,
LLM 이 숫자를 지어내도 표는 멀쩡해 보이며, 잘못된 웹훅 주소도 저장은 된다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import jandi_briefing, work_briefing, workday

SEOUL = ZoneInfo("Asia/Seoul")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=SEOUL)


# ── 근무일 창 ────────────────────────────────────────────────────────────────

def test_weekday_window_starts_at_yesterday_evening():
    start, _end, meta = workday.mail_window(at("2026-08-25T09:00:00"), frozenset())
    assert start.isoformat() == "2026-08-24T18:00:00+09:00"
    assert meta["span_days"] == 1
    assert meta["multi_day"] is False


def test_monday_window_reaches_back_to_friday_evening():
    """월요일에 어제(일요일)만 보면 주말 내내 온 메일이 통째로 빠진다."""
    start, _end, meta = workday.mail_window(at("2026-08-24T09:00:00"), frozenset())
    assert start.isoformat() == "2026-08-21T18:00:00+09:00"
    assert meta["span_days"] == 3
    assert meta["multi_day"] is True


def test_day_after_holiday_skips_the_holiday_too():
    holiday = date(2026, 8, 17)  # 월요일이 공휴일이면 금요일 퇴근까지 거슬러 간다
    start, _end, meta = workday.mail_window(at("2026-08-18T09:00:00"), frozenset({holiday}))
    assert start.isoformat() == "2026-08-14T18:00:00+09:00"
    assert meta["skipped"] == ["2026-08-17"]


def test_unknown_holidays_are_disclosed_not_guessed():
    """⛔ 공휴일을 확인 못 했으면 조용히 넘기지 않고 본문에 적는다."""
    _start, _end, meta = workday.mail_window(
        at("2026-08-25T09:00:00"), frozenset(), holiday_source="fallback",
    )
    assert meta["holidays_known"] is False
    assert "공휴일 확인 실패" in meta["label"]


def test_window_start_always_precedes_now():
    for text in ("2026-08-24T07:00:00", "2026-08-25T23:00:00", "2026-08-22T09:00:00"):
        start, end, _meta = workday.mail_window(at(text), frozenset())
        assert start < end, text


# ── 숫자 검증 ────────────────────────────────────────────────────────────────

def test_numbers_absent_from_the_source_are_flagged():
    haystack = "8월 예상 매출 794억 원, 마케팅 예산 525억 확정"
    assert work_briefing.unsupported_numbers("예산 525억 확정안", haystack) == []
    assert work_briefing.unsupported_numbers("영업이익률 40.7%", haystack) == ["40.7"]


def test_dates_counts_and_years_pass_without_a_source():
    """날짜·개수·연도까지 막으면 검출력이 아니라 소음이 된다."""
    assert work_briefing.unsupported_numbers("2026년 8월 25일 3건", "") == []


def test_comma_formatting_does_not_create_a_false_positive():
    assert work_briefing.unsupported_numbers("1,250건", "총 1250건 접수") == []


# ── 문서 구성 ────────────────────────────────────────────────────────────────

def sample():
    events = [{
        "id": "e1", "title": "본부장 월간회의",
        "start": "2026-08-25T10:00:00+09:00", "end": "2026-08-25T12:00:00+09:00",
        "location": "Creation (3F)", "attendees": ["ryankwon", "sckang"],
        "url": "https://calendar.google.com/e1", "conference_url": "", "ended": False,
        "description": "8월 예상 매출 794억 점검",
    }, {
        "id": "e9", "title": "내일 회의",
        "start": "2026-08-26T10:00:00+09:00", "end": "2026-08-26T11:00:00+09:00",
        "location": "", "attendees": [], "url": "", "conference_url": "", "ended": False,
    }]
    mails = [{
        "id": "m1", "from_display": "이해인", "subject": "예산 시트 공유",
        "snippet": "8월 28일까지 작성 부탁드립니다.",
        "received_at": "2026-08-25T08:10:00+09:00", "unread": True,
        "url": "https://mail.google.com/m1",
    }]
    return events, mails


def compose(raw):
    events, mails = sample()
    return work_briefing.compose(
        day=date(2026, 8, 25), now=at("2026-08-25T09:00:00"),
        events=events, mails=mails, window={"label": "어제 18:00 이후"}, raw=raw,
    )


def test_only_today_events_reach_the_document():
    document = compose({})
    assert [row["id"] for row in document["meetings"]] == ["e1"]


def test_items_without_a_real_source_id_are_dropped():
    document = compose({
        "actions": [
            {"text": "시트 입력", "source": "mail", "source_id": "m1"},
            {"text": "유령 작업", "source": "mail", "source_id": "nope"},
        ],
        "deadlines": [{"date": "2026-08-28", "text": "시트 제출",
                       "source": "mail", "source_id": "ghost"}],
        "mail_points": [{"message_id": "ghost", "points": ["없는 메일"], "request": "확인"}],
    })
    assert [row["text"] for row in document["actions"]] == ["시트 입력"]
    assert document["deadlines"] == []
    # ⚠️ 안 읽은 메일은 **요약거리가 없어도** 목록에 오른다 (2026-08-26 규칙 변경).
    #    버려야 하는 것은 LLM 이 지어낸 **문장**이지 실재하는 메일이 아니다 —
    #    행은 남되 그 안에 LLM 이 쓴 글자는 하나도 없어야 한다.
    assert [row["id"] for row in document["mail"]] == ["m1"]
    assert document["mail"][0]["points"] == []
    assert document["mail"][0]["request"] == ""
    assert document["dropped"] == 3


def test_invented_numbers_take_the_whole_sentence_with_them():
    document = compose({
        "meetings": [{"event_id": "e1", "prep": "이익률 40.7% 시뮬레이션 공유"}],
        "mail_summary": "총 9,900억 규모입니다.",
    })
    assert document["meetings"][0]["prep"] == ""
    assert document["mail_summary"] == ""
    assert document["dropped"] == 2


def test_imminent_meeting_is_urgent_regardless_of_the_model():
    document = compose({"meetings": [{"event_id": "e1", "prep": "", "urgency": "normal"}]})
    assert document["meetings"][0]["urgency"] == "high"


def test_a_deadline_today_or_tomorrow_is_urgent_regardless_of_the_model():
    document = compose({"deadlines": [
        {"date": "2026-08-26", "text": "시트 제출", "source": "mail",
         "source_id": "m1", "urgency": "normal"},
    ]})
    assert document["deadlines"][0]["urgency"] == "high"
    assert document["deadlines"][0]["label"] == "내일"


def test_deadlines_outside_a_sane_horizon_are_dropped():
    far = (date(2026, 8, 25) + timedelta(days=200)).isoformat()
    document = compose({"deadlines": [
        {"date": far, "text": "먼 미래", "source": "mail", "source_id": "m1"},
        {"date": "엉망", "text": "파싱 실패", "source": "mail", "source_id": "m1"},
    ]})
    assert document["deadlines"] == []


def test_urgent_marks_are_capped_so_they_keep_meaning():
    document = compose({
        "actions": [
            {"text": f"할 일 {index}", "source": "mail", "source_id": "m1", "urgency": "high"}
            for index in range(6)
        ],
    })
    high = [row for row in document["actions"] if row["urgency"] == "high"]
    assert document["urgent"] == work_briefing.MAX_URGENT
    # 회의 1건이 규칙으로 이미 긴급이므로 액션에 남는 긴급은 그만큼 줄어든다.
    assert len(high) == work_briefing.MAX_URGENT - 1


def test_markdown_carries_every_section_that_has_content():
    """절이 **버그로** 사라지지 않는지 본다 (원래 이 테스트의 의도).

    ⛔ 빈 절까지 싣도록 못 박지 않는다 — 잔디는 스크롤이 없는 평문 매체라
       "없습니다" 네 줄이면 정작 볼 것이 화면 밖으로 밀린다 (2026-08-31 사용자 요청:
       "잔디에서 받는 양식을 좀 가독성 있게"). 실측으로 30줄 중 8줄이 빈 절이었다.
       첫 화면에는 이미 같은 규칙이 있었고 본문에만 빠져 있었다.
    """
    filled = compose({
        "actions": [{"text": "예산 시트 입력", "source": "mail",
                     "source_id": "m1", "urgency": "high"}],
        "deadlines": [{"date": "2026-08-28", "text": "시트 제출",
                       "source": "mail", "source_id": "m1", "urgency": "normal"}],
    })
    text = work_briefing.render_markdown(filled)
    for heading in ("오늘의 일정", "수신 메일", "Action Item", "마감·기한"):
        assert heading in text, heading
    assert "어제 18:00 이후" in text


def test_empty_sections_are_left_out_of_the_message():
    """⚠️ 내용이 없으면 제목도 싣지 않는다 — 빈 줄과 제목이 본문을 밀어낸다."""
    text = work_briefing.render_markdown(compose({}))
    assert "오늘의 일정" in text          # 내용이 있는 절은 그대로
    assert "Action Item" not in text     # 없는 절은 통째로 빠진다
    assert "마감·기한" not in text
    assert "없습니다" not in text
    # 조건부 절이 남긴 빈 줄도 한 줄로 접힌다 — 평문 매체에서 빈 줄은 위계를 만드는
    # 유일한 수단이라 낭비하면 안 된다.
    assert chr(10) * 3 not in text


def test_document_survives_a_dead_model():
    document = compose(None)
    assert document["status"] == "ready"
    assert [row["id"] for row in document["meetings"]] == ["e1"]
    assert document["mail_total"] == 1


# ── 잔디 경계 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://wh.jandi.com/connect-api/webhook/11320800/7c1bdd4a0947be10377703affd57e97a",
])
def test_valid_jandi_webhooks_are_accepted(url):
    assert jandi_briefing.is_valid_webhook(url) is True


@pytest.mark.parametrize("url", [
    "",
    "http://wh.jandi.com/connect-api/webhook/1/abc",
    "https://evil.example.com/connect-api/webhook/1/abc",
    "https://wh.jandi.com.evil.com/connect-api/webhook/1/abc",
    "https://wh.jandi.com/connect-api/webhook/1/abc?x=https://evil",
    "https://wh.jandi.com/other/1/abc",
])
def test_non_jandi_targets_are_refused(url):
    """⛔ 임의 URL 을 받으면 개인 메일 요약을 아무 데나 보내주는 발송기가 된다."""
    assert jandi_briefing.is_valid_webhook(url) is False


def test_stored_webhook_is_never_echoed_back_in_full():
    url = "https://wh.jandi.com/connect-api/webhook/11320800/7c1bdd4a0947be10377703affd57e97a"
    masked = jandi_briefing.mask(url)
    assert "7c1bdd4a0947be10377703affd57e97a" not in masked
    assert masked.startswith("…/7c1b")


# ── 릴레이 내부 엔드포인트 방어선 ─────────────────────────────────────────────

class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, token, host="127.0.0.1"):
        self.headers = {"x-relay-token": token} if token is not None else {}
        self.client = _FakeClient(host)


def _relay(monkeypatch, configured):
    from app.api import jandi_briefing_api as api
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_relay_token", configured, raising=False)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    return api


def test_relay_is_closed_when_no_token_is_configured(monkeypatch):
    """⛔ 기본값으로 열려 있지 않다 — 설정하지 않았으면 경로 자체가 없다."""
    from fastapi import HTTPException

    api = _relay(monkeypatch, "")
    with pytest.raises(HTTPException) as caught:
        api._require_relay(_FakeRequest("anything"))
    assert caught.value.status_code == 404


def test_relay_rejects_a_wrong_token(monkeypatch):
    from fastapi import HTTPException

    api = _relay(monkeypatch, "correct-token")
    with pytest.raises(HTTPException) as caught:
        api._require_relay(_FakeRequest("wrong-token"))
    assert caught.value.status_code == 404


def test_relay_rejects_a_correct_token_from_off_box(monkeypatch):
    """릴레이는 SSH 터널로 들어온다 — nginx 를 거친 요청은 여기 닿으면 안 된다."""
    from fastapi import HTTPException

    api = _relay(monkeypatch, "correct-token")
    with pytest.raises(HTTPException) as caught:
        api._require_relay(_FakeRequest("correct-token", host="10.1.100.5"))
    assert caught.value.status_code == 404


def test_relay_accepts_the_tunnelled_call(monkeypatch):
    api = _relay(monkeypatch, "correct-token")
    assert api._require_relay(_FakeRequest("correct-token")) is None


# ── 환율 (2026-08-26) ────────────────────────────────────────────────────────

def _fx(**overrides):
    fx = {
        "status": "ready", "for_date": "2026-08-26", "stale_days": 0,
        "items": [
            {"currency": "USD", "unit": 1, "krw": 1383.12, "change_pct": 0.31},
            {"currency": "JPY", "unit": 100, "krw": 868.54, "change_pct": -0.12},
            {"currency": "CNY", "unit": 1, "krw": 205.30, "change_pct": None},
        ],
    }
    fx.update(overrides)
    return fx


def test_exchange_rates_ride_along_in_the_jandi_body():
    text = work_briefing.render_markdown(compose({}), fx=_fx())
    assert "환율" in text
    assert "USD 1,383원" in text
    assert "JPY(100) 869원" in text


def test_a_missing_rate_change_is_left_blank_not_zero():
    """⛔ 안 움직인 것과 모르는 것은 다르다 — 모를 때 0% 라고 쓰면 거짓말이다."""
    from app.core import fx_rates

    assert fx_rates.change_label({"change_pct": None}) == ""
    assert fx_rates.change_label({"change_pct": 0}) == "보합"
    assert fx_rates.change_label({"change_pct": -0.12}) == "▼ 0.12%"


def test_a_stale_rate_says_how_old_it_is():
    """주말·공휴일엔 새 고시가 없다 — 조용히 어제 값을 오늘 값처럼 보이면 안 된다."""
    text = work_briefing.render_markdown(compose({}), fx=_fx(stale_days=3, for_date="2026-08-23"))
    assert "3일 전 고시" in text


def test_no_rates_means_no_section_at_all():
    """빈 칸은 0원처럼 읽힌다 — 값이 없으면 줄 자체를 넣지 않는다."""
    assert work_briefing._fx_lines(None) == []
    assert work_briefing._fx_lines({"items": []}) == []
    assert "환율" not in work_briefing.render_markdown(compose({}), fx=None)


def test_jpy_is_quoted_per_hundred():
    """1엔으로 적으면 자릿수가 낯설다 — 한국 고시 관행은 100엔이다."""
    from app.core import fx_rates

    assert fx_rates.UNITS["JPY"] == 100
    assert fx_rates.label({"currency": "JPY", "unit": 100, "krw": 868.54}) == "JPY(100) 869원"
    assert fx_rates.label({"currency": "USD", "unit": 1, "krw": 1383.12}) == "USD 1,383원"


def test_the_two_currency_lists_never_drift():
    """⛔ 릴레이(DB_PC 단독 실행)와 앱이 통화 목록을 각자 갖는다 — 불가피한 사본이다.

    한쪽만 늘리면 **에러 없이** 새 통화가 화면에 안 뜬다. 릴레이가 안 보내면 값이 없고,
    앱이 모르면 보내도 안 그린다. 둘 다 조용하다 — 그래서 여기서 대조한다.
    """
    import re
    from pathlib import Path

    from app.core import fx_rates

    relay = (Path(__file__).resolve().parent.parent
             / "scripts" / "fx_relay.py").read_text(encoding="utf-8")
    currencies = re.search(r"CURRENCIES = \(([^)]*)\)", relay).group(1)
    units = re.search(r"UNITS = \{([^}]*)\}", relay).group(1)

    assert re.findall(r'"(\w+)"', currencies) == list(fx_rates.CURRENCIES)
    assert dict(
        (name, int(value)) for name, value in re.findall(r'"(\w+)":\s*(\d+)', units)
    ) == fx_rates.UNITS


def test_tiny_currencies_are_quoted_per_hundred():
    """1루피아는 0.078원이다 — 그대로 적으면 0 처럼 보인다."""
    from app.core import fx_rates

    assert fx_rates.UNITS.get("IDR") == 100
    assert fx_rates.label({"currency": "IDR", "unit": 100, "krw": 8.53}) == "IDR(100) 8.53원"


# ── 지표 두 줄 (2026-08-26) ──────────────────────────────────────────────────

def _business():
    return {"status": "ready", "items": [
        {"kind": "sales", "for_date": "2026-08-24",
         "title": "전사 주시 · 미국 하락 33.7억 → 16.4억 (-51%)",
         "body": "· 주의: 미국 33.7억 → 16.4억\n· 기회: 인도네시아 8.2억 → 18.1억\n"
                 "· 전사 전체 · 8/18~8/24 합계 104.0억"},
        {"kind": "marketing", "for_date": "2026-08-25",
         "title": "광고비 4.0억 · 직전 7일 대비 -47%",
         "body": "8/19~8/25 · 클릭 3,061,489회"},
    ]}


def test_metrics_reach_the_jandi_body_as_two_entries():
    text = work_briefing.render_markdown(compose({}), business=_business())
    assert "지표" in text
    assert "미국 하락" in text
    assert "광고비 4.0억" in text


def test_metric_body_lines_are_not_glued_into_one():
    """⛔ 줄을 이으면 `· ·` 가 생기고 네 줄 설명이 한 줄로 뭉쳐 읽히지 않는다."""
    text = work_briefing.render_markdown(compose({}), business=_business())
    assert "· ·" not in text
    assert "      주의: 미국 33.7억 → 16.4억" in text
    assert "      기회: 인도네시아 8.2억 → 18.1억" in text


def test_no_metrics_means_no_section():
    assert work_briefing._business_lines(None) == []
    assert work_briefing._business_lines({"items": []}) == []
    assert "지표" not in work_briefing.render_markdown(compose({}), business=None)


def test_rates_show_where_they_came_from_not_just_the_percent():
    """`1,283 → 1,383원` (2026-08-26 사용자 요청) — 변동률만 있으면 폭을 알 수 없다."""
    from app.core import fx_rates

    assert fx_rates.label({
        "currency": "USD", "unit": 1, "krw": 1383.12, "was_krw": 1283.0,
    }) == "USD 1,283 → 1,383원"
    assert fx_rates.label({
        "currency": "JPY", "unit": 100, "krw": 868.54, "was_krw": 891.8,
    }) == "JPY(100) 892 → 869원"


def test_an_unchanged_rate_drops_the_arrow():
    """⚠️ 반올림 후 두 값이 같으면 `1,614 → 1,614원` 이 된다 — 소음이다."""
    from app.core import fx_rates

    assert fx_rates.label({
        "currency": "EUR", "unit": 1, "krw": 1614.08, "was_krw": 1614.2,
    }) == "EUR 1,614원"


def test_a_rate_without_a_basis_shows_only_today():
    from app.core import fx_rates

    assert fx_rates.label({
        "currency": "IDR", "unit": 100, "krw": 7.81, "was_krw": None,
    }) == "IDR(100) 7.81원"


def test_a_far_basis_is_not_called_a_month_over_month():
    """⛔ 실제로 견준 날이 '한 달 전'에서 멀면 전월대비라고 부르지 마라.

    8/26 의 전월대비 목표는 7/26 인데 그날이 **일요일**이라 7/24(금)로 떨어진다 —
    이건 정상이다. 그런데 보유일이 드물면 그 간격이 몇 주가 되고, 그때도 "전월대비"
    라고 적으면 **에러 없이 이름만 틀린다** (2026-08-26 사용자 질문에서 드러났다).
    """
    from datetime import date as _date

    from app.core import fx_rates

    # 주말 때문에 이틀 밀린 것 — 전월대비가 맞다.
    assert fx_rates.basis_note(_date(2026, 7, 24), _date(2026, 7, 26)) == "전월대비 2026-07-24"
    # 한 달 가까이 벌어진 것 — 숫자는 진짜지만 이름은 아니다.
    assert fx_rates.basis_note(_date(2026, 7, 24), _date(2026, 8, 23)) == "2026-07-24 대비"
    assert fx_rates.basis_note(None, _date(2026, 7, 26)) == ""


def test_month_before_lands_on_the_same_day_number():
    """8/26 의 한 달 전은 7/26 이다 (사용자 확인). timedelta(30) 로 때우면 어긋난다."""
    from datetime import date as _date

    from app.core import fx_rates

    assert fx_rates.month_before(_date(2026, 8, 26)) == _date(2026, 7, 26)
    # 없는 날짜는 말일로 당긴다.
    assert fx_rates.month_before(_date(2026, 3, 31)) == _date(2026, 2, 28)


def test_the_basis_wording_comes_from_the_server_only():
    """⛔ 화면·잔디가 '전월대비' 를 각자 조립하지 마라 — 진짜 한 달 전인지는 서버만 안다."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    js = (root / "app" / "frontend" / "personal-briefing.js").read_text(encoding="utf-8")
    md = (root / "app" / "core" / "work_briefing.py").read_text(encoding="utf-8")
    for source in (js, md):
        assert "basis_note" in source
        # ⚠️ 주석에는 있어도 된다 (왜 그렇게 했는지 적혀 있다). 코드에만 없으면 된다.
        code = [
            line for line in source.splitlines()
            if not line.lstrip().startswith(("//", "#", "*", "/*"))
        ]
        assert "전월대비" not in chr(10).join(code), [
            line.strip() for line in code if "전월대비" in line
        ]


# ── ROAS (2026-08-26) ────────────────────────────────────────────────────────

def _mkt(**slot):
    from datetime import date as _date

    base = {"now_cost": 2.5e8, "prev_cost": 2.0e8, "now_conv": 7.5e8, "prev_conv": 5.0e8}
    base.update(slot)
    return {"base": _date(2026, 8, 25), "cur_from": _date(2026, 8, 19),
            "prev_from": _date(2026, 8, 12), "prev_to": _date(2026, 8, 18),
            "by_team": {}, "by_country": {}, "all": base}


def test_roas_falls_back_to_the_total_when_there_is_nothing_to_rank():
    """국가별로 나눌 것이 없으면 합계 하나를 낸다 — 없는 순위를 지어내지 않는다."""
    from app.core import briefing

    item = briefing.roas_item("전사", _mkt())
    assert "ROAS 3.00" in item["title"]
    # ⛔ 실매출이 아니라는 사실이 본문에 남아야 한다.
    assert "플랫폼 자체 집계값" in item["body"]


def test_roas_names_the_best_and_worst_place():
    """합계 하나로는 **어디를 손댈지** 알 수 없다 (2026-08-26 사용자 요청)."""
    from app.core import briefing

    data = _mkt()
    data["by_country"] = {
        "베트남": {"now_cost": 5e7, "now_conv": 4e8},      # ROAS 8.0
        "미국": {"now_cost": 1e8, "now_conv": 1.1e8},      # ROAS 1.1
        "태국": {"now_cost": 5e6, "now_conv": 5e8},        # 규모 미달 — 제외
    }
    item = briefing.roas_item("전사", data)
    assert "최고 베트남 8.0" in item["title"]
    assert "최저 미국 1.1" in item["title"]
    # 규모 미달은 순위에 끼지 않는다 (작은 축의 배수가 이기면 안 된다).
    assert "태국" not in item["title"]


def test_untracked_places_are_excluded_and_the_exclusion_is_disclosed():
    """⛔ 전환매출 0 을 '최악' 으로 줄 세우지 마라 — 추적이 없는 것이지 매출이 없는 게 아니다.

    ⚠️ 그냥 빼기만 하면 조용한 누락이다. **뺀 건수를 본문에 적는다.**
    """
    from app.core import briefing

    data = _mkt()
    data["by_country"] = {
        "베트남": {"now_cost": 5e7, "now_conv": 4e8},
        "미국": {"now_cost": 1e8, "now_conv": 1.1e8},
        "일본": {"now_cost": 8e7, "now_conv": 0.0},        # 추적 없음
        "중국": {"now_cost": 9e7, "now_conv": 0.0},        # 추적 없음
    }
    item = briefing.roas_item("전사", data)
    assert "일본" not in item["title"] and "중국" not in item["title"]
    assert "전환 추적 없는 2곳 제외" in item["body"]


def test_zero_conversion_is_not_reported_as_the_worst_roas():
    """⛔ Meta·Tiktok 은 전환 추적이 없으면 0 으로 들어온다 — 매출이 없다는 뜻이 아니다.

    0 을 그대로 나누면 `ROAS 0.00` 이 되고 '최악' 으로 읽힌다. 배수를 만들지 않는다.
    """
    from app.core import briefing

    item = briefing.roas_item("전사", _mkt(now_conv=0.0))
    assert "산출 불가" in item["title"]
    assert "0.00" not in item["title"]
    assert "추적이 없으면" in item["body"]


def test_no_spend_means_no_roas_line_at_all():
    from app.core import briefing

    assert briefing.roas_item("전사", _mkt(now_cost=0.0)) is None
    assert briefing.roas_item("전사", None) is None


def test_sales_item_comes_from_the_snapshot_not_a_saved_alert():
    """⛔ 알릴 만한 변화가 없는 날엔 알림 행이 없다 — 그러면 며칠 전 수치가 붙는다."""
    from datetime import date as _date

    from app.core import briefing

    snapshot = {"base": _date(2026, 8, 24), "cur_from": _date(2026, 8, 18),
                "prev_from": _date(2026, 8, 11), "prev_to": _date(2026, 8, 17),
                "by_team": {"EAST1": {"now": 10.0e8, "prev": 8.0e8}}, "by_country": {}}
    item = briefing.sales_item("동남아시아1팀", snapshot)
    assert "매출 10.0억" in item["title"]
    assert "+25%" in item["title"]
    assert item["for_date"] == "2026-08-24"
    assert briefing.sales_item("전사", None) is None


# ── 데이터 리터러시 (2026-08-26) ─────────────────────────────────────────────

def test_contribution_refuses_to_claim_a_share_when_directions_differ():
    """⛔ 전사는 늘었는데 이 나라만 줄었으면 '감소분의 32%' 는 거짓이다."""
    from app.core.briefing import _contribution

    assert _contribution(-17.4e8, -53.7e8) == "감소분의 32%"
    assert _contribution(+9.9e8, +30.0e8) == "증가분의 33%"
    assert _contribution(-17.4e8, +5.0e8) == "전체와 반대 방향"
    # 축 하나가 전체 증감분보다 크면 다른 곳이 반대로 움직인 것이다.
    # "155%" 라고만 적으면 읽는 사람이 그 뜻을 다시 풀어야 한다.
    assert _contribution(-50.0e8, -5.0e8) == "증감분보다 큼 · 다른 곳은 반대로"
    assert _contribution(-1.0e8, 0.0) == ""


def test_movers_rank_by_amount_not_by_percent():
    """⛔ 변화율로 고르면 작은 축의 배수가 이긴다 (`_top_change` 주석의 실제 사고)."""
    from app.core.briefing import _AD_MIN_SCALE_KRW, _movers

    rows = _movers({
        "영국": {"now_cost": 1.2e8, "prev_cost": 0.2e8},     # +500%, +1.0억
        "미국": {"now_cost": 3.0e8, "prev_cost": 8.0e8},     # -63%, -5.0억
        "태국": {"now_cost": 5e6, "prev_cost": 1e6},         # 규모 미달
    }, "now_cost", "prev_cost", _AD_MIN_SCALE_KRW)
    assert [r["name"] for r in rows] == ["미국", "영국"], rows
    assert all(r["name"] != "태국" for r in rows)


def test_sales_item_names_the_mover_and_how_big_a_story_it_is():
    """합계만 적으면 '무슨 일이 있었나' 를 말하지 못한다 (2026-08-26 사용자 요청)."""
    from datetime import date as _date

    from app.core import briefing

    snapshot = {
        "base": _date(2026, 8, 24), "cur_from": _date(2026, 8, 18),
        "prev_from": _date(2026, 8, 11), "prev_to": _date(2026, 8, 17),
        "by_team": {"ALL": {"now": 104.0e8, "prev": 157.7e8}},
        "by_country": {},
        "countries": [
            {"team": "ALL", "country": "미국", "now_amt": 16.4e8, "prev_amt": 33.7e8},
            {"team": "ALL", "country": "일본", "now_amt": 20.0e8, "prev_amt": 21.0e8},
        ],
    }
    item = briefing.sales_item("전사", snapshot)
    assert "미국" in item["title"]
    assert "-17.3억" in item["title"] or "-17.4억" in item["title"]
    # 얼마나 큰 이야기인지 함께 말한다.
    assert "감소분의" in item["body"]
    assert "전사 전체 104.0억" in item["body"]


def test_marketing_stays_inside_the_users_axis():
    """⛔ 광고 스냅샷의 `by_country` 는 전사 집계다 — 팀 화면에 남의 팀 국가가 뜬다.

    실측(2026-08-26): 동남아시아1팀·서구권마케팅팀·서구권이커머스팀이 **모두 같은**
    `미국 광고비 2.9→0.9억` 을 받았다. 숫자는 진짜라 화면만 보고는 알 수 없었다.
    """
    from datetime import date as _date

    from app.core import briefing

    data = {
        "base": _date(2026, 8, 25), "cur_from": _date(2026, 8, 19),
        "prev_from": _date(2026, 8, 12), "prev_to": _date(2026, 8, 18),
        "by_team": {
            "EAST1": {"now_cost": 1.4e8, "prev_cost": 2.5e8, "now_clicks": 100,
                      "prev_clicks": 90, "now_conv": 6.7e8, "prev_conv": 5e8},
            "WEST_Ecomm": {"now_cost": 0.9e8, "prev_cost": 2.9e8, "now_clicks": 80,
                           "prev_clicks": 200, "now_conv": 1e8, "prev_conv": 3e8},
        },
        "by_country": {}, "all": {},
        "pairs": [
            {"team": "EAST1", "country": "인도네시아", "now_cost": 1.4e8,
             "prev_cost": 2.5e8, "now_conv": 6.7e8, "prev_conv": 5e8},
            {"team": "WEST_Ecomm", "country": "미국", "now_cost": 0.9e8,
             "prev_cost": 2.9e8, "now_conv": 1e8, "prev_conv": 3e8},
        ],
    }
    east = briefing.marketing_item("동남아시아1팀", data)
    assert "인도네시아" in east["title"]
    assert "미국" not in east["title"], east

    west = briefing.marketing_item("서구권이커머스팀", data)
    assert "미국" in west["title"]
    assert "인도네시아" not in west["title"], west


def test_a_country_axis_never_talks_about_another_country():
    """⛔ 국가 축이면 그 나라 자체가 주제다 — 실측: `말레이시아` 축에 호주가 떴다."""
    from datetime import date as _date

    from app.core import briefing

    snapshot = {
        "base": _date(2026, 8, 24), "cur_from": _date(2026, 8, 18),
        "prev_from": _date(2026, 8, 11), "prev_to": _date(2026, 8, 17),
        "by_team": {}, "by_country": {"말레이시아": {"now": 4.7e8, "prev": 4.0e8}},
        "countries": [
            {"team": "EAST1", "country": "호주", "now_amt": 2.8e8, "prev_amt": 13.8e8},
            {"team": "EAST1", "country": "말레이시아", "now_amt": 4.7e8, "prev_amt": 4.0e8},
        ],
    }
    item = briefing.sales_item("말레이시아", snapshot)
    assert "호주" not in item["title"], item
    assert "말레이시아" in item["title"]


# ── 잔디 알림 (2026-08-26) ───────────────────────────────────────────────────

def test_the_two_kind_tables_never_drift():
    """⛔ 릴레이(DB_PC 단독 실행)와 앱이 종류 표를 각자 갖는다 — 불가피한 사본이다.

    한쪽만 고치면 색과 제목이 어긋난다. 에러는 안 난다 — 그래서 여기서 대조한다.
    """
    import re
    from pathlib import Path

    from app.core import jandi_briefing

    relay = (Path(__file__).resolve().parent.parent
             / "scripts" / "jandi_briefing_relay.py").read_text(encoding="utf-8")
    block = re.search(r"KIND_META = \{([^}]*)\}", relay, re.S).group(1)
    found = re.findall(r'"(\w+)":\s*\("([^"]+)",\s*"(#[0-9a-fA-F]+)"\)', block)
    pairs = {name: (label, color) for name, label, color in found}
    assert pairs == jandi_briefing.KIND_META, (pairs, jandi_briefing.KIND_META)


def test_every_jandi_message_carries_a_way_back_to_cella():
    """⚠️ 잔디에서 읽고 끝나면 셀라에 오지 않는다 — 도달이 병목이라는 것이 실측이다."""
    from app.core import jandi_briefing

    payload = jandi_briefing.jandi_payload(
        "본문", kind="report_share", title="일본 매출 보고서",
        link="http://10.1.100.5/api/reports/7")
    titles = [row["title"] for row in payload["connectInfo"]]
    assert "셀라에서 이어서 물어보기" in titles
    assert any("10.1.100.5" in str(row.get("description", ""))
               for row in payload["connectInfo"])
    # 종류마다 색이 달라야 잔디에서 한눈에 구분된다.
    assert payload["connectColor"] == jandi_briefing.KIND_META["report_share"][1]


def test_a_missing_base_url_never_becomes_a_fake_link():
    """⛔ 주소가 설정돼 있지 않으면 링크를 지어내지 마라 — 눌러도 안 열리면 신뢰를 잃는다."""
    from app.core import jandi_notify

    monkey = jandi_notify.base_url()
    if monkey:
        # 주소가 있으면 보고서는 그 보고서로, 나머지는 홈으로 간다.
        assert jandi_notify.link_for("report_share", {"report_id": 7}).endswith("/7")
        assert jandi_notify.link_for("announcement", {}) == monkey
    else:
        assert jandi_notify.link_for("report_share", {"report_id": 7}) == ""


def test_only_handled_feedback_is_pushed():
    """⛔ '접수됨' 을 잔디로 또 알리면 소음이다 — 처리된 것만 회신으로 보낸다."""
    import inspect

    from app.core import jandi_notify

    source = inspect.getsource(jandi_notify._feedbacks)
    assert 'handled_at' in source
    assert "continue" in source


def test_broadcast_announcements_never_reach_jandi():
    """⛔ 잔디로는 **받는 사람이 특정되는 것만** 보낸다 (2026-08-26 사용자 지시).

    공지는 전원 방송이라 아무에게도 앞으로 오지 않는다. 게다가 알림함을 한 번도
    안 연 사람은 `announce_seen_at` 이 NULL 이라 **지난 공지가 전부 '안 읽음'** 이다 —
    개수를 줄이는 걸로는 못 고친다. 종류가 틀린 것이라 목록에서 빼야 한다.
    """
    from app.core import jandi_notify

    names = [fn.__name__ for fn in jandi_notify._PERSONAL_SOURCES]
    # ⚠️ 2026-09-09 에 `_group_assignments` 가 늘었다. 이 관문을 지난 근거는
    #    **역할이 아니라 사람**이라는 것이다 — 그룹 배정 대기 알림은
    #    `group_alerts.OWNER_EMAIL` **한 사람**에게만 가고, 대상이 아니면
    #    `for_user` 가 빈 목록을 준다 (사용자 지시: "나한테만").
    #    "관리자에게" 로 넓혔다면 공지와 같은 모양이라 여기서 막혔어야 한다.
    assert names == ["_shares", "_feedbacks", "_group_assignments"], (
        f"잔디 알림 소스가 바뀌었다: {names}. 새 종류를 넣기 전에 "
        "'받는 사람이 특정되는가' 를 먼저 물어라 — 방송은 알림함이 맡는다"
    )


def test_stale_notifications_are_not_pushed_but_unknown_dates_are():
    """묵은 것은 밀지 않되, **시각을 모르면 보낸다** — 모른다고 버리면 새 알림이 사라진다."""
    from datetime import datetime, timedelta

    from app.core import jandi_notify

    now = datetime(2026, 8, 26, 9, 0)
    fresh = {"at": now - timedelta(days=jandi_notify.PUSH_WINDOW_DAYS - 1)}
    stale = {"at": now - timedelta(days=jandi_notify.PUSH_WINDOW_DAYS + 1)}

    assert jandi_notify._fresh(fresh, now)
    assert not jandi_notify._fresh(stale, now)
    assert jandi_notify._fresh({}, now)
    assert jandi_notify._fresh({"at": None}, now)


def test_newest_first_when_more_than_the_cap_is_waiting():
    """넘치면 새것부터. 오래된 쪽을 먼저 울리면 급한 것이 뒤로 밀린다."""
    from datetime import datetime, timedelta

    from app.core import jandi_notify

    now = datetime(2026, 8, 26, 9, 0)
    rows = [{"kind": "report_share", "seen": False, "at": now - timedelta(hours=n),
             "dedup_key": f"report_share:{n}"} for n in range(6)]

    def fake(_user_id):
        return rows

    original = jandi_notify._PERSONAL_SOURCES
    jandi_notify._PERSONAL_SOURCES = (fake,)
    try:
        got = jandi_notify.pending_items(1, now=now)
    finally:
        jandi_notify._PERSONAL_SOURCES = original

    assert len(got) == jandi_notify.MAX_PER_USER
    assert [row["dedup_key"] for row in got] == [
        "report_share:0", "report_share:1", "report_share:2"]


def test_every_unread_mail_reaches_today():
    """⛔ 이 절에 오르는 기준이 **"LLM 이 요약할 거리를 찾았는가"** 하나였다.
       읽음 여부는 아예 보지 않아서, 안 읽은 5건 중 1건만 Today 에 오르고 나머지는
       아래 카드에 흩어졌다 — 사용자에게는 규칙이 없어 보인다 (2026-08-26 제보:
       "안읽은 메일이 나오는 기준을 모르겠다 / 일부여서 이상함").

    Today 의 메일 절은 **아직 안 본 것이 한자리에 모여야** 쓸모가 있다.
    ⚠️ 요약 없이 제목만 실린다 — 그것이 지어내는 것보다 낫다.
    """
    events, mails = sample()
    mails.append({
        "id": "m9", "from_display": "OP팀", "subject": "재고 실사 일정",
        "snippet": "", "received_at": "2026-08-25T09:00:00+09:00",
        "unread": True, "url": "https://mail.google.com/m9",
    })
    document = work_briefing.compose(
        day=date(2026, 8, 25), now=at("2026-08-25T09:00:00"),
        events=events, mails=mails, window={"label": "어제 18:00 이후"},
        raw={"mail_points": []})
    ids = [row["id"] for row in document["mail"]]
    assert "m9" in ids, "안 읽은 메일이 Today 에 없다"
    row = next(r for r in document["mail"] if r["id"] == "m9")
    assert row["unread"] is True and row["points"] == []


def test_unread_mail_is_never_crowded_out_by_read_mail():
    """⛔ 상한(8건)을 **LLM 이 고른 것이 먼저 다 써버렸다.** 실측(2026-08-26 프로덕션):
       안 읽은 5건 중 3건만 Today 에 올랐다 — 안 읽은 것을 모아 보여주려고 고친 절인데
       도로 일부만 나온 셈이다.

    자리가 모자라면 **읽은 것부터** 뺀다.
    """
    events, mails = sample()
    for i in range(9):
        mails.append({
            "id": f"r{i}", "from_display": "뉴스", "subject": f"읽은 메일 {i}",
            "snippet": "", "received_at": "2026-08-25T08:00:00+09:00",
            "unread": False, "url": f"https://mail.google.com/r{i}",
        })
    for i in range(4):
        mails.append({
            "id": f"u{i}", "from_display": "동료", "subject": f"안 읽은 메일 {i}",
            "snippet": "", "received_at": "2026-08-25T08:30:00+09:00",
            "unread": True, "url": f"https://mail.google.com/u{i}",
        })
    raw = {"mail_points": [
        {"message_id": f"r{i}", "points": [f"읽은 메일 {i}"], "request": ""}
        for i in range(9)]}
    document = work_briefing.compose(
        day=date(2026, 8, 25), now=at("2026-08-25T09:00:00"),
        events=events, mails=mails, window={"label": "어제 18:00 이후"}, raw=raw)

    ids = [row["id"] for row in document["mail"]]
    for i in range(4):
        assert f"u{i}" in ids, f"안 읽은 메일 u{i} 이 읽은 메일에 밀렸다"
    assert len(ids) <= work_briefing.MAX_MAIL_ROWS
    assert document["mail_omitted_unread"] == 0


def _many_unread(count: int):
    events, mails = sample()
    for i in range(count):
        mails.append({
            "id": f"u{i}", "from_display": "동료", "subject": f"안 읽은 메일 {i}",
            "snippet": "", "received_at": "2026-08-25T08:30:00+09:00",
            "unread": True, "url": f"https://mail.google.com/u{i}",
        })
    return work_briefing.compose(
        day=date(2026, 8, 25), now=at("2026-08-25T09:00:00"),
        events=events, mails=mails, window={"label": "어제 18:00 이후"},
        raw={"mail_points": []})


def test_screen_makes_room_by_scrolling_not_by_cutting():
    """⛔ 자리가 모자라다고 **잘라 버리면** 무엇이 빠졌는지 알 수 없다.
       공간은 스크롤로 만든다 (2026-08-26 사용자 지시: "카드 내 스크롤을 통해 공간 확보").
       그래서 화면 상한은 수집 상한(Gmail 20~40건)만큼 넉넉하다."""
    document = _many_unread(25)
    rows = document["mail"]
    assert len(rows) == 26, "26건(m1 + u0~24)이 전부 실려야 한다"
    assert document["mail_omitted_unread"] == 0


def test_beyond_the_screen_cap_the_cut_is_disclosed():
    """상한을 넘기면 그때는 잘린다 — 대신 **몇 건이 잘렸는지** 문서가 들고 있어야
       화면·잔디가 밝힐 수 있다. 조용히 자르는 것이 가장 나쁜 실패다."""
    document = _many_unread(45)
    rows = document["mail"]
    assert len(rows) == work_briefing.MAX_MAIL_ROWS
    assert all(row["unread"] for row in rows)
    assert document["mail_omitted_unread"] == 46 - work_briefing.MAX_MAIL_ROWS
    assert "안 읽은 메일 6건" in document["markdown"]


def test_chat_body_is_cut_shorter_than_the_screen():
    """⛔ **채팅 본문에는 스크롤이 없다.** 잔디에 40줄을 밀어 넣으면 아무도 안 읽는다 —
       화면과 같은 자를 쓰면 안 된다. 넘치면 "외 N건" 으로 줄이고 어디서 볼 수 있는지 적는다."""
    document = _many_unread(25)
    text = document["markdown"]
    listed = text.count("안 읽은 메일 ")
    assert listed <= work_briefing.MAX_MAIL_LINES_IN_TEXT + 1
    assert "외 16건" in text and "안 읽음 16" in text
    assert "Today" in text

def test_summarised_read_mail_is_not_squeezed_out_by_unread():
    """⛔ 제보(2026-08-26): "13개 중 안읽음 7개는 나오는데 읽음은 한 개만 나온다".

    원인은 **요약이 붙은 행을 통틀어 8개로 묶은 상한**이었다. 안 읽은 7건이 그
    자리를 먼저 써서 읽은 메일은 1건만 남았다. 화면 자리를 아끼려던 상한인데
    절이 안에서 스크롤하게 된 뒤로는 아낄 이유가 없다 — 자리는 스크롤이 만든다.
    """
    events, mails = sample()
    for i in range(7):
        mails.append({
            "id": f"u{i}", "from_display": "동료", "subject": f"안 읽은 메일 {i}",
            "snippet": f"안 읽은 메일 {i} 내용", "received_at": "2026-08-25T08:30:00+09:00",
            "unread": True, "url": f"https://mail.google.com/u{i}",
        })
    for i in range(6):
        mails.append({
            "id": f"r{i}", "from_display": "협력사", "subject": f"읽은 메일 {i}",
            "snippet": f"읽은 메일 {i} 내용", "received_at": "2026-08-25T08:00:00+09:00",
            "unread": False, "url": f"https://mail.google.com/r{i}",
        })
    raw = {"mail_points": (
        [{"message_id": f"u{i}", "points": [f"안 읽은 메일 {i} 내용"], "request": ""}
         for i in range(7)]
        + [{"message_id": f"r{i}", "points": [f"읽은 메일 {i} 내용"], "request": ""}
           for i in range(6)])}
    document = work_briefing.compose(
        day=date(2026, 8, 25), now=at("2026-08-25T09:00:00"),
        events=events, mails=mails, window={"label": "어제 18:00 이후"}, raw=raw)

    ids = [row["id"] for row in document["mail"]]
    for i in range(7):
        assert f"u{i}" in ids, f"안 읽은 메일 u{i} 이 빠졌다"
    for i in range(6):
        assert f"r{i}" in ids, f"요약이 있는 읽은 메일 r{i} 이 빠졌다"
    assert document["mail_omitted_read"] == 0


def test_all_collected_mail_reaches_today():
    """⛔ 예전엔 요약이 없는 읽은 메일을 뺐다 ("이 절이 받은편지함이 된다"). 그 결과
       같은 메일이 아래 `그 밖의 메일` 카드에 다시 나왔고 **한 화면에 목록이 두 벌**이
       됐다 — 어느 쪽이 전부인지 알 수 없다. 절이 안에서 스크롤하는 지금은 길이가
       문제가 아니므로 한곳에 다 모은다 (2026-08-26 사용자 확인).
    """
    events, mails = sample()
    mails.append({
        "id": "rx", "from_display": "뉴스레터", "subject": "주간 소식",
        "snippet": "", "received_at": "2026-08-25T07:00:00+09:00",
        "unread": False, "url": "https://mail.google.com/rx",
    })
    document = work_briefing.compose(
        day=date(2026, 8, 25), now=at("2026-08-25T09:00:00"),
        events=events, mails=mails, window={"label": "어제 18:00 이후"},
        raw={"mail_points": []})

    ids = [row["id"] for row in document["mail"]]
    assert "rx" in ids, "요약이 없다고 빼면 카드에서 다시 보여줘야 한다"
    assert len(ids) == len(mails), "수집한 메일이 전부 실려야 한다"
    assert document["mail_omitted_read"] == 0


def test_unread_still_comes_first():
    """전부 싣더라도 순서는 안 읽은 것이 먼저다."""
    events, mails = sample()
    mails.append({
        "id": "rx", "from_display": "뉴스레터", "subject": "주간 소식",
        "snippet": "", "received_at": "2026-08-25T07:00:00+09:00",
        "unread": False, "url": "https://mail.google.com/rx",
    })
    mails.append({
        "id": "ux", "from_display": "동료", "subject": "확인 부탁",
        "snippet": "", "received_at": "2026-08-25T06:00:00+09:00",
        "unread": True, "url": "https://mail.google.com/ux",
    })
    document = work_briefing.compose(
        day=date(2026, 8, 25), now=at("2026-08-25T09:00:00"),
        events=events, mails=mails, window={"label": "어제 18:00 이후"},
        raw={"mail_points": []})
    flags = [row["unread"] for row in document["mail"]]
    assert flags == sorted(flags, reverse=True), flags


def test_rows_within_a_group_are_newest_first():
    """⚠️ LLM 이 고른 것을 먼저 담고 나머지를 뒤에 붙이므로, 그대로 두면 시간이 튄다
       (실측 2026-08-26: 15:01 → 12:03 → 14:08). 화면 왼쪽이 시간축이라 거짓말처럼 보인다.

    ⛔ `at`(HH:MM) 으로 정렬하면 안 된다 — 어제 22:16 이 맨 위로 온다.
    """
    events, mails = sample()
    stamps = ["2026-08-24T22:16:00+09:00", "2026-08-25T08:06:00+09:00",
              "2026-08-25T14:08:00+09:00", "2026-08-25T12:03:00+09:00"]
    for i, stamp in enumerate(stamps):
        mails.append({
            "id": f"u{i}", "from_display": "동료", "subject": f"안 읽은 메일 {i}",
            "snippet": f"안 읽은 메일 {i} 내용", "received_at": stamp,
            "unread": True, "url": f"https://mail.google.com/u{i}",
        })
    # LLM 은 마지막 것만 골랐다 — 그대로면 그게 맨 앞에 온다
    raw = {"mail_points": [{"message_id": "u0", "points": ["안 읽은 메일 0 내용"], "request": ""}]}
    document = work_briefing.compose(
        day=date(2026, 8, 25), now=at("2026-08-25T15:00:00"),
        events=events, mails=mails, window={"label": "어제 18:00 이후"}, raw=raw)

    unread = [row for row in document["mail"] if row["unread"]]
    order = [row["id"] for row in unread if row["id"].startswith("u")]
    assert order == ["u2", "u3", "u1", "u0"], order


# ── 잔디 링크 표기 (2026-08-31 실측) ─────────────────────────────────────────

def test_jandi_links_use_markdown_form_only():
    """⛔ 주소를 평문으로 싣지 마라 — **잔디는 자동으로 링크를 걸지 않는다.**

    네 형태를 실제 토픽으로 보내 확인했다 (2026-08-31):
        [글](주소)  → 눌린다
        평문 · 주소 / <주소> / 주소만 → 전부 그냥 글자
    눌리지 않는 주소는 "돌아올 문" 이 아니라 소음이다. 도달이 병목이라 이 한 줄이
    브리핑을 읽고 끝낼지 셀라로 올지를 가른다.
    """
    import inspect

    from app.core import jandi_notify, personal_briefing

    for module, where in ((personal_briefing, "브리핑 본문"),
                          (jandi_notify, "알림 본문")):
        source = inspect.getsource(module)
        assert "]({link})" in source, f"{where}: 마크다운 링크 표기가 아니다"
        assert "물어보기 · {link}" not in source, f"{where}: 평문 주소로 되돌아갔다"
        assert "보기 · {link}" not in source, f"{where}: 평문 주소로 되돌아갔다"
