"""Calendar meeting counts from complete, per-user event history (#167).

Dates, exclusions and totals are applied before rendering. Calendar records and
attendee addresses are used only in memory; no history cache is created.
"""
from __future__ import annotations

import calendar
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

logger = structlog.get_logger(__name__)
SEOUL = ZoneInfo("Asia/Seoul")
_PERIOD_HELP = "집계할 시작일과 종료일을 알려주세요. 예: 2026-01-01~2026-08-31 미팅 횟수"
_MEETING = re.compile(r"미팅|회의|논의|협의|면담|인터뷰|\bmeeting\b|\bsync\b|\b1[:/]1\b", re.I)


@dataclass(frozen=True)
class CalendarStatsRequest:
    start: datetime
    end: datetime
    excluded_titles: tuple[str, ...] = ()
    wants_teams: bool = False
    past_only: bool = True
    completed_before: datetime | None = None


def is_statistics_query(question: str) -> bool:
    return bool(re.search(r"캘린더|일정|미팅|회의|calendar|meeting", question, re.I)
                and re.search(r"몇\s*(?:번|건|회|개)|횟수|건수|통계|돌아보|회고|협업|how many", question, re.I))


def _month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=SEOUL)


def _next_month(value: datetime) -> datetime:
    return _month_start(value.year + (value.month == 12), value.month % 12 + 1)


def _exclusions(question: str) -> tuple[str, tuple[str, ...]]:
    marker = re.search(r"제외|빼(?:고|줘|주세요)|빼\s*줘", question)
    if not marker:
        return question, ()
    prefix = question[:marker.start()]
    # A separate clause such as '(단, A, B는 제외)' names titles, not API q= text.
    boundary = list(re.finditer(r"[.(\n;?]|(?:단|다만)\s*[,，:]|(?:미팅|회의|일정)\s*중\s*", prefix))
    offset = boundary[-1].end() if boundary else 0
    titles = prefix[offset:].strip()
    if not titles or re.search(r"올해|이번년|몇\s*번|횟수|확인해|캘린더", titles):
        raise ValueError("제외할 일정 제목을 알려주세요. 예: 올해 미팅 횟수 (단, 데일리 미팅은 제외)")
    parts = re.split(r"\s*[,，·;]\s*|\s+(?:및|그리고)\s+|(?<=미팅)과\s*|(?<=회의)와\s*", titles)
    cleaned = tuple(dict.fromkeys(
        re.sub(r"(?:은|는|을|를|만)$", "", part.strip().strip("\"'‘’“” ")).strip()
        for part in parts if part.strip()
    ))
    if not cleaned or any(not item for item in cleaned):
        raise ValueError("제외할 일정 제목을 확인해 주세요.")
    suffix = re.sub(r"^(?:해\s*주세요|해주세요|해줘|하고|해요|해|요)?\s*[).]*", " ", question[marker.end():])
    return question[:offset] + suffix, cleaned


