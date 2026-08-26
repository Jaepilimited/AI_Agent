"""첫 화면 출근 브리핑 문서 — 실제 브라우저에서 그려지는지 본다.

여기서 잡으려는 것은 전부 **에러 없이 잘못 보이는** 종류다: 섹션이 통째로 빠져도
콘솔은 조용하고, 제목이 HTML 로 해석돼도 화면은 멀쩡해 보인다.
"""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "app/frontend/personal-briefing.js"
STYLE = ROOT / "app/static/style.css"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    current = context.new_page()
    yield current
    context.close()


def _document(**overrides):
    document = {
        "status": "ready",
        "for_date": "2026-08-25",
        "weekday": "화",
        "window": {"label": "어제(8/24 월) 18:00 이후 받은 메일"},
        "mail_summary": "예산 시트 작성 요청이 왔습니다.",
        "meetings": [{
            "id": "e1", "time": "10:00~12:00", "title": "본부장 월간회의",
            "location": "Creation (3F)", "attendees": ["ryankwon", "sckang"],
            "attendee_count": 2, "conference_url": "", "url": "https://calendar.google.com/e1",
            "prep": "예산 확정안 팔로업", "ended": False, "declined": False, "urgency": "high",
        }],
        "mail": [{
            "id": "m1", "at": "09:12", "from": "이해인", "subject": "예산 시트 공유",
            "url": "https://mail.google.com/mail/u/0/#all/m1", "unread": True,
            "points": ["8~12월 예산 예상 시트 작성"], "request": "시트 입력 후 회신",
            "urgency": "normal",
        }],
        "actions": [{
            "text": "예산 시트 입력", "source": "mail", "source_id": "m1", "at": "09:12",
            "url": "https://mail.google.com/mail/u/0/#all/m1", "urgency": "high",
        }],
        "deadlines": [{
            "date": "2026-08-28", "label": "8/28(금)", "text": "시트 제출",
            "source": "mail", "source_id": "m1",
            "url": "https://mail.google.com/mail/u/0/#all/m1", "urgency": "normal",
        }],
        "mail_total": 1, "mail_unread": 1, "dropped": 0, "urgent": 2,
        "markdown": "☀️ 오늘의 출근 브리핑",
    }
    document.update(overrides)
    return document


def _payload(document):
    return {
        "enabled": True,
        "for_date": "2026-08-25",
        "generated_at": "2026-08-25T09:00:00+09:00",
        "needs_refresh": False,
        "google": {"connected": True, "account": "me@example.com"},
        "priorities": [],
        "calendar": {"status": "ready", "items": [], "truncated": False, "error_code": ""},
        "mail": {"status": "ready", "items": [], "count_label": "1건", "unread": 1,
                 "summary": "", "action_candidates": [], "truncated": False, "error_code": ""},
        "business": {"status": "empty", "item": None},
        "document": document,
    }


def _mount(page, payload):
    page.set_content(
        '<section id="personal-briefing">'
        '<div class="personal-briefing-grid"></div></section>'
        '<textarea id="chat-input"></textarea>'
    )
    page.add_style_tag(path=str(STYLE))
    page.add_script_tag(path=str(SCRIPT))
    page.evaluate(
        """async payload => {
            const fetchImpl = async () => ({ok: true, json: async () => payload});
            window.controller = CellaPersonalBriefing.create({
              root: document.querySelector('#personal-briefing'),
              input: document.querySelector('#chat-input'), connect: () => {}, fetchImpl
            });
            await window.controller.load();
        }""",
        payload,
    )


def test_every_section_is_rendered(page):
    """섹션 이름은 이모지 없이 한 낱말이다 — 아이콘이 아니라 시간축이 종류를 말한다."""
    _mount(page, _payload(_document()))
    names = page.locator(".briefing-doc-section-name").all_inner_texts()
    assert names == ["일정", "메일", "할 일", "기한", "지표"]


