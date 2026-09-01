"""잔디로 받을 항목을 사용자가 고른다 + 참조(CC) 메일은 할 일이 아니다.

두 규칙 모두 **틀려도 에러가 안 난다** — 안 받겠다고 껐는데 그대로 오거나,
참조 메일이 할 일에 섞여 진짜 내 일이 뒤로 밀린다. 그래서 양방향으로 건다:
꺼야 할 것이 꺼지는가 / **끄지 말아야 할 것이 남는가**.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core import jandi_briefing, work_briefing


# ── 저장 형식 ────────────────────────────────────────────────────────────────

def test_muted_list_stores_what_is_turned_off_not_what_is_on():
    """⛔ **끈 것**을 저장한다. 켠 목록으로 두면 나중에 항목이 하나 늘었을 때
    이미 설정을 저장해 둔 사람에게는 영영 안 보인다 — 에러도 흔적도 없다."""

    assert jandi_briefing.parse_muted("") == frozenset()
    assert jandi_briefing.wants("", "mail"), "아무것도 안 껐으면 전부 받는다"
    # 새 항목이 생겨도 옛 설정에 없으므로 자동으로 켜진다
    assert jandi_briefing.wants("mail,fx", "새로_생긴_절")


def test_unknown_keys_are_dropped_instead_of_stored():
    """모르는 키를 저장하면 화면에 그릴 수 없는 설정이 남는다."""

    assert jandi_briefing.parse_muted("mail,없는키,fx") == {"mail", "fx"}
    assert jandi_briefing.serialize_muted(["fx", "mail", "?"]) == "mail,fx"


def test_every_briefing_section_key_exists_in_the_document():
    """⛔ 절 키가 문서 키와 어긋나면 **끈 줄 알았는데 그대로 나간다.**

    체크박스는 꺼지는데 본문은 그대로다 — 사용자는 설정이 안 먹는다고만 느낀다.
    """

    document = {
        "meetings": [{"urgency": "low", "time": "10:00", "title": "회의"}],
        "mail": [{"urgency": "low", "from": "김", "subject": "제목"}],
        "mail_total": 1, "mail_unread": 1, "mail_summary": "요약",
        "actions": [{"urgency": "low", "text": "할 일"}],
        "deadlines": [{"urgency": "low", "label": "9/3", "text": "마감"}],
        "saved": [{"question": "질문", "answer": "답"}],
        "for_date": "2026-09-01", "weekday": "월", "window": {},
    }
    fx = {"for_date": "2026-09-01",
          "items": [{"currency": "USD", "unit": 1, "krw": 1380}]}
    business = {"items": [{"title": "매출", "body": "· 12.3억"}]}

    everything = work_briefing.render_markdown(document, fx=fx, business=business)
    for key in jandi_briefing.BRIEFING_SECTION_KEYS:
        without = work_briefing.render_markdown(
            document, fx=fx, business=business, muted={key})
        assert len(without) < len(everything), f"'{key}' 를 껐는데 본문이 그대로다"


def test_muting_one_section_leaves_the_others_alone():
    """⚠️ 한 절을 끄면서 옆 절까지 지우면 안 된다."""

    document = {
        "for_date": "2026-09-01", "weekday": "월", "window": {},
        "meetings": [{"urgency": "low", "time": "10:00", "title": "주간회의"}],
        "mail": [{"urgency": "low", "from": "김", "subject": "견적 회신 요청"}],
        "mail_total": 3, "mail_unread": 1, "mail_summary": "견적 1건",
        "actions": [{"urgency": "high", "text": "견적서 회신"}],
    }
    text = work_briefing.render_markdown(document, muted={"mail"})

    assert "견적 회신 요청" not in text, "메일을 껐는데 제목이 남았다"
    assert "견적 1건" not in text, "메일을 껐는데 요약이 남았다"
    assert "주간회의" in text and "견적서 회신" in text, "옆 절까지 지웠다"


def test_screen_document_is_never_muted():
    """⛔ 끈 것은 "잔디로 안 받는다" 이지 "안 본다" 가 아니다.

    첫 화면까지 지우면 그 사람은 어디서도 그 내용을 볼 수 없게 된다.
    """

    document = {"for_date": "2026-09-01", "weekday": "월", "window": {},
                "mail": [{"urgency": "low", "from": "김", "subject": "견적"}],
                "mail_total": 1, "mail_unread": 0}
    assert "견적" in work_briefing.render_markdown(document), \
        "muted 를 안 줬는데 걸러졌다 — 화면 경로가 오염됐다"


# ── 다 껐을 때 ───────────────────────────────────────────────────────────────

def test_content_sections_reports_only_what_would_be_shown():
    empty = {"for_date": "2026-09-01", "weekday": "월", "window": {}}
    assert work_briefing.content_sections(empty) == set()

    filled = {**empty, "meetings": [{"t": 1}], "mail_total": 2}
    assert work_briefing.content_sections(filled) == {"meetings", "mail"}

    # 환율·지표는 문서가 아니라 봉투에서 온다 — 키 이름이 어긋나면
    # 끄기 판정이 조용히 빗나간다 (실제로 `rates` 라고 잘못 적었다가 걸렸다).
    assert work_briefing.content_sections(
        empty, fx={"items": [{"currency": "USD", "unit": 1, "krw": 1380}]},
        business={"items": [{"title": "매출", "body": "12.3억"}]},
    ) == {"fx", "business"}


def test_everything_muted_means_nothing_to_send():
    """⛔ 실릴 것을 전부 끈 사람에게 머리말만 보내지 마라 — 매일 오는 빈 알림은
    곧 무시당하고, 그러면 안 끈 사람의 브리핑까지 함께 안 읽힌다."""

    document = {"for_date": "2026-09-01", "weekday": "월", "window": {},
                "meetings": [{"urgency": "low", "time": "10:00", "title": "회의"}]}
    present = work_briefing.content_sections(document)

    assert present and present <= {"meetings"}, "이 문서는 일정 하나뿐이어야 한다"
    assert not (present <= {"mail"}), "안 끈 절이 남았으면 보내야 한다"


# ── 알림 종류 ────────────────────────────────────────────────────────────────

def test_notification_kinds_are_selectable_and_match_real_kinds():
    """알림 키가 실제 `kind` 값과 같아야 한다 — 다르면 체크를 꺼도 계속 온다."""

    keys = {key for key, _, group in jandi_briefing.SECTIONS if group == "알림"}
    assert keys == {"report_share", "feedback"}
    assert keys <= set(jandi_briefing.KIND_META), "KIND_META 에 없는 종류를 그리고 있다"


def test_muted_notification_is_skipped_but_others_still_go(monkeypatch):
    from app.core import jandi_notify

    items = [{"kind": "report_share", "title": "8월 보고서", "dedup_key": "report_share:7"},
             {"kind": "feedback", "title": "차트가 이상해요", "note": "고쳤습니다",
              "dedup_key": "feedback:3"}]
    monkeypatch.setattr(jandi_notify, "pending_items", lambda user_id, now=None: items)
    monkeypatch.setattr(jandi_notify, "link_for", lambda kind, item: "")
    queued: list[str] = []
    monkeypatch.setattr(
        jandi_briefing, "enqueue",
        lambda *a, **k: queued.append(k["kind"]) or True)

    jandi_notify.enqueue_for_user(7, "https://wh.jandi.com/connect-api/webhook/1/aaaa",
                                  today=date(2026, 9, 1), muted="report_share")

    assert queued == ["feedback"], "끈 종류가 나갔거나 안 끈 종류를 막았다"


# ── 참조(CC) 메일 ────────────────────────────────────────────────────────────

def _mail(mid: str, subject: str, cc_only: bool) -> dict:
    return {"id": mid, "subject": subject, "cc_only": cc_only,
            "received_at": "2026-09-01T09:00:00+09:00", "url": "https://mail",
            "snippet": subject}


def test_cc_only_mail_does_not_become_an_action():
    """⛔ 참조로만 온 메일은 내 할 일이 아니다 (2026-09-01 사용자 지시).
    받는 사람이 따로 있는데 할 일에 섞이면 진짜 내 일이 뒤로 밀린다."""

    mails = [_mail("m1", "견적서 회신 부탁드립니다", False),
             _mail("m2", "주간 공유 참고 바랍니다", True)]
    raw = {"actions": [
        {"text": "견적서 회신", "source": "mail", "source_id": "m1", "urgency": "high"},
        {"text": "참고 바랍니다", "source": "mail", "source_id": "m2", "urgency": "low"},
    ]}
    dropped: list[str] = []
    rows = work_briefing._action_rows(
        raw, [], mails, work_briefing._haystack([], mails), dropped)

    assert [row["text"] for row in rows] == ["견적서 회신"]
    assert "action:cc_only" in dropped, "버린 사실을 남기지 않았다"


def test_direct_mail_still_becomes_an_action():
    """⚠️ 반대 방향. 참조 판정이 과하게 걸리면 할 일이 통째로 사라진다."""

    mails = [_mail("m1", "견적서 회신 부탁드립니다", False)]
    raw = {"actions": [{"text": "견적서 회신", "source": "mail",
                        "source_id": "m1", "urgency": "high"}]}
    rows = work_briefing._action_rows(
        raw, [], mails, work_briefing._haystack([], mails), [])
    assert len(rows) == 1


def test_mail_without_a_cc_verdict_is_kept():
    """⛔ 모르면 지우지 않는다. 메일링 그룹으로 와서 내 주소가 어느 칸에도 없는
    메일은 참조가 아니다 — 그걸 참조로 찍으면 진짜 할 일이 조용히 사라진다."""

    mails = [{"id": "m1", "subject": "견적서 회신 부탁드립니다",
              "received_at": "2026-09-01T09:00:00+09:00", "url": "u",
              "snippet": "견적서 회신 부탁드립니다"}]  # cc_only 키 자체가 없다
    raw = {"actions": [{"text": "견적서 회신", "source": "mail",
                        "source_id": "m1", "urgency": "high"}]}
    rows = work_briefing._action_rows(
        raw, [], mails, work_briefing._haystack([], mails), [])
    assert len(rows) == 1


@pytest.mark.parametrize("to_header,cc_header,expected", [
    ("me@skin1004korea.com", "other@x.com", False),
    ("other@x.com", "me@skin1004korea.com", True),
    ("Me <me@skin1004korea.com>", "ME@SKIN1004KOREA.COM", False),
    ("team-all@skin1004korea.com", "", False),   # 그룹 메일 — 참조가 아니다
    ("", "", False),
])
def test_cc_detection_reads_both_headers(to_header, cc_header, expected):
    """⛔ **참조 칸에 있고 받는 칸에 없을 때만** 참조다.

    어느 칸에도 없으면(그룹 메일) 참조가 아니다 — 모르는 것을 참조로 찍으면
    진짜 할 일이 사라진다.
    """
    from app.core.google_workspace import _addresses

    me = "me@skin1004korea.com"
    verdict = me in _addresses(cc_header) and me not in _addresses(to_header)
    assert verdict is expected
