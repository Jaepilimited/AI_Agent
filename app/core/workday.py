"""근무일 판정 — 출근 브리핑이 어느 구간의 메일을 읽을지 결정한다.

⛔ **공휴일 표를 손으로 적지 않는다.** 사본은 반드시 낡고, 낡으면 에러가 아니라
   *조용히 틀린 조회 구간*이 된다 (프롬프트의 손수 DISTINCT 목록이 오답을 유도했던 것,
   메가와리 분기를 LLM 이 지어냈던 것과 같은 부류). 판정은 구글 '대한민국 공휴일'
   캘린더 실측으로 하고, 읽지 못하면 **주말만 보고 그 사실을 브리핑 본문에 공시한다.**

구간 규칙 (2026-08-25 사용자 확정):
  평일            → 어제(전 근무일) 18:00 ~ 지금
  월요일·연휴 다음날 → 마지막 근무일 18:00 ~ 지금 (주말·연휴 누적분 전체)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")

#: 퇴근 시각. "어제 퇴근 이후" 를 결정적으로 만들기 위한 단일 상수다.
WORK_END_HOUR = 18

#: 근무일을 거슬러 올라갈 최대 일수. 연말연시·긴 연휴를 덮되 무한 루프는 막는다.
MAX_LOOKBACK_DAYS = 14


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def is_workday(day: date, holidays: frozenset[date] | set[date] | None = None) -> bool:
    """주말도 공휴일도 아니면 근무일이다."""

    if is_weekend(day):
        return False
    return day not in (holidays or frozenset())


def previous_workday(day: date, holidays: frozenset[date] | set[date] | None = None) -> date:
    """``day`` 직전의 근무일. 하나도 못 찾으면 ``MAX_LOOKBACK_DAYS`` 만큼 뒤를 돌려준다."""

    cursor = day - timedelta(days=1)
    for _ in range(MAX_LOOKBACK_DAYS):
        if is_workday(cursor, holidays):
            return cursor
        cursor -= timedelta(days=1)
    return day - timedelta(days=1)


def mail_window(
    now: datetime,
    holidays: frozenset[date] | set[date] | None = None,
    holiday_source: str = "calendar",
) -> tuple[datetime, datetime, dict]:
    """(시작, 끝, 설명) — 마지막 근무일 18:00 부터 지금까지.

    ``holiday_source`` 가 ``calendar`` 가 아니면 공휴일을 확인하지 못한 것이므로
    설명(meta)에 남긴다. 화면과 잔디 본문이 그 사실을 그대로 적는다.
    """

    current = now.astimezone(SEOUL)
    today = current.date()
    previous = previous_workday(today, holidays)
    start = datetime.combine(previous, time(hour=WORK_END_HOUR), tzinfo=SEOUL)

    # 근무일 새벽(예: 화요일 07:00)에도 시작점은 어제 18:00 이라 항상 start < now 다.
    # 다만 연휴 첫날 18:00 이전처럼 경계가 뒤집히는 경우를 방어한다.
    if start >= current:
        start = datetime.combine(
            previous_workday(previous, holidays), time(hour=WORK_END_HOUR), tzinfo=SEOUL,
        )

    span_days = max(1, (today - previous).days)
    meta = {
        "from_date": previous.isoformat(),
        "from_weekday": "월화수목금토일"[previous.weekday()],
        "span_days": span_days,
        "multi_day": span_days > 1,
        "holiday_source": holiday_source,
        "holidays_known": holiday_source == "calendar",
        "skipped": sorted(
            d.isoformat()
            for d in (holidays or frozenset())
            if previous < d < today
        ),
    }
    meta["label"] = _window_label(previous, span_days, current, meta)
    return start, current, meta


def _window_label(previous: date, span_days: int, current: datetime, meta: dict) -> str:
    weekday = meta["from_weekday"]
    if span_days <= 1:
        text = f"어제({previous.month}/{previous.day} {weekday}) {WORK_END_HOUR}:00 이후 받은 메일"
    else:
        text = (
            f"{previous.month}/{previous.day}({weekday}) {WORK_END_HOUR}:00 이후 받은 메일 "
            f"· {span_days}일치 누적"
        )
    if not meta["holidays_known"]:
        text += " · 공휴일 확인 실패(주말만 반영)"
    return text