def test_no_emoji_survives_in_the_rendered_document(page):
    """이모지 헤딩·신호등은 이 화면이 '생성된 것'처럼 보이게 만든 가장 큰 요인이었다."""
    _mount(page, _payload(_document()))
    text = page.locator(".briefing-doc").inner_text()
    for glyph in ("📅", "✉", "✅", "⏰", "📊",
                  "🔴", "🟡", "💡", "☀"):
        assert glyph not in text, repr(glyph)


def test_the_time_gutter_carries_every_row(page):
    """왼쪽 시간축이 이 화면의 뼈대다 — 비면 종류를 알 수 없다."""
    _mount(page, _payload(_document()))
    times = page.locator(".briefing-doc-time").all_inner_texts()
    assert times[0].splitlines() == ["10:00", "12:00"]   # 일정은 시작·종료
    assert "09:12" in times[1]                            # 메일은 수신 시각
    assert "09:12" in times[2]                            # 할 일은 근거가 온 시각
    assert "8/28(금)" in times[3]                         # 기한은 날짜
    assert all(text.strip() for text in times)


def test_headline_counts_come_from_the_document(page):
    _mount(page, _payload(_document()))
    stats = page.locator(".briefing-doc-stats").inner_text().split()
    assert stats == ["일정", "1", "메일", "1", "긴급", "2", "기한", "1"]


def test_the_date_is_the_headline(page):
    """무엇을 보는 화면인지 날짜가 먼저 말한다."""
    _mount(page, _payload(_document()))
    assert page.locator(".briefing-doc-date").inner_text() == "8월 25일 화요일"
    # 바깥 제목이 이미 'Today' 다 — 문서가 또 '오늘의 브리핑' 이라고 말하지 않는다.
    assert page.locator(".briefing-doc-eyebrow").count() == 0


def test_urgent_rows_are_marked_by_type_and_rule_not_by_a_dot(page):
    """급한 것은 시각이 진해지고 왼쪽에 색 바가 선다 — 신호등 이모지를 쓰지 않는다."""
    _mount(page, _payload(_document()))
    assert page.locator(".briefing-doc-row.urgent").count() == 2

    urgent, calm = page.locator(".briefing-doc-time b").all()[:2]
    weights = [
        node.evaluate("el => getComputedStyle(el).fontWeight") for node in (urgent, calm)
    ]
    assert int(weights[0]) > int(weights[1]), weights
    bar = page.locator(".briefing-doc-row.urgent").first.evaluate(
        "el => getComputedStyle(el).borderLeftColor"
    )
    assert bar != "rgba(0, 0, 0, 0)"


def test_document_starts_open_and_the_header_collapses_it(page):
    _mount(page, _payload(_document()))
    body = page.locator(".briefing-doc-body")
    assert body.is_visible() is True
    page.locator(".briefing-doc-head").click()
    assert body.is_visible() is False
    assert page.locator(".briefing-doc-head").get_attribute("aria-expanded") == "false"


def test_titles_are_text_not_markup(page):
    """제목이 HTML 로 해석되면 에러 없이 화면만 조용히 오염된다."""
    document = _document()
    document["meetings"][0]["title"] = "<img src=x onerror=window.xss=1>"
    _mount(page, _payload(document))
    assert page.evaluate("() => window.xss") is None
    assert "<img" in page.locator(".briefing-doc-row-head").first.inner_text()


def test_non_google_links_are_refused(page):
    document = _document()
    document["meetings"][0]["url"] = "https://evil.example/x"
    document["meetings"][0]["conference_url"] = "javascript:alert(1)"
    _mount(page, _payload(document))
    head = page.locator(".briefing-doc-row-head").first
    assert head.evaluate("node => node.tagName") == "BUTTON"
    assert page.locator(".briefing-doc-join").count() == 0


def test_dropped_sentences_are_disclosed_on_screen(page):
    """⛔ 버린 문장을 조용히 사라지게 두지 않는다 — 화면에도 건수가 남는다."""
    _mount(page, _payload(_document(dropped=3)))
    assert "3건" in page.locator(".briefing-doc-note").inner_text()