def _validate_scope(text: str) -> None:
    """Only total/department breakdowns are supported; never discard a title/person filter."""
    rest = re.sub(r"\s+", "", text).lower()
    rest = re.sub(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{4}년|\d{1,3}(?:개월|월|일|달|주|분기)|[1-4]q|q[1-4]", "", rest)
    words = (
        "구글", "google", "캘린더", "calendar", "이번년에", "이번년", "이번해", "올해", "금년",
        "작년", "지난해", "thisyear", "lastyear", "상반기", "하반기", "이번달", "지난달", "다음달",
        "thismonth", "lastmonth", "이번주", "지난주", "다음주", "오늘", "어제", "내일", "모레",
        "최근", "지난", "과거", "부터", "까지", "동안", "이내", "현재", "지금", "앞으로", "예정된", "예정",
        "확인해서", "확인해줘", "확인해주세요", "알려주세요", "알려줘", "보여줘", "정리해줘", "정리",
        "세어줘", "세줘", "집계해줘", "집계", "알수있을까", "확인", "해줘", "해주세요",
        "있었는지", "있었어", "했는지", "했어", "했나", "했나요", "있나요", "있어", "남았어", "남았는지",
        "내가", "나의", "저의", "개인", "내", "제", "전체", "총", "모든", "모두", "전부",
        "미팅", "회의", "일정", "몇번", "몇건", "몇회", "몇개", "횟수", "건수", "통계", "돌아보기", "회고",
        "어느", "어떤", "팀별로", "부서별로", "팀별", "부서별", "팀", "부서", "협업", "긴밀하게", "가장", "많이",
        "및", "그리고", "단", "다만",
    )
    for word in sorted(words, key=len, reverse=True):
        rest = rest.replace(word, "")
    if not re.fullmatch(r"(?:은|는|이|가|을|를|의|에|와|과|로|도|요|[\W_])*", rest):
        raise ValueError("추가 조건을 정확히 해석하지 못했습니다. 전체 미팅 횟수라면 "
                         "'올해 전체 미팅 횟수', 제외할 제목이 있다면 '(단, 데일리 미팅은 제외)'처럼 알려주세요.")


def parse_request(question: str, now: datetime | None = None) -> CalendarStatsRequest:
    current = now or datetime.now(SEOUL)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SEOUL)
    current = current.astimezone(SEOUL)
    text, excluded = _exclusions(question)
    _validate_scope(text)
    q = re.sub(r"\s+", "", text).lower()
    for pattern in (r"상반기|하반기", r"지난달|이번달|다음달|lastmonth|thismonth",
                    r"지난주|이번주|다음주", r"어제|내일|모레"):
        if len(re.findall(pattern, q)) > 1:
            raise ValueError(_PERIOD_HELP)
    current_year_words = r"올해|이번년|이번해|금년|thisyear"
    last_year_words = r"작년|지난해|lastyear"
    if re.search(current_year_words, q) and re.search(last_year_words, q):
        raise ValueError(_PERIOD_HELP)
    if re.search(current_year_words, q):
        q = q.replace("오늘까지", "")
    past_only = not any(word in q for word in ("예정", "앞으로", "다음", "내일", "모레"))
    # Partial periods must not accidentally become a whole year/month.
    if re.search(r"첫(?:째|\d|한|두)|둘째|셋째|넷째|다섯째|마지막|격주|매주|오전|오후", q):
        raise ValueError(_PERIOD_HELP)
    year_match = re.search(r"(?<!\d)(\d{4})년", q)
    year = int(year_match[1]) if year_match else current.year
    if any(word in q for word in ("작년", "지난해", "lastyear")):
        year = current.year - 1
    if year != current.year and re.search(r"지난달|이번달|다음달|지난주|이번주|다음주|오늘|어제|내일", q):
        raise ValueError(_PERIOD_HELP)
    start = end = None
    iso = re.findall(r"(?<!\d)(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?!\d)", q)
    korean_days = re.findall(r"(?:(\d{4})년)?(\d{1,2})월(\d{1,2})일", q)
    dates = iso or korean_days
    if not dates and len(set(re.findall(r"\d{4}년", q))) > 1:
        raise ValueError(_PERIOD_HELP)
    if len(re.findall(r"[1-4]분기|[1-4]q|q[1-4]", q)) > 1:
        raise ValueError(_PERIOD_HELP)
    if len(dates) == 2 and not re.search(r"부터|[~～–]", q):
        raise ValueError(_PERIOD_HELP)
    relative = re.search(r"(?:최근|지난|과거)(\d{1,3})(개월|달|주|일)", q)
    months = re.findall(r"(?<!\d)(\d{1,2})월", q)
    if dates:
        if len(dates) > 2:
            raise ValueError(_PERIOD_HELP)
        parsed = [datetime(int(y or year), int(m), int(d), tzinfo=SEOUL) for y, m, d in dates]
        start, end = parsed[0], parsed[-1] + timedelta(days=1)
        if len(parsed) == 1 and any(word in q for word in ("부터", "까지", "~")):
            raise ValueError(_PERIOD_HELP)
    elif relative:
        amount, unit = int(relative[1]), relative[2]
        if not amount:
            raise ValueError(_PERIOD_HELP)
        if unit in ("개월", "달"):
            absolute = current.year * 12 + current.month - 1 - amount
            y, m = divmod(absolute, 12)
            start = datetime(y, m + 1, min(current.day, calendar.monthrange(y, m + 1)[1]), tzinfo=SEOUL)
        else:
            start = (current - timedelta(days=amount * (7 if unit == "주" else 1))).replace(
                hour=0, minute=0, second=0, microsecond=0)
        end = current
    elif months:
        if len(months) > 2:
            raise ValueError(_PERIOD_HELP)
        if len(months) == 2 and not re.search(r"부터|[~～–]", q):
            raise ValueError(_PERIOD_HELP)
        start = _month_start(year, int(months[0]))
        end = _next_month(_month_start(year, int(months[-1])))
        if len(months) == 1 and any(word in q for word in ("부터", "까지", "~")):
            raise ValueError(_PERIOD_HELP)
    elif re.search(r"[1-4]분기|[1-4]q|q[1-4]", q):
        quarter = re.search(r"([1-4])분기|([1-4])q|q([1-4])", q)
        number = int(next(value for value in quarter.groups() if value))
        start = _month_start(year, number * 3 - 2)
        end = _month_start(year + (number == 4), number * 3 % 12 + 1)
    elif "상반기" in q or "하반기" in q:
        month = 1 if "상반기" in q else 7
        start = _month_start(year, month)
        end = _month_start(year + (month == 7), 7 if month == 1 else 1)
    elif any(word in q for word in ("지난달", "이번달", "다음달", "lastmonth", "thismonth")):
        start = _month_start(current.year, current.month)
        if "지난달" in q or "lastmonth" in q:
            start = (start - timedelta(days=1)).replace(day=1)
        elif "다음달" in q:
            start = _next_month(start)
        end = _next_month(start)
    elif any(word in q for word in ("지난주", "이번주", "다음주")):
        start = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        start += timedelta(days=-7 if "지난주" in q else 7 if "다음주" in q else 0)
        end = start + timedelta(days=7)
    elif any(word in q for word in ("오늘", "어제", "내일", "모레")):
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        start += timedelta(days=-1 if "어제" in q else 2 if "모레" in q else 1 if "내일" in q else 0)
        end = start + timedelta(days=1)
    elif year_match or any(word in q for word in ("올해", "이번년", "이번해", "금년", "작년", "지난해", "thisyear", "lastyear")):
        start, end = _month_start(year, 1), _month_start(year + 1, 1)
    if start is None or end is None or end <= start:
        raise ValueError(_PERIOD_HELP)
    if past_only:
        end = min(end, current)
    else:
        start = max(start, current)
    if end <= start:
        raise ValueError("아직 지나지 않은 기간입니다. 예정된 일정을 세려면 '예정 미팅 횟수'라고 요청해 주세요.")
    if end - start > timedelta(days=732):
        raise ValueError("한 번에 2년 이내의 기간으로 나누어 조회해 주세요.")
    return CalendarStatsRequest(start, end, excluded, bool(re.search(r"팀|부서|협업", text)), past_only, current)


