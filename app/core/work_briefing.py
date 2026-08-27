"""출근 브리핑 문서 — 일정·메일을 한 장으로 묶는다 (첫 화면 + 잔디 공용 본문).

설계의 전부는 **LLM 이 사실을 만들지 못하게 하는 것**이다. 이 파이프라인에서
LLM 이 하는 일은 *문장을 고르는 것*뿐이고, 다음 넷은 코드가 판정한다:

  1. 모든 항목은 실재하는 event_id / message_id 에 묶인다 — 못 묶이면 버린다
  2. 문장 속 숫자가 원문(제목·미리보기·시간·장소)에 없으면 그 항목을 버린다
     (`answer_check` · 보고서 서술 검증과 같은 사상. 통계처럼 생긴 값이 가장 잘 믿긴다)
  3. 🔴/🟡 는 규칙이 정한다 — 3시간 내 시작, 오늘·내일 마감이면 LLM 판단과 무관하게 🔴
  4. 마감일은 파싱되고 상식적 범위(어제~45일 뒤) 안일 때만 남는다

⛔ 버린 것은 조용히 사라지지 않는다. WARNING 으로 남기고 건수를 문서에 싣는다.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from app.core.llm import get_flash_client

logger = structlog.get_logger()

SEOUL = ZoneInfo("Asia/Seoul")

MAX_MEETINGS = 12
#: LLM 이 돌려준 `mail_points` 를 몇 개까지 훑을지. **행 상한이 아니다** —
#: 이 값으로 행을 자르면 안 읽은 메일이 읽은 메일의 자리를 먹는다 (2026-08-26).
#: 폭주한 응답에서 멈추기 위한 안전판일 뿐이라 행 상한보다 넉넉해도 된다.
MAX_LLM_MAIL_ENTRIES = 60
#: 절 전체 행 상한. 제목만 있는 행은 한 줄이라 싸다.
#: ⛔ 안 읽은 메일은 이 상한까지 **요약 없이라도** 자리를 갖는다 — Today 의 이 절은
#:    "아직 안 본 것" 이 한자리에 모이는 것이 존재 이유다.
#: ⚠️ 화면은 절 안에서 **스크롤**하므로 길어져도 다른 절을 밀어내지 않는다
#:    (2026-08-26 사용자 지시: "카드 내 스크롤을 통해 공간 확보"). 그래서 수집 상한
#:    (Gmail 20~40건) 만큼 넉넉히 잡는다 — 자르는 것보다 스크롤이 낫다.
MAX_MAIL_ROWS = 40

#: ⛔ **채팅 본문은 스크롤이 없다.** 잔디에 40줄을 밀어 넣으면 아무도 안 읽는다 —
#:    화면 상한과 같은 자를 쓰면 안 된다. 넘치면 "…외 N건" 으로 줄인다.
MAX_MAIL_LINES_IN_TEXT = 10
MAX_ACTIONS = 6
MAX_DEADLINES = 6
MAX_URGENT = 5
DEADLINE_PAST_GRACE_DAYS = 1
DEADLINE_HORIZON_DAYS = 45
URGENT_EVENT_HOURS = 3

#: 검증 없이 통과시키는 숫자. 날짜·개수·서수·연도처럼 지어내도 해롭지 않은 값만이다.
#: ⚠️ 넓히면 검출력이 죽는다 — 금액·비율이 여기로 새면 방어가 없어진다.
_SAFE_NUMBER_MAX = 31
_SAFE_YEARS = {str(year) for year in range(2020, 2036)}

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


# ── 시간 표기 ────────────────────────────────────────────────────────────────

def _parse(value: str) -> datetime | None:
    text = str(value or "")
    try:
        if len(text) == 10:
            return datetime.fromisoformat(text).replace(tzinfo=SEOUL)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=SEOUL) if parsed.tzinfo is None else parsed.astimezone(SEOUL)


def _time_label(item: dict[str, Any]) -> str:
    if item.get("all_day"):
        return "종일"
    start, end = _parse(str(item.get("start", ""))), _parse(str(item.get("end", "")))
    if not start:
        return "시간 미정"
    if not end:
        return start.strftime("%H:%M")
    return f"{start:%H:%M}~{end:%H:%M}"


def _clock(value: str) -> str:
    """수신·시작 시각의 HH:MM. 화면 왼쪽 시간축이 이 값으로 정렬된다."""

    parsed = _parse(str(value or ""))
    return parsed.strftime("%H:%M") if parsed else ""


def is_today(item: dict[str, Any], day: date) -> bool:
    start = _parse(str(item.get("start", "")))
    return bool(start and start.date() == day)


# ── 숫자 검증 ────────────────────────────────────────────────────────────────

def _normalize_number(token: str) -> str:
    return token.replace(",", "").rstrip(".")


def unsupported_numbers(text: str, haystack: str) -> list[str]:
    """원문으로 설명되지 않는 숫자만 돌려준다 (빈 목록이면 통과)."""

    bad = []
    for raw in _NUMBER_RE.findall(str(text or "")):
        token = _normalize_number(raw)
        if not token:
            continue
        if token in _SAFE_YEARS:
            continue
        try:
            if "." not in token and int(token) <= _SAFE_NUMBER_MAX:
                continue
        except ValueError:
            continue
        if token in haystack or raw in haystack:
            continue
        bad.append(raw)
    return bad


def _haystack(events: list[dict[str, Any]], mails: list[dict[str, Any]]) -> str:
    parts = []
    for event in events:
        parts += [
            str(event.get("title", "")), str(event.get("location", "")),
            _time_label(event), str(event.get("description", "")),
            " ".join(str(name) for name in event.get("attendees", []) or []),
        ]
    for mail in mails:
        parts += [
            str(mail.get("subject", "")), str(mail.get("from_display", "")),
            str(mail.get("snippet", "")), str(mail.get("received_at", "")),
        ]
    return _normalize_number(" \n ".join(parts))


# ── LLM ──────────────────────────────────────────────────────────────────────

_PROMPT = """너는 출근 직후 5분 안에 오늘을 파악하게 돕는 비서다.
아래 '오늘 일정'과 '받은 메일 미리보기'만 근거로 JSON 을 만들어라.

