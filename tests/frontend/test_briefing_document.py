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


def _mount_with_settings(page, payload):
    saved = {
        "questions": [{
            "id": "sq-1",
            "question": "쇼피 인도네시아 주간 매출",
            "cadence": "weekly",
            "weekday": 0,
            "enabled": True,
            "last_run_at": "",
            "last_status": "",
            "last_error": "",
        }]
    }
    jandi = {
        "registered": True,
        "enabled": True,
        "masked": "…/abcd********",
        "send_at": "09:30",
        "send_time_choices": ["08:00", "09:30"],
        "sections": [{
            "key": "meetings",
            "label": "오늘의 일정",
            "group": "브리핑",
            "enabled": True,
        }],
        "last_sent_at": "",
        "last_error": "",
    }
    page.set_content(
        '<section id="personal-briefing">'
        '<div class="personal-briefing-grid"></div></section>'
        '<textarea id="chat-input"></textarea>'
    )
    page.add_style_tag(path=str(STYLE))
    page.add_script_tag(path=str(SCRIPT))
    page.evaluate(
        """async data => {
          const reply = value => ({ok: true, json: async () => value});
          window.fetch = async url => {
            if (url === '/api/saved-questions') return reply(data.saved);
            return {ok: false, json: async () => ({detail: 'unexpected request'})};
          };
          const fetchImpl = async url => {
            if (url === '/api/personal-briefing') return reply(data.briefing);
            if (url === '/api/personal-briefing/jandi') return reply(data.jandi);
            return {ok: false, json: async () => ({detail: 'unexpected request'})};
          };
          window.controller = CellaPersonalBriefing.create({
            root: document.querySelector('#personal-briefing'),
            input: document.querySelector('#chat-input'), connect: () => {}, fetchImpl
          });
          await window.controller.load();
        }""",
        {"briefing": payload, "saved": saved, "jandi": jandi},
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


def test_document_does_not_duplicate_the_global_settings_entry_point(page):
    """Settings live in the user footer, so the briefing document must not grow a second gear."""

    _mount(page, _payload(_document()))

    assert page.locator(".briefing-doc-footer").count() == 0
    assert page.get_by_role("button", name="설정", exact=True).count() == 0


def test_saved_reports_are_managed_from_the_settings_button_only(page):
    """저장한 보고 절에 옛 관리 버튼이 남으면 설정 입구가 다시 둘로 갈린다."""

    document = _document(saved=[{
        "question": "쇼피 인도네시아 주간 매출",
        "answer": "약 28.5억원입니다.",
        "last_run_at": "2026-08-25T09:00:00",
    }])
    _mount(page, _payload(document))

    assert page.locator(".briefing-doc-section-action", has_text="관리").count() == 0
    assert page.get_by_role("button", name="설정", exact=True).count() == 0


def test_settings_button_opens_both_editors_in_one_dialog(page):
    """한쪽이 다시 별도 팝업으로 빠지면 '한 설정창'이라는 약속이 깨진다."""

    _mount_with_settings(page, _payload(_document()))
    page.evaluate("() => window.controller.openSettings()")

    dialog = page.get_by_role("dialog", name="설정", exact=True)
    assert dialog.is_visible()
    assert dialog.get_attribute("aria-modal") == "true"
    assert dialog.locator(".briefing-settings-section-title").all_inner_texts() == [
        "내가 저장한 보고",
        "잔디로 받기",
        "노션으로 받기",
    ]
    assert dialog.locator("#saved-new").is_visible()
    assert dialog.locator(".saved-item-q").inner_text() == "쇼피 인도네시아 주간 매출"
    assert dialog.locator(".briefing-jandi-input").is_visible()
    assert dialog.locator(".briefing-jandi-select").input_value() == "09:30"


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


def test_mail_lives_in_the_document_not_in_a_second_card(page):
    """같은 말을 두 번 하지 않는다 — 메일 목록은 **한 곳**에만 있다.

    ⛔ 예전엔 문서가 요약 있는 메일만 싣고 나머지를 `그 밖의 메일` 카드가 받았다.
       한 화면에 목록이 두 벌이라 **어느 쪽이 전부인지 알 수 없었다** (2026-08-26).
       이제 문서가 받은 메일을 전부 싣고 절 안에서 스크롤한다 — 카드는 안 만든다.
    """
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
    assert names == ["향후 일정"], "메일 카드가 또 있으면 목록이 두 벌이다"
    items = page.locator(".personal-briefing-card .briefing-doc-row-head").all_inner_texts()
    assert not any("메일" in text for text in items)
    assert not any("회의 today" in text for text in items)
    assert page.locator(".personal-briefing-item").count() == 0


def test_mail_card_is_the_safety_net_when_the_document_has_none(page):
    """⚠️ 카드를 아예 지우면 **문서가 비었을 때 메일이 통째로 사라진다** —
       문서 생성이 실패하거나 시간이 초과돼도 `mail.items` 에는 목록이 남는다."""
    payload = _with_cards(
        _document(),
        [],
        [{"id": "m9", "subject": "문서가 없을 때 보이는 메일", "from_display": "B",
          "received_at": "2026-08-25T08:00:00+09:00", "unread": True, "url": ""}],
    )
    payload["document"]["mail"] = []
    _mount(page, payload)
    names = page.locator(".personal-briefing-card-name").all_inner_texts()
    assert "받은 메일" in names
    items = page.locator(".personal-briefing-card .briefing-doc-row-head").all_inner_texts()
    assert "문서가 없을 때 보이는 메일" in items
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
    assert page.locator(".personal-briefing-skeleton").count() == 1
    names = page.locator(".personal-briefing-card-name").all_inner_texts()
    assert names == ["향후 일정"]
    for retired in ("오늘 우선 확인", "7일 일정", "오늘 메일", "업무 지표",
                    "내일부터", "그 밖의 메일"):
        assert retired not in names


# ── 가로 공간 활용 (2026-08-26) ───────────────────────────────────────────────

def test_the_body_splits_into_two_columns_on_a_wide_screen(page):
    """세로로만 쌓으면 넓은 화면에서 오른쪽이 통째로 비고 스크롤만 길어진다."""
    page.set_viewport_size({"width": 1400, "height": 1200})
    _mount(page, _payload(_document()))

    left = page.locator(".briefing-doc-cell.is-left .briefing-doc-section-name")
    right = page.locator(".briefing-doc-cell.is-right .briefing-doc-section-name")
    # 왼쪽은 오늘 '일어나는' 것, 오른쪽은 '해야 할' 것과 참고.
    assert left.all_inner_texts() == ["일정", "메일"]
    assert right.all_inner_texts() == ["할 일", "기한", "지표"]

    lx = page.locator(".briefing-doc-cell.is-left").first.bounding_box()
    rx = page.locator(".briefing-doc-cell.is-right").first.bounding_box()
    assert rx["x"] > lx["x"] + lx["width"] / 2, (lx, rx)


def test_rows_line_up_across_the_two_columns(page):
    """⛔ 열마다 따로 쌓으면 1행만 맞고 2행부터 어긋난다 — 오른쪽 절이 짧아 먼저 올라간다.

    2026-08-27 사용자 지적("행 길이가 같아야 함"). 측정으로 확인한 어긋남:
        1행 일정 top=424 / 할 일 top=424  (맞음)
        2행 메일 top=508 / 기한 top=420  (어긋남)
    각 절에 행 번호를 직접 주고 `align-items: start` 로 같은 지점에서 시작시킨다.
    """
    page.set_viewport_size({"width": 1400, "height": 1400})
    _mount(page, _payload(_document()))

    # ⛔ **칸(cell) 위쪽을 재지 마라 — 사람은 머리글 글자를 본다.**
    #    처음엔 칸만 쟀고 통과했는데, 화면에서는 2행이 18px 어긋나 있었다
    #    (2026-08-27 사용자 지적 "그렇게 안 보이는데?"). 원인은 머리글의
    #    `margin-top` 이 절마다 달랐던 것 — 스크롤되는 메일 절만 sticky 때문에 0 이었다.
    #    칸은 같은 높이에서 시작해도 글자는 어긋날 수 있다. 보이는 것을 재야 한다.
    # ⚠️ **칸 단위로** 짝을 짓는다. 마지막 오른쪽 칸은 여러 절을 담은 상자라
    #    절 단위로 세면 개수가 어긋나 엉뚱한 짝이 비교된다.
    def first_name_tops(side):
        cells = page.locator(f".briefing-doc-cell.is-{side}")
        out = []
        for i in range(cells.count()):
            name = cells.nth(i).locator(".briefing-doc-section-name").first
            out.append(round(name.bounding_box()["y"]))
        return out

    left_tops, right_tops = first_name_tops("left"), first_name_tops("right")
    assert left_tops and right_tops
    for row, (lt, rt) in enumerate(zip(left_tops, right_tops), start=1):
        assert abs(lt - rt) <= 1, f"{row}행 머리글이 어긋났다: 왼쪽 {lt} vs 오른쪽 {rt}"


def test_the_columns_stack_on_a_narrow_screen(page):
    """좁아지면 한 열로 접히고, 그때 순서는 DOM 순서 그대로여야 읽힌다.

    ⚠️ DOM 순서는 **왼쪽 전부 → 오른쪽 전부** 다. 행 정렬 때문에 섞어 두면
       좁은 화면에서 일정 → 할 일 → 메일 순으로 읽혀 흐름이 깨진다.
    """
    page.set_viewport_size({"width": 720, "height": 1400})
    _mount(page, _payload(_document()))

    cells = page.locator(".briefing-doc-cell")
    xs = [round(cells.nth(i).bounding_box()["x"]) for i in range(cells.count())]
    assert len(set(xs)) == 1, f"좁은 화면인데 한 줄로 안 접혔다: {xs}"

    names = page.locator(".briefing-doc-cell .briefing-doc-section-name").all_inner_texts()
    assert names == ["일정", "메일", "할 일", "기한", "지표"], names

    ys = [round(cells.nth(i).bounding_box()["y"]) for i in range(cells.count())]
    assert ys == sorted(ys), f"한 줄로 접혔는데 순서가 뒤섞였다: {ys}"


def test_upcoming_events_show_who_is_coming(page):
    """'향후 일정' 도 오늘 일정과 같이 참석자를 보여준다 (2026-08-26 사용자 요청)."""
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
    # 문서(오늘)와 카드(향후 일정) 두 곳이 같은 함수로 같은 줄을 만든다.
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


def test_every_section_rule_is_the_same_line(page):
    """⛔ 절 머리글의 **밑줄**이 이 문서의 구조다 — 길이나 높이가 다르면 격자가 깨진다.

    2026-08-27 사용자가 세 번 "조화롭지 않다" 고 한 것의 정체였다. 실측으로 셋이 나왔다:
      · 열 폭 1.4:1 → 같은 행의 두 선이 559px / 399px
      · 메일 절 스크롤바 8px → 같은 열 안에서 559 / 551 (오른쪽 끝이 들쭉날쭉)
      · `안읽음` 배지의 위아래 1px 패딩 → 메일 밑줄만 2px 아래
    셋 다 에러가 아니라 **눈에만 보이는** 결함이라 테스트가 없으면 다시 들어온다.
    """
    page.set_viewport_size({"width": 1400, "height": 1400})
    _mount(page, _payload(_document()))

    rules = page.locator(".briefing-doc-columns .briefing-doc-section-title")
    boxes = [rules.nth(i).bounding_box() for i in range(rules.count())]
    assert len(boxes) >= 4

    widths = {round(b["width"]) for b in boxes}
    assert len(widths) == 1, f"밑줄 길이가 제각각이다: {sorted(widths)}"

    # ⚠️ 칸 단위로 짝을 짓는다 — 마지막 오른쪽 칸은 여러 절을 담은 상자다.
    def first_rule_bottom(side):
        cells = page.locator(f".briefing-doc-cell.is-{side}")
        out = []
        for i in range(cells.count()):
            box = cells.nth(i).locator(".briefing-doc-section-title").first.bounding_box()
            out.append(round(box["y"] + box["height"]))
        return out

    for row, (lt, rt) in enumerate(
            zip(first_rule_bottom("left"), first_rule_bottom("right")), start=1):
        assert abs(lt - rt) <= 1, f"{row}행 밑줄이 어긋났다: 왼쪽 {lt} vs 오른쪽 {rt}"


def test_the_two_columns_are_equal_width(page):
    """⛔ 폭이 다르면 같은 행의 두 밑줄 길이가 달라진다 — 그것이 부조화의 첫 원인이었다."""
    page.set_viewport_size({"width": 1400, "height": 1400})
    _mount(page, _payload(_document()))

    left = page.locator(".briefing-doc-cell.is-left").first.bounding_box()
    right = page.locator(".briefing-doc-cell.is-right").first.bounding_box()
    assert abs(left["width"] - right["width"]) <= 1, (left["width"], right["width"])


def test_surplus_right_sections_sit_beside_the_mail_not_below_it(page):
    """⛔ 오른쪽 절마다 새 행을 만들면 긴 메일 절 **아래로** 밀린다 (2026-08-27 사용자
    지적: "메일 부분에 맞춰 저장한 질문과 지표가 칸에 맞아야 함").

    왼쪽 행 수를 넘는 절은 마지막 행에 함께 쌓아 메일 옆을 채운다 — 그래야 메일
    옆이 비지 않고 문서도 짧아진다 (실측: 1,010 → 750px).
    ⚠️ 같은 행·같은 열에 둘을 그냥 두면 그리드는 쌓지 않고 **겹친다.** 상자로 감싼다.
    """
    page.set_viewport_size({"width": 1400, "height": 1400})
    document = _document()
    document["saved"] = [{"question": "쇼피 인도네시아 이번 달 매출",
                          "answer": "약 28.5억원입니다. (표 1행)",
                          "last_run_at": "2026-08-27T09:00:00", "link": ""}]
    _mount(page, _payload(document))

    left_last = page.locator(".briefing-doc-cell.is-left").last
    right_last = page.locator(".briefing-doc-cell.is-right").last
    lb, rb = left_last.bounding_box(), right_last.bounding_box()

    # 마지막 왼쪽 절(메일)과 마지막 오른쪽 칸이 **같은 높이에서 시작**해야 한다.
    assert abs(lb["y"] - rb["y"]) <= 1, (lb["y"], rb["y"])

    # 남는 절이 여럿이면 하나의 상자로 묶여 그 안에서 세로로 흐른다 (겹치지 않는다).
    stack = page.locator(".briefing-doc-stack")
    if stack.count():
        names = stack.locator(".briefing-doc-section-name").all_inner_texts()
        assert len(names) >= 2, names
        boxes = [stack.locator(".briefing-doc-section").nth(i).bounding_box()
                 for i in range(len(names))]
        ys = [round(b["y"]) for b in boxes]
        assert ys == sorted(ys) and len(set(ys)) == len(ys), f"절이 겹쳤다: {ys}"


def _mail_scroll_payload(mail_count=24):
    """메일과 오른쪽 참고 절이 모두 긴 실제 렌더링용 데이터."""
    document = _document()
    template = document["mail"][0]
    document["mail"] = [
        {**template, "id": f"m{i}", "subject": f"{i + 1}. 채널별 예산 검토 요청"}
        for i in range(mail_count)
    ]
    document["mail_total"] = mail_count
    document["mail_unread"] = mail_count
    document["saved"] = [{
        "question": f"{i + 1}. 이번 달 채널별 매출과 지난달 실적 비교",
        "answer": "국가와 채널별 실적을 확인하고 주요 변동 항목을 비교합니다. " * 10,
        "last_run_at": "2026-08-25T09:00:00", "link": "",
    } for i in range(3)]
    return _payload(document)


def _mail_geometry(page):
    # The observer runs after layout; include its size update before measuring.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    return page.evaluate("""() => {
        const mail = document.querySelector('.briefing-doc-mail');
        const peer = document.querySelector('.briefing-doc-cell.is-right:last-child');
        const box = mail.getBoundingClientRect();
        const right = peer.getBoundingClientRect();
        return {top: box.top, height: box.height, rightTop: right.top,
            rightHeight: right.height, client: mail.clientHeight, scroll: mail.scrollHeight};
    }""")


def _wait_for_mail_to_match_right(page):
    page.wait_for_function("""() => {
        const mail = document.querySelector('.briefing-doc-mail').getBoundingClientRect();
        const peer = document.querySelector('.briefing-doc-cell.is-right:last-child').getBoundingClientRect();
        return peer.height > 420 && Math.abs(mail.height - peer.height) <= 1;
    }""", timeout=1500)


def test_long_mail_uses_the_height_of_the_right_sections(page):
    """오른쪽은 계속되는데 메일만 고정 상한에서 끝나면 화면 절반을 낭비한다."""
    page.set_viewport_size({"width": 1400, "height": 1200})
    _mount(page, _mail_scroll_payload())
    _wait_for_mail_to_match_right(page)
    geometry = _mail_geometry(page)
    assert abs(geometry["top"] - geometry["rightTop"]) <= 1
    assert geometry["scroll"] > geometry["client"]
    assert page.locator(".briefing-doc-mail .briefing-doc-row").count() == 24


def test_mail_height_tracks_wrapping_and_document_reopening(page):
    page.set_viewport_size({"width": 1400, "height": 1200})
    _mount(page, _mail_scroll_payload())
    _wait_for_mail_to_match_right(page)
    initial = _mail_geometry(page)

    page.set_viewport_size({"width": 1100, "height": 1200})
    _wait_for_mail_to_match_right(page)
    wrapped = _mail_geometry(page)
    assert wrapped["height"] > initial["height"]

    page.locator(".briefing-doc-head").click()
    page.locator(".briefing-doc-head").click()
    _wait_for_mail_to_match_right(page)


def test_short_mail_keeps_its_natural_height(page):
    page.set_viewport_size({"width": 1400, "height": 1200})
    _mount(page, _mail_scroll_payload(mail_count=1))
    geometry = _mail_geometry(page)
    assert geometry["rightHeight"] > 420
    assert geometry["height"] < geometry["rightHeight"] / 2
    assert geometry["scroll"] <= geometry["client"]


def test_mobile_mail_stays_bounded_and_its_last_row_is_reachable(page):
    page.set_viewport_size({"width": 390, "height": 640})
    _mount(page, _mail_scroll_payload())
    mail = page.locator(".briefing-doc-mail")
    geometry = _mail_geometry(page)
    assert geometry["height"] <= 420
    assert geometry["scroll"] > geometry["client"]
    assert page.locator(".briefing-doc-mail .briefing-doc-row").count() == 24
    mail.evaluate("el => { el.scrollTop = el.scrollHeight; }")
    last = mail.locator(".briefing-doc-row").last.bounding_box()
    bounds = mail.bounding_box()
    assert last["y"] + last["height"] <= bounds["y"] + bounds["height"] + 1
    assert last["y"] >= bounds["y"]
    heading = mail.locator(".briefing-doc-section-title").bounding_box()
    assert abs(heading["y"] - bounds["y"]) <= 1


@pytest.mark.parametrize("operation", ["refresh", "invalidate"])
def test_replacing_a_collapsed_document_releases_its_resize_observer(page, operation):
    """0px인 문서를 교체해도 크기가 안 변해 자동 resize 알림은 오지 않는다."""
    page.set_viewport_size({"width": 1400, "height": 1200})
    # Native ResizeObserver still measures and delivers every real callback.
    page.evaluate("""() => {
        const NativeObserver = window.ResizeObserver;
        window.mailObserverRecords = [];
        window.ResizeObserver = class extends NativeObserver {
            constructor(callback) {
                const record = {height: null, disconnected: false};
                super((entries, observer) => {
                    record.height = entries[0].contentRect.height;
                    callback(entries, observer);
                });
                this.record = record;
                mailObserverRecords.push(record);
            }
            disconnect() {
                this.record.disconnected = true;
                super.disconnect();
            }
        };
    }""")
    _mount(page, _mail_scroll_payload())
    _wait_for_mail_to_match_right(page)
    page.locator(".briefing-doc-head").click()
    page.wait_for_function("mailObserverRecords[0].height === 0")

    page.evaluate("async operation => { await controller[operation](); }", operation)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert page.evaluate("mailObserverRecords[0].disconnected") is True
    if operation == "refresh":
        page.locator(".briefing-doc-head").click()
        _wait_for_mail_to_match_right(page)