def test_disconnected_google_still_shows_metrics_and_rates(page):
    """⛔ 미연결이라고 문서를 통째로 숨기지 마라 (2026-08-26).

    그러면 첫 화면에 "연결하세요" 버튼 하나만 남아 다시 오지 않는다 —
    실측상 가입 62명 중 구글 연결은 9명뿐이었다.
    지표·환율은 **구글과 무관한 데이터**다.
    """
    payload = _payload(_document(status="disconnected"))
    payload["google"] = {"connected": False, "account": ""}
    payload["business"] = {"status": "ready", "items": [
        {"kind": "sales", "id": "1", "for_date": "2026-08-24",
         "title": "미국 33.7억 → 16.4억 (-51%)", "body": "감소분의 32%"}]}
    payload["fx"] = {"status": "ready", "for_date": "2026-08-26", "stale_days": 0,
                     "items": [{"currency": "USD", "unit": 1, "krw": 1383.12,
                                "was_krw": 1461.0, "change_pct": -5.33}]}
    _mount(page, payload)

    assert page.locator(".briefing-doc").is_hidden() is False
    names = page.locator(".briefing-doc-section-name").all_inner_texts()
    assert names == ["오늘의 일정과 메일", "지표", "환율"], names
    # 일정·메일 절은 만들지 않는다 (보여줄 것이 없다).
    assert page.locator(".briefing-doc-columns").count() == 0
    # 왜 연결해야 하는지 말하고, 그 자리에서 연결할 수 있어야 한다.
    assert "일정" in page.locator(".briefing-doc-empty").first.inner_text()
    assert page.locator(".briefing-doc-action.primary").inner_text() == "Google 연결"
    # 구글과 무관한 값은 그대로 보인다.
    assert "미국" in page.locator(".briefing-doc").inner_text()
    assert "1,383원" in page.locator(".briefing-doc").inner_text()


def test_a_document_with_no_status_is_hidden(page):
    """상태 자체가 없으면(아직 못 불러옴) 빈 껍데기를 보여주지 않는다."""
    payload = _payload({})
    _mount(page, payload)
    assert page.locator(".briefing-doc").is_hidden() is True


def test_empty_sections_say_so_instead_of_vanishing(page):
    document = _document(meetings=[], mail=[], actions=[], deadlines=[], mail_total=0, urgent=0)
    _mount(page, _payload(document))
    assert page.locator(".briefing-doc-section").count() == 5
    assert page.locator(".briefing-doc-empty").count() == 5


# ── 문서와 카드의 역할 분리 (2026-08-26) ──────────────────────────────────────

def _with_cards(document, cal_items, mail_items):
    payload = _payload(document)
    payload["calendar"]["items"] = cal_items
    payload["mail"]["items"] = mail_items
    return payload


def _event(event_id, start):
    return {"id": event_id, "title": "회의 " + event_id, "start": start,
            "end": start.replace("T10:", "T11:"), "all_day": False,
            "location": "", "url": "", "ended": False}


def test_cards_hold_only_what_the_document_does_not(page):
    """오늘 일정과 문서에 실린 메일은 카드에서 빠진다 — 같은 말을 두 번 하지 않는다."""
    payload = _with_cards(
        _document(),
        [_event("today", "2026-08-25T10:00:00+09:00"), _event("later", "2026-08-27T10:00:00+09:00")],
        [{"id": "m1", "subject": "문서에 실린 메일", "from_display": "A",
          "received_at": "2026-08-25T08:00:00+09:00", "unread": True, "url": ""},
         {"id": "m9", "subject": "문서가 고르지 않은 메일", "from_display": "B",
          "received_at": "2026-08-25T08:00:00+09:00", "unread": False, "url": ""}],
    )
    _mount(page, payload)
    names = page.locator(".personal-briefing-card-name").all_inner_texts()
    counts = page.locator(".personal-briefing-count").all_inner_texts()
    assert names == ["내일부터", "그 밖의 메일"]
    assert counts == ["1", "1"]
    # 카드 항목도 Today 일정과 같은 시간축 행이다 (2026-08-26 사용자 요청).
    items = page.locator(".personal-briefing-card .briefing-doc-row-head").all_inner_texts()
    assert "문서가 고르지 않은 메일" in items
    assert "문서에 실린 메일" not in items
    assert not any("회의 today" in text for text in items)
    assert page.locator(".personal-briefing-item").count() == 0