⛔ 절대 규칙
- 입력에 없는 사실·숫자·날짜·사람 이름을 쓰지 마라. 특히 금액·비율·건수를 추측하지 마라.
- event_id / message_id 는 입력값을 그대로 복사해라. 새로 만들면 그 항목은 버려진다.
- 메일 본문 전체가 아니라 미리보기만 주어졌다. 잘린 문장을 완성하지 마라.
- 확실하지 않으면 그 항목을 빼라. 빈 배열이 틀린 문장보다 낫다.

작성 지침
- prep: 그 회의 직전에 챙길 것 한 줄. 관련 메일이 있으면 그 내용을 근거로 써라. 없으면 "".
- mail_summary: 전체 흐름 2~3문장. 무엇이 왔고 무엇이 나를 기다리는지.
- points: 메일 한 건의 핵심 2개 이내. request 는 나에게 요청된 행동(없으면 "").
- actions: 오늘 내가 회신하거나 조치할 일. 반드시 근거 메일/일정에 묶어라.
- deadlines: 오늘 또는 금주 안에 기한이 있는 것만. date 는 YYYY-MM-DD.
- urgency: 오늘 안에 못 하면 문제가 되는 것만 "high", 나머지는 "normal".

JSON 스키마
{"meetings":[{"event_id":"","prep":"","urgency":"normal"}],
 "mail_summary":"",
 "mail_points":[{"message_id":"","points":[""],"request":"","urgency":"normal"}],
 "actions":[{"text":"","source":"mail","source_id":"","urgency":"normal"}],
 "deadlines":[{"date":"","text":"","source":"mail","source_id":"","urgency":"normal"}]}