def _normalized_title(title: str) -> str:
    return re.sub(r"[\W_]+", "", title.casefold())


def _people(event: dict) -> set[str]:
    return {str(person.get("email", "")).strip().casefold()
            for person in event.get("attendees", []) or []
            if person.get("email") and not person.get("self") and not person.get("resource")
            and person.get("responseStatus") != "declined"}


def summarize(events: list[dict], request: CalendarStatsRequest, teams: dict[str, str] | None = None) -> dict:
    total = excluded_count = matched = omitted = no_people = 0
    months, team_counts = Counter(), Counter()
    seen, unknown = set(), set()
    excluded = [_normalized_title(title) for title in request.excluded_titles]
    for event in events:
        event_id = event.get("id")
        if event_id and event_id in seen:
            continue
        if event_id:
            seen.add(event_id)
        if event.get("status") == "cancelled" or event.get("eventType", "default") != "default":
            continue
        if any(person.get("self") and person.get("responseStatus") == "declined"
               for person in event.get("attendees", []) or []):
            continue
        try:
            start = datetime.fromisoformat(event["start"]["dateTime"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(event["end"]["dateTime"].replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        if not request.start <= start < request.end or (
                request.past_only and end > (request.completed_before or request.end)):
            continue
        title = event.get("summary", "") or ""
        people = _people(event)
        if not people and not _MEETING.search(title):
            continue
        if any(term in _normalized_title(title) for term in excluded):
            excluded_count += 1
            continue
        total += 1
        months[start.astimezone(SEOUL).strftime("%Y-%m")] += 1
        known = {teams[email] for email in people if teams and teams.get(email)}
        if known:
            matched += 1
            team_counts.update(known)
        unknown.update(email for email in people if not teams or not teams.get(email))
        omitted += bool(event.get("attendeesOmitted"))
        no_people += not bool(people)
    return {"total": total, "months": dict(sorted(months.items())), "teams": dict(team_counts),
            "excluded_by_title": excluded_count, "team_matched_meetings": matched,
            "unmatched_people": len(unknown), "attendees_omitted": omitted, "no_people": no_people}


def _lookup_teams(emails: set[str]) -> dict[str, str]:
    """Match only attendee addresses; ambiguous/missing directory entries stay unknown."""
    from app.db.mariadb import fetch_all
    available = fetch_all(
        "SELECT TABLE_NAME AS name FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME IN ('directory_users','ad_users')") or []
    names = {row["name"] for row in available}
    directory = "directory_users" if "directory_users" in names else "ad_users"
    if directory not in names:
        raise RuntimeError("Employee directory is unavailable")
    matches = defaultdict(set)
    addresses = sorted(emails)
    for offset in range(0, len(addresses), 500):
        batch = tuple(addresses[offset:offset + 500])
        placeholders = ",".join(["%s"] * len(batch))
        rows = fetch_all(
            f"SELECT email, department FROM {directory} WHERE is_active=1 AND LOWER(email) IN ({placeholders}) "
            f"UNION ALL SELECT u.email, a.department FROM users u JOIN {directory} a ON a.id=u.ad_user_id "
            f"WHERE a.is_active=1 AND LOWER(u.email) IN ({placeholders})", batch + batch) or []
        for row in rows:
            if row.get("department") and row.get("email"):
                path = re.split(r"\s*(?:>|/|\\|→)\s*", row["department"].strip())
                team = next((part.strip() for part in reversed(path) if part.strip().endswith("팀")), "")
                # A part belongs to its nearest team; a division alone cannot
                # identify a team and must remain in the unknown coverage.
                if team:
                    matches[row["email"].strip().casefold()].add(team)
    return {email: next(iter(departments)) for email, departments in matches.items() if len(departments) == 1}


def _cell(value: str) -> str:
    return re.sub(r"[\r\n|`<>]", " ", value)


def render(result: dict, request: CalendarStatsRequest) -> str:
    end_label = ((request.end - timedelta(days=1)).strftime("%Y-%m-%d")
                 if request.end.time() == datetime.min.time() else request.end.strftime("%Y-%m-%d %H:%M"))
    lines = [f"{request.start:%Y-%m-%d} ~ {end_label} (한국 시간), "
             f"내 기본 캘린더의 미팅 일정은 **총 {result['total']:,}건**입니다.", ""]
    if request.excluded_titles:
        lines += [f"제외한 제목: {' · '.join(_cell(value) for value in request.excluded_titles)} "
                  f"({result['excluded_by_title']:,}건 제외)", ""]
    lines += ["| 월 | 미팅 일정 |", "|---|---:|"]
    month = request.start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while month < request.end:
        key = month.strftime("%Y-%m")
        lines.append(f"| {key} | {result['months'].get(key, 0):,}건 |")
        month = _next_month(month)
    lines += ["", "집계 기준: 시간이 지정되어 있고 다른 참석자가 있거나 제목에 미팅·회의·논의 등이 "
              "있는 일정입니다. 종일 일정·취소·본인이 거절한 일정은 제외하고, 반복 일정은 각 회차를 셉니다."]
    if request.past_only:
        lines.append("조회 기간에 시작해 조회 시각까지 종료된 일정만 셌습니다. 진행 중이거나 미래인 일정은 포함하지 않습니다.")
    lines.append("캘린더 등록 기준이며 실제 참석 여부를 확인한 수치는 아닙니다.")
    if request.wants_teams:
        lines += ["", "참석자의 현재 소속별 공동 일정입니다. 협업 성과나 당시 소속을 뜻하지 않습니다.",
                  "", "| 참석자 소속 | 공동 일정 |", "|---|---:|"]
        for team, count in sorted(result["teams"].items(), key=lambda pair: (-pair[1], pair[0])):
            lines.append(f"| {_cell(team)} | {count:,}건 |")
        if not result["teams"]:
            lines.append("| 확인된 소속 없음 | 집계 불가 |")
        lines += ["", f"전체 {result['total']:,}건 중 참석자 소속이 확인된 일정은 "
                  f"{result['team_matched_meetings']:,}건입니다. 소속 미확인 참석자 {result['unmatched_people']:,}명, "
                  f"참석자 정보가 없는 일정 {result['no_people']:,}건, 참석자 목록이 일부인 일정 "
                  f"{result['attendees_omitted']:,}건은 소속 분석이 제한됩니다.",
                  "같은 소속 사람이 여러 명이어도 한 일정은 1건이며, 여러 소속이 함께한 일정은 각 소속에 포함됩니다."]
    return "\n".join(lines)


def answer_statistics(creds, question: str, now: datetime | None = None) -> str:
    from app.core.google_workspace import list_calendar_history
    try:
        request = parse_request(question, now=now)
    except ValueError as exc:
        message = str(exc)
        return message if re.search(r"[가-힣]", message) else _PERIOD_HELP
    try:
        history = list_calendar_history(creds, request.start, request.end)
    except Exception as exc:
        logger.warning("calendar_history_failed", error_type=type(exc).__name__)
        return "캘린더 전체 조회를 완료하지 못해 횟수를 집계하지 못했습니다. 잠시 후 다시 요청해 주세요."
    if history.get("truncated"):
        return "캘린더 전체를 가져오지 못해 총횟수를 집계하지 않았습니다. 기간을 나누어 다시 요청해 주세요."
    teams = {}
    directory_failed = False
    if request.wants_teams:
        try:
            teams = _lookup_teams(set().union(*(_people(event) for event in history["items"])))
        except Exception as exc:
            directory_failed = True
            logger.warning("calendar_team_lookup_failed", error_type=type(exc).__name__)
    answer = render(summarize(history["items"], request, teams), request)
    if directory_failed:
        answer += "\n\n현재 소속 조회에 실패해 소속별 분석을 완료하지 못했습니다. 미팅 총횟수에는 영향이 없습니다."
    return answer