def test_a_lone_card_takes_the_full_width(page):
    """2열 그리드에 한 장만 남으면 왼쪽에 붙어 잘린 것처럼 보인다."""
    payload = _with_cards(_document(), [_event("later", "2026-08-27T10:00:00+09:00")], [])
    _mount(page, payload)
    grid = page.locator(".personal-briefing-grid")
    assert page.locator(".personal-briefing-card").count() == 1
    assert "single" in (grid.get_attribute("class") or "")


def test_the_grid_disappears_when_no_card_has_anything_to_say(page):
    """⛔ '없습니다' 만 적힌 빈 카드를 남기지 않는다 — 화면만 길어진다."""
    _mount(page, _with_cards(_document(), [], []))
    assert page.locator(".personal-briefing-card").count() == 0
    assert page.locator(".personal-briefing-grid").is_hidden() is True


def test_disconnected_google_keeps_the_connect_card(page):
    """연결이 끊긴 상태에서는 빈 카드라도 남아야 다시 연결할 자리가 있다."""
    payload = _with_cards(_document(), [], [])
    payload["google"] = {"connected": False, "account": ""}
    _mount(page, payload)
    assert page.locator(".personal-briefing-connect").count() == 1


def test_collapsed_document_still_shows_what_is_urgent(page):
    """접으면 '긴급 2건' 숫자만 남아 무엇이 급한지 알 수 없다."""
    _mount(page, _payload(_document()))
    strip = page.locator(".briefing-doc-urgent")
    assert strip.is_hidden() is True

    page.locator(".briefing-doc-head").click()
    assert strip.is_visible() is True
    text = strip.inner_text()
    assert text.startswith("긴급")
    assert "본부장 월간회의" in text
    assert "예산 시트 입력" in text

    page.locator(".briefing-doc-head").click()
    assert strip.is_hidden() is True


def test_no_urgent_item_means_no_strip(page):
    document = _document(urgent=0)
    document["meetings"][0]["urgency"] = "normal"
    document["actions"][0]["urgency"] = "normal"
    _mount(page, _payload(document))
    assert page.locator(".briefing-doc-urgent").count() == 0


def test_skeleton_does_not_promise_cards_that_no_longer_exist(page):
    """로딩 스켈레톤이 은퇴한 카드 이름을 그리면 화면이 잠깐 거짓말을 한다."""
    page.set_content(
        '<section id="personal-briefing">'
        '<div class="personal-briefing-grid"></div></section>'
        '<textarea id="chat-input"></textarea>'
    )
    page.add_script_tag(path=str(SCRIPT))
    page.evaluate(
        """() => {
            const controller = CellaPersonalBriefing.create({
              root: document.querySelector('#personal-briefing'),
              input: document.querySelector('#chat-input'), connect: () => {},
              fetchImpl: async () => ({ok: true, json: async () => ({})})
            });
            controller.invalidate();  // 스켈레톤만 그리고 멈추는 유일한 동기 경로
        }"""
    )
    assert page.locator(".personal-briefing-skeleton").count() == 2
    names = page.locator(".personal-briefing-card-name").all_inner_texts()
    assert names == ["내일부터", "그 밖의 메일"]
    for retired in ("오늘 우선 확인", "7일 일정", "오늘 메일", "업무 지표"):
        assert retired not in names


# ── 가로 공간 활용 (2026-08-26) ───────────────────────────────────────────────