"""


def _llm_payload(events: list[dict[str, Any]], mails: list[dict[str, Any]], day: date) -> str:
    return json.dumps({
        "today": day.isoformat(),
        "events": [{
            "event_id": event.get("id", ""),
            "time": _time_label(event),
            "title": event.get("title", ""),
            "location": event.get("location", ""),
            "attendees": (event.get("attendees") or [])[:10],
            "description": str(event.get("description", ""))[:300],
        } for event in events],
        "mails": [{
            "message_id": mail.get("id", ""),
            "from": mail.get("from_display", ""),
            "subject": mail.get("subject", ""),
            "received": mail.get("received_at", ""),
            "preview": str(mail.get("snippet", ""))[:500],
        } for mail in mails],
    }, ensure_ascii=False)


def generate(events: list[dict[str, Any]], mails: list[dict[str, Any]], day: date) -> dict[str, Any]:
    """LLM 원문 JSON. 파싱까지만 하고 검증은 호출부(`compose`)가 한다."""

    if not events and not mails:
        return {}
    text = get_flash_client().generate_json(
        _PROMPT + "\n\n입력:\n" + _llm_payload(events, mails, day),
        temperature=0.1,
        max_output_tokens=8000,
    )
    value = json.loads(text)
    return value if isinstance(value, dict) else {}


# ── 검증 ─────────────────────────────────────────────────────────────────────

def _clean(value: Any, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _urgency(value: Any) -> str:
    return "high" if str(value or "").lower() == "high" else "normal"


def _verified(text: str, haystack: str, dropped: list[str], where: str) -> bool:
    bad = unsupported_numbers(text, haystack)
    if bad:
        dropped.append(f"{where}:{','.join(bad)}")
        return False
    return True


def _meeting_rows(
    raw: dict[str, Any], events: list[dict[str, Any]], haystack: str,
    now: datetime, dropped: list[str],
) -> list[dict[str, Any]]:
    prep_by_id: dict[str, dict[str, Any]] = {}
    for entry in raw.get("meetings", []) or []:
        if isinstance(entry, dict) and entry.get("event_id"):
            prep_by_id[str(entry["event_id"])] = entry

    rows = []
    for event in events[:MAX_MEETINGS]:
        entry = prep_by_id.get(str(event.get("id", "")), {})
        prep = _clean(entry.get("prep", ""))
        if prep and not _verified(prep, haystack, dropped, "prep"):
            prep = ""
        start = _parse(str(event.get("start", "")))
        soon = bool(
            start and not event.get("ended")
            and now <= start <= now + timedelta(hours=URGENT_EVENT_HOURS)
        )
        rows.append({
            "id": str(event.get("id", "")),
            "time": _time_label(event),
            "title": _clean(event.get("title", ""), 120) or "(제목 없음)",
            "location": _clean(event.get("location", ""), 80),
            "attendees": [_clean(name, 30) for name in (event.get("attendees") or [])[:12]],
            "attendee_count": len(event.get("attendees") or []),
            "conference_url": str(event.get("conference_url", "")),
            "url": str(event.get("url", "")),
            "prep": prep,
            "ended": bool(event.get("ended")),
            "declined": bool(event.get("declined")),
            # ⛔ 임박 판정은 규칙이 이긴다. LLM 은 시계를 보지 않는다.
            "urgency": "high" if soon else _urgency(entry.get("urgency")),
        })
    return rows


def _mail_rows(
    raw: dict[str, Any], mails: list[dict[str, Any]], haystack: str, dropped: list[str],
) -> list[dict[str, Any]]:
    by_id = {str(mail.get("id", "")): mail for mail in mails}
    rows = []
    for entry in raw.get("mail_points", []) or []:
        if not isinstance(entry, dict):
            continue
        mail = by_id.get(str(entry.get("message_id", "")))
        if not mail:
            dropped.append("mail_point:unknown_id")
            continue
        points = [
            _clean(point) for point in (entry.get("points") or [])[:2]
            if _clean(point) and _verified(_clean(point), haystack, dropped, "point")
        ]
        request = _clean(entry.get("request", ""))
        if request and not _verified(request, haystack, dropped, "request"):
            request = ""
        if not points and not request:
            continue
        rows.append({
            "id": mail["id"],
            "at": _clock(str(mail.get("received_at", ""))),
            "from": _clean(mail.get("from_display", ""), 60),
            "subject": _clean(mail.get("subject", ""), 140) or "(제목 없음)",
            "url": str(mail.get("url", "")),
            "unread": bool(mail.get("unread")),
            "points": points,
            "request": request,
            "urgency": "high" if (request and _urgency(entry.get("urgency")) == "high") else "normal",
        })
        # ⚠️ 여기서 자르지 않는다 — 고르는 일은 `_fit` 이 한다 (안 읽은 것 우선).
        #    여기서 캡을 걸면 LLM 이 고른 순서대로 잘려 규칙이 두 곳으로 흩어진다.
        if len(rows) >= MAX_LLM_MAIL_ENTRIES:
            break

    # ⛔ **요약거리가 없어도 올린다** (2026-08-26 제보:
    #    "안읽은 메일이 나오는 기준을 모르겠다 / 일부여서 이상함").
    #    지금까지 이 절에 오르는 기준은 **LLM 이 요약할 거리를 찾았는가** 하나였고,
    #    읽음 여부는 아예 보지 않았다. 그래서 안 읽은 5건 중 1건만 Today 에 오르고
    #    나머지는 아래 카드에 흩어졌다 — 사용자에게는 규칙이 없어 보인다.
    #    Today 의 메일 절은 "아직 안 본 것" 이 한자리에 모여야 쓸모가 있다.
    # ⚠️ 요약 없이 제목만 실린다. 그것이 지어내는 것보다 낫다 —
    #    미리보기에 근거가 없으면 `_verified` 가 어차피 문장을 버린다.
    listed = {row["id"] for row in rows}
    for mail in mails:
        if str(mail.get("id", "")) in listed:
            continue
        rows.append({
            "id": mail["id"],
            "at": _clock(str(mail.get("received_at", ""))),
            "from": _clean(mail.get("from_display", ""), 60),
            "subject": _clean(mail.get("subject", ""), 140) or "(제목 없음)",
            "url": str(mail.get("url", "")),
            "unread": bool(mail.get("unread")),
            "points": [],
            "request": "",
            "urgency": "normal",
        })

    # ⚠️ 그룹 안 순서는 **최신순**이다. 위에서 LLM 이 고른 것을 먼저 담고 나머지를
    #    뒤에 붙였기 때문에, 그대로 두면 15:01 → 12:03 → 14:08 처럼 시간이 튄다
    #    (2026-08-26 실측). 화면의 시간축이 거짓말하는 것처럼 보인다.
    #    ⛔ `at`(HH:MM)으로 정렬하면 안 된다 — 어제 22:16 이 맨 위로 온다.
    received = {str(mail.get("id", "")): str(mail.get("received_at", "")) for mail in mails}
    rows.sort(key=lambda row: received.get(str(row["id"]), ""), reverse=True)
    return _fit(rows)


def _fit(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """자리에 맞춰 고르고, **잘라낸 수를 함께 돌려준다**.

    ⛔ 예전엔 `rows[:MAX]` 로 조용히 잘랐다. 목록이 멀쩡히 보이므로 **몇 건이
       빠졌는지 아무도 알 수 없다** — 이 프로젝트에서 반복된 부류의 실패다.
       그래서 호출부가 그 수를 받아 문서에 싣고, 화면·잔디가 밝힌다.

    규칙 두 줄:
      1. 안 읽은 것이 먼저 자리를 갖는다.
      2. 남은 자리는 읽은 것이 도착순으로 채운다 — **요약이 있든 없든** 싣는다.

    ⛔ 예전엔 요약이 없는 읽은 메일을 뺐다 ("이 절이 받은편지함이 된다"). 그 결과
       같은 메일이 아래 `그 밖의 메일` 카드에 다시 나왔고, **한 화면에 메일 목록이
       두 벌**이 됐다 — 어느 쪽이 전부인지 알 수 없다. 절이 안에서 스크롤하게
       된 지금은 길이가 문제가 아니므로, 한곳에 다 모으고 카드를 걷는 편이 낫다
       (2026-08-26 사용자 확인).

    ⛔ **요약 개수로 행을 자르지 않는다** (2026-08-26 제보: "13개 중 안읽음 7개는
       나오는데 읽음은 한 개만 나온다"). 예전엔 요약이 붙은 행을 통틀어 8개로
       묶었는데, 안 읽은 7건이 그 자리를 먼저 써서 **읽은 메일이 1건만 남았다.**
       화면 자리를 아끼려던 상한인데 이제 절이 **안에서 스크롤**하므로 아낄 이유가
       없다 — 자리는 스크롤이 만든다. 남는 상한은 행 수(`MAX_MAIL_ROWS`) 하나다.
    """
    unread = [row for row in rows if row["unread"]]
    read = [row for row in rows if not row["unread"]]

    kept: list[dict[str, Any]] = list(unread[:MAX_MAIL_ROWS])
    kept.extend(read[:max(0, MAX_MAIL_ROWS - len(kept))])

    kept_ids = {row["id"] for row in kept}
    omitted_unread = sum(1 for row in unread if row["id"] not in kept_ids)
    omitted_read = sum(1 for row in read if row["id"] not in kept_ids)
    return kept, omitted_unread, omitted_read


def _source_ref(
    source: str, source_id: str, events: list, mails: list,
) -> tuple[str, str, str] | None:
    """(링크, 시각, 근거 이름). 근거를 못 찾으면 ``None`` — 호출부가 그 항목을 버린다.

    시각은 화면 왼쪽 시간축에 쓴다. 할 일에는 그 자체의 시각이 없어 **근거가 도착한
    시각**을 쓰는데, 그대로 두면 "그 시각에 하는 일" 로 읽힌다 — 근거 이름을 함께 준다.
    """

    pool = mails if source == "mail" else events
    for item in pool:
        if str(item.get("id", "")) != source_id:
            continue
        if source == "mail":
            stamp = item.get("received_at")
            origin = _clean(item.get("subject", ""), 90)
        else:
            stamp = item.get("start")
            origin = _clean(item.get("title", ""), 90)
        return str(item.get("url", "")), _clock(str(stamp or "")), origin
    return None


def _action_rows(
    raw: dict[str, Any], events: list[dict[str, Any]], mails: list[dict[str, Any]],
    haystack: str, dropped: list[str],
) -> list[dict[str, Any]]:
    rows = []
    for entry in raw.get("actions", []) or []:
        if not isinstance(entry, dict):
            continue
        text = _clean(entry.get("text", ""))
        source = "mail" if str(entry.get("source", "")) == "mail" else "calendar"
        source_id = str(entry.get("source_id", ""))
        ref = _source_ref(source, source_id, events, mails)
        if not text or ref is None:
            dropped.append("action:unknown_id" if text else "action:empty")
            continue
        if not _verified(text, haystack, dropped, "action"):
            continue
        rows.append({
            "text": text, "source": source, "source_id": source_id,
            "url": ref[0], "at": ref[1], "origin": ref[2],
            "urgency": _urgency(entry.get("urgency")),
        })
        if len(rows) >= MAX_ACTIONS:
            break
    return rows


def _deadline_rows(
    raw: dict[str, Any], events: list[dict[str, Any]], mails: list[dict[str, Any]],
    haystack: str, day: date, dropped: list[str],
) -> list[dict[str, Any]]:
    lower = day - timedelta(days=DEADLINE_PAST_GRACE_DAYS)
    upper = day + timedelta(days=DEADLINE_HORIZON_DAYS)
    rows = []
    for entry in raw.get("deadlines", []) or []:
        if not isinstance(entry, dict):
            continue
        text = _clean(entry.get("text", ""))
        source = "mail" if str(entry.get("source", "")) == "mail" else "calendar"
        source_id = str(entry.get("source_id", ""))
        ref = _source_ref(source, source_id, events, mails)
        try:
            due = date.fromisoformat(str(entry.get("date", "")))
        except ValueError:
            dropped.append("deadline:bad_date")
            continue
        if not text or ref is None or not (lower <= due <= upper):
            dropped.append("deadline:unknown_id" if ref is None else "deadline:out_of_range")
            continue
        if not _verified(text, haystack, dropped, "deadline"):
            continue
        rows.append({
            "date": due.isoformat(),
            "label": _due_label(due, day),
            "text": text, "source": source, "source_id": source_id, "url": ref[0],
            # ⛔ 오늘·내일 마감은 LLM 판단과 무관하게 긴급이다.
            "urgency": "high" if due <= day + timedelta(days=1) else _urgency(entry.get("urgency")),
        })
        if len(rows) >= MAX_DEADLINES:
            break
    rows.sort(key=lambda row: row["date"])
    return rows


def _due_label(due: date, day: date) -> str:
    gap = (due - day).days
    if gap <= 0:
        return "오늘"
    if gap == 1:
        return "내일"
    weekday = "월화수목금토일"[due.weekday()]
    return f"{due.month}/{due.day}({weekday})"


def _cap_urgent(document: dict[str, Any]) -> int:
    """🔴 가 흔해지면 아무 뜻도 없어진다. 임박한 것부터 최대 5개만 남긴다."""

    buckets = [
        document["meetings"], document["deadlines"],
        document["actions"], document["mail"],
    ]
    urgent = [row for rows in buckets for row in rows if row.get("urgency") == "high"]
    for row in urgent[MAX_URGENT:]:
        row["urgency"] = "normal"
    return min(len(urgent), MAX_URGENT)


def _saved_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """저장 질문을 화면·잔디가 함께 쓰는 짧은 문서 행으로 바꾼다."""

    result: list[dict[str, str]] = []
    for row in rows or []:
        question = _clean(row.get("question", ""), 500)
        if not question:
            continue
        ran_at = row.get("last_run_at")
        if isinstance(ran_at, datetime):
            ran_at_text = ran_at.isoformat(timespec="seconds")
        else:
            ran_at_text = str(ran_at or "")
        result.append({
            "question": question,
            # ⚠️ 브리핑은 훑어보는 문서다. 답변 전문을 싣지 않아야 매일 읽을 길이를 지킨다.
            "answer": _clean(row.get("last_answer", ""), 300),
            "last_run_at": ran_at_text,
            "link": str(row.get("link") or ""),
        })
    return result


def compose(
    *,
    day: date,
    now: datetime,
    events: list[dict[str, Any]],
    mails: list[dict[str, Any]],
    window: dict[str, Any],
    raw: dict[str, Any] | None,
    saved: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """검증을 통과한 항목만으로 브리핑 문서를 만든다."""

    current = now.astimezone(SEOUL)
    today_events = [event for event in events if is_today(event, day)]
    haystack = _haystack(today_events, mails)
    dropped: list[str] = []
    raw = raw or {}

    summary = _clean(raw.get("mail_summary", ""), 400)
    if summary and not _verified(summary, haystack, dropped, "summary"):
        summary = ""

    # ⛔ 자리가 모자라 잘린 수를 **문서에 싣는다.** 화면에 몇 줄이 멀쩡히 보이면
    #    무엇이 빠졌는지 알 수 없다 — 자른 사실은 코드가 공시한다.
    mail_rows, omitted_unread, omitted_read = _mail_rows(raw, mails, haystack, dropped)

    document = {
        "status": "ready" if (today_events or mails) else "empty",
        "for_date": day.isoformat(),
        "weekday": "월화수목금토일"[day.weekday()],
        "window": window,
        "mail_summary": summary,
        "meetings": _meeting_rows(raw, today_events, haystack, current, dropped),
        "mail": mail_rows,
        "actions": _action_rows(raw, today_events, mails, haystack, dropped),
        "deadlines": _deadline_rows(raw, today_events, mails, haystack, day, dropped),
        "mail_total": len(mails),
        "mail_unread": sum(1 for mail in mails if mail.get("unread")),
        "mail_omitted_unread": omitted_unread,
        "mail_omitted_read": omitted_read,
        "dropped": len(dropped),
    }
    saved_rows = _saved_rows(saved)
    # ⛔ 빈 절은 화면만 길게 만든다. 항목이 있을 때만 키를 만들어 모든 렌더러가 같은 규칙을 쓴다.
    if saved_rows:
        document["saved"] = saved_rows
    document["urgent"] = _cap_urgent(document)
    if dropped:
        logger.warning(
            "work_briefing_items_dropped", count=len(dropped), reasons=dropped[:12],
        )
    document["markdown"] = render_markdown(document)
    return document


# ── 렌더 ─────────────────────────────────────────────────────────────────────

def _mark(urgency: str) -> str:
    return "🔴" if urgency == "high" else "🟡"


def render_markdown(
    document: dict[str, Any], name: str = "", fx: dict[str, Any] | None = None,
    business: dict[str, Any] | None = None,
) -> str:
    """잔디 본문 겸 복사용 텍스트. 잔디는 마크다운을 거의 안 그리므로 평문으로 쓴다."""

    day = document.get("for_date", "")
    who = f" — {name}" if name else ""
    lines = [f"☀️ 오늘의 출근 브리핑 ({day} {document.get('weekday','')}요일){who}", ""]

    meetings = document.get("meetings") or []
    lines.append(f"📅 오늘의 일정 · {len(meetings)}건")
    if not meetings:
        lines.append("  · 등록된 일정이 없습니다.")
    for row in meetings:
        head = f"  {_mark(row['urgency'])} {row['time']} | {row['title']}"
        if row.get("declined"):
            head += " (불참 회신함)"
        lines.append(head)
        detail = []
        if row.get("location"):
            detail.append(f"장소 {row['location']}")
        if row.get("attendee_count"):
            shown = ", ".join(row["attendees"][:6])
            more = row["attendee_count"] - len(row["attendees"][:6])
            detail.append(f"참석 {shown}" + (f" 외 {more}명" if more > 0 else ""))
        if detail:
            lines.append("      " + " · ".join(detail))
        if row.get("prep"):
            lines.append(f"      💡 {row['prep']}")
    lines.append("")

    window = document.get("window") or {}
    lines.append(
        f"✉️ 수신 메일 · {document.get('mail_total', 0)}건"
        f" (안 읽음 {document.get('mail_unread', 0)})"
    )
    if window.get("label"):
        lines.append(f"  · 기준: {window['label']}")
    if document.get("mail_summary"):
        lines.append(f"  {document['mail_summary']}")
    # ⛔ 채팅 본문에는 스크롤이 없다 — 화면보다 짧게 자르고, 자른 수를 적는다
    mail_rows = document.get("mail") or []
    for row in mail_rows[:MAX_MAIL_LINES_IN_TEXT]:
        lines.append(f"  {_mark(row['urgency'])} {row['from']} | {row['subject']}")
        for point in row.get("points", []):
            lines.append(f"      · {point}")
        if row.get("request"):
            lines.append(f"      → 요청: {row['request']}")
    if len(mail_rows) > MAX_MAIL_LINES_IN_TEXT:
        rest = len(mail_rows) - MAX_MAIL_LINES_IN_TEXT
        unread_rest = sum(1 for row in mail_rows[MAX_MAIL_LINES_IN_TEXT:]
                          if row.get("unread"))
        tail = f"  · 외 {rest}건"
        if unread_rest:
            tail += f" (안 읽음 {unread_rest})"
        lines.append(tail + " — 첫 화면 Today 에서 전부 볼 수 있습니다.")
    # ⚠️ 잘렸으면 잘렸다고 적는다 — 목록만 보면 그게 전부인 줄 안다
    if document.get("mail_omitted_unread"):
        lines.append(
            f"  · 안 읽은 메일 {document['mail_omitted_unread']}건은 상한을 넘어 "
            "실리지 않았습니다 (Gmail 에서 확인해 주세요)."
        )
    if not (document.get("mail") or document.get("mail_total")):
        lines.append("  · 새로 온 메일이 없습니다.")
    lines.append("")

    actions = document.get("actions") or []
    lines.append(f"✅ 우선순위 Action Item · {len(actions)}건")
    if not actions:
        lines.append("  · 즉시 조치할 항목을 찾지 못했습니다.")
    for row in actions:
        lines.append(f"  {_mark(row['urgency'])} {row['text']}")
    lines.append("")

    deadlines = document.get("deadlines") or []
    lines.append(f"⏰ 마감·기한 · {len(deadlines)}건")
    if not deadlines:
        lines.append("  · 기한이 확인된 항목이 없습니다.")
    for row in deadlines:
        lines.append(f"  {_mark(row['urgency'])} [{row['label']}] {row['text']}")

    saved = document.get("saved") or []
    if saved:
        lines += ["", "저장한 질문"]
        for row in saved:
            lines.append(f"  · {row.get('question', '')}")
            if row.get("answer"):
                lines.append(f"      {row['answer']}")
            if row.get("last_run_at"):
                lines.append(f"      실행 {row['last_run_at']}")
            if row.get("link"):
                lines.append(f"      [셀라에서 이어보기]({row['link']})")

    lines += _business_lines(business)
    lines += _fx_lines(fx)

    if document.get("dropped"):
        lines += ["", f"※ 근거가 확인되지 않아 제외한 문장 {document['dropped']}건"]
    return "\n".join(lines).strip()


def _business_lines(business: dict[str, Any] | None) -> list[str]:
    """지표 — 매출 한 줄 · 마케팅 한 줄. 화면과 **같은 소스**를 쓴다."""

    rows = (business or {}).get("items") or []
    if not rows:
        return []
    lines = ["", "📊 지표"]
    for row in rows:
        lines.append(f"  · {row.get('title', '')}".rstrip())
        # ⚠️ 본문 줄을 하나로 잇지 마라 — 각 줄이 이미 `· ` 로 시작해 `· ·` 가 되고,
        #    네 줄짜리 매출 설명이 한 줄에 뭉쳐 읽히지 않는다 (2026-08-26 실측).
        for part in str(row.get("body") or "").splitlines():
            text = part.strip().lstrip("·").strip()
            if text:
                lines.append("      " + text)
    return lines


def _fx_lines(fx: dict[str, Any] | None) -> list[str]:
    """환율 한 줄. ⚠️ 값이 없으면 **아무 줄도 넣지 않는다** — 빈 칸이 0원처럼 읽힌다."""

    from app.core import fx_rates

    items = (fx or {}).get("items") or []
    if not items:
        return []
    parts = []
    for item in items:
        change = fx_rates.change_label(item)
        parts.append(fx_rates.label(item) + (f" {change}" if change else ""))
    # ⚠️ 무엇과 견준 값인지 밝힌다 — 변동률만 있으면 전일대비로 읽힌다
    head = f"💱 환율 · {fx.get('for_date', '')} 기준"
    if fx.get("basis_note"):
        head += f" · {fx['basis_note']}"
    if int(fx.get("stale_days") or 0) > 0:
        # 주말·공휴일에는 새 값이 안 들어온다. 며칠 전 값인지 밝힌다.
        head += f" ({fx['stale_days']}일 전 고시)"
    return ["", head, "  " + "  ·  ".join(parts)]