def test_the_body_splits_into_two_columns_on_a_wide_screen(page):
    """세로로만 쌓으면 넓은 화면에서 오른쪽이 통째로 비고 스크롤만 길어진다."""
    page.set_viewport_size({"width": 1400, "height": 1200})
    _mount(page, _payload(_document()))
    columns = page.locator(".briefing-doc-col")
    assert columns.count() == 2

    left = columns.nth(0).locator(".briefing-doc-section-name").all_inner_texts()
    right = columns.nth(1).locator(".briefing-doc-section-name").all_inner_texts()
    # 왼쪽은 오늘 '일어나는' 것, 오른쪽은 '해야 할' 것과 참고.
    assert left == ["일정", "메일"]
    assert right == ["할 일", "기한", "지표"]

    boxes = [columns.nth(i).bounding_box() for i in range(2)]
    assert boxes[1]["x"] > boxes[0]["x"] + boxes[0]["width"] / 2, boxes


def test_the_columns_stack_on_a_narrow_screen(page):
    """좁아지면 한 열로 접히고, 그때 순서는 DOM 순서 그대로여야 읽힌다."""
    page.set_viewport_size({"width": 720, "height": 1200})
    _mount(page, _payload(_document()))
    boxes = [page.locator(".briefing-doc-col").nth(i).bounding_box() for i in range(2)]
    assert abs(boxes[0]["x"] - boxes[1]["x"]) < 1, boxes
    assert boxes[1]["y"] > boxes[0]["y"], boxes

    names = page.locator(".briefing-doc-section-name").all_inner_texts()
    assert names == ["일정", "메일", "할 일", "기한", "지표"]


def test_upcoming_events_show_who_is_coming(page):
    """'내일부터' 도 오늘 일정과 같이 참석자를 보여준다 (2026-08-26 사용자 요청)."""
    payload = _with_cards(
        _document(),
        [{"id": "later", "title": "주간 실적 리뷰", "start": "2026-08-27T10:00:00+09:00",
          "end": "2026-08-27T11:00:00+09:00", "all_day": False, "location": "Creation (3F)",
          "url": "", "ended": False,
          "attendees": ["ryankwon", "sckang", "rak", "wkyang", "yjcho", "haily", "kak"]}],
        [],
    )
    _mount(page, payload)
    detail = page.locator(".personal-briefing-card .briefing-doc-detail").first.inner_text()
    assert "Creation (3F)" in detail
    assert "ryankwon, sckang, rak, wkyang, yjcho" in detail
    assert "외 2명" in detail


def test_one_builder_makes_the_event_detail_for_both_places():
    """⛔ 장소·참석자 문장을 두 곳에서 따로 만들면 언젠가 서로 다른 말을 한다."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.count("function eventDetail(") == 1
    # 문서(오늘)와 카드(내일부터) 두 곳이 같은 함수로 같은 줄을 만든다.
    assert source.count('docLine(main, "briefing-doc-detail", eventDetail(item))') == 2


def test_metrics_are_two_rows_sales_and_marketing(page):
    """지표는 매출 한 줄 · 마케팅 한 줄이다 (2026-08-26 사용자 요청).

    ⛔ 한 덩어리로 두면 어느 수치가 어느 쪽인지 읽는 사람이 갈라야 한다.
    ⚠️ 기준일이 서로 다를 수 있다 — 광고 적재가 매출보다 하루 빠르다.
    """
    payload = _payload(_document())
    payload["business"] = {"status": "ready", "items": [
        {"kind": "sales", "id": "1", "for_date": "2026-08-24",
         "title": "전사 주시 · 미국 하락 33.7억 → 16.4억 (-51%)",
         "body": "· 전사 전체 · 매출 82.0억", "follow_up": "자세히"},
        {"kind": "marketing", "id": "mkt-1", "for_date": "2026-08-25",
         "title": "광고비 2.5억 · 직전 7일 대비 +25%",
         "body": "8/19~8/25 · 클릭 1,234회 · 직전 7일 2.0억", "follow_up": "비교"},
    ]}
    _mount(page, payload)
    section = page.locator(".briefing-doc-section").last
    rows = section.locator(".briefing-doc-row")
    assert rows.count() == 2

    assert "미국 하락" in rows.nth(0).locator(".briefing-doc-row-head").inner_text()
    assert "광고비 2.5억" in rows.nth(1).locator(".briefing-doc-row-head").inner_text()
    # 각자 자기 기준일을 들고 온다.
    assert rows.nth(0).locator(".briefing-doc-time b").inner_text() == "8/24"
    assert rows.nth(1).locator(".briefing-doc-time b").inner_text() == "8/25"


def test_rate_cells_flow_horizontally_with_the_previous_value(page):
    """환율은 통화마다 한 줄씩 쌓지 않는다 — 9종이면 아홉 줄이 되고 오른쪽이 텅 빈다.

    ⛔ 시간축이 필요 없는 유일한 절이라 두 열 밖 **전체 폭**에서 가로로 흐른다
       (2026-08-26 사용자 지적). 값은 `1,283 → 1,383원` 로 이전 값을 함께 보여준다.
    """
    page.set_viewport_size({"width": 1400, "height": 1200})
    payload = _payload(_document())
    payload["fx"] = {"status": "ready", "for_date": "2026-08-26",
                     "basis_note": "전월대비 2026-07-24", "stale_days": 0, "items": [
                         {"currency": "USD", "unit": 1, "krw": 1383.12,
                          "was_krw": 1283.0, "change_pct": 7.81},
                         {"currency": "EUR", "unit": 1, "krw": 1614.08,
                          "was_krw": 1614.2, "change_pct": 0.01},
                         {"currency": "IDR", "unit": 100, "krw": 7.81,
                          "was_krw": None, "change_pct": None}]}
    _mount(page, payload)
    cells = page.locator(".briefing-doc-fx-item")
    assert cells.count() == 3

    usd = cells.nth(0).inner_text().split()
    assert "1,283" in usd and "→" in usd and "1,383원" in usd

    # ⚠️ 반올림 후 같은 값이면 화살표를 쓰지 않는다 — `1,614 → 1,614원` 은 소음이다.
    assert cells.nth(1).locator(".briefing-doc-fx-arrow").count() == 0
    # 비교 기준이 없으면 오늘 값만.
    assert cells.nth(2).locator(".briefing-doc-fx-was").count() == 0

    # 세 칸이 같은 줄에 선다 (세로로 쌓이지 않는다).
    tops = [cells.nth(i).bounding_box()["y"] for i in range(3)]
    assert max(tops) - min(tops) < 5, tops

    # 두 열 밖에 있어야 전체 폭을 쓴다.
    assert page.locator(".briefing-doc-col .briefing-doc-fx").count() == 0
    assert page.locator(".briefing-doc-body > .briefing-doc-fx").count() == 1


def test_the_basis_wording_is_whatever_the_server_said(page):
    """⛔ 화면이 '전월대비' 를 조립하지 않는다 — 진짜 한 달 전인지는 서버만 안다."""
    payload = _payload(_document())
    payload["fx"] = {"status": "ready", "for_date": "2026-08-26",
                     "basis_note": "2026-07-24 대비", "stale_days": 0,
                     "items": [{"currency": "USD", "unit": 1, "krw": 1383.12,
                                "was_krw": 1283.0, "change_pct": 7.81}]}
    _mount(page, payload)
    note = page.locator(".briefing-doc-fx .briefing-doc-window").inner_text()
    assert "2026-07-24 대비" in note
    assert "전월대비" not in note


def test_the_connect_prompt_appears_once_not_twice(page):
    """⛔ 문서가 연결을 안내하면 카드에서 또 하지 않는다 — 버튼이 두 곳이면 헷갈린다."""
    payload = _payload(_document(status="disconnected"))
    payload["google"] = {"connected": False, "account": ""}
    payload["calendar"] = {"status": "disconnected", "items": [], "truncated": False,
                           "error_code": "oauth_missing"}
    payload["mail"] = {"status": "disconnected", "items": [], "count_label": "0건",
                       "unread": 0, "summary": "", "action_candidates": [],
                       "truncated": False, "error_code": "oauth_missing"}
    _mount(page, payload)
    assert page.locator(".personal-briefing-grid").is_hidden() is True
    assert page.locator(".personal-briefing-connect").count() == 0
    assert page.locator(".briefing-doc-action.primary").count() == 1
