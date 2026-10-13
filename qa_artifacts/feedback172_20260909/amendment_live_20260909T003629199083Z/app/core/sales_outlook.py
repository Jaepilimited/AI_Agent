"""Deterministic month-close answers from dated, registered sales.

Future dated sales are valid records. They are kept in the monthly total and
reported separately; neither their sum nor the number of occupied dates is a
validated forecast of the eventual monthly close.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext


_KST = timezone(timedelta(hours=9))
_MONEY_FIELDS = (
    "revenue_through_as_of", "revenue_after_as_of", "revenue_in_period",
)
_CONTRACT = frozenset((
    "outlook_period_start", "outlook_period_end", "outlook_as_of",
    "outlook_row_count", *_MONEY_FIELDS,
))
_EXPLICIT_MONTH = re.compile(r"(?<!\d)(?:(\d{4})년)?(1[0-2]|0?[1-9])월")
_CURRENT_MONTH = re.compile(r"이번달|이달|이번월")
_COMPANY_SCOPE = re.compile(r"우리회사전체|회사전체|전체회사|전사|전체")
_KO_WORDS = (
    "예상실적", "예상매출", "매출예상", "실적예상", "매출전망", "실적전망",
    "매출예측", "실적예측", "마감예상", "마감전망", "마감예측", "예상마감",
    "매출액", "매출", "실적", "예상", "예측", "전망", "월말", "마감", "얼마",
    "알려주세요", "보여주세요", "예상해주세요", "예측해주세요", "전망해주세요",
    "예상해줘", "예측해줘", "전망해줘", "알려줘", "보여줘", "궁금해", "어때",
    "얼마인가요", "얼마일까요", "얼마일까", "얼마야", "어떻게",
)
_KO_TOKEN = re.compile(
    r"(?:마감(?:할|될)(?:거|것)?(?:같아요|같나요|같습니까|같아|까요|까)?"
    r"|(?:나올|될)(?:까요|까|거같아요|것같아요|거같아|것같아)"
    r"|" + "|".join(re.escape(word) for word in sorted(_KO_WORDS, key=len, reverse=True))
    + r")(?:으로는|으로|정도|쯤|은|는|이|가|을|를|의|도|로)?"
)
_EN_WORDS = frozenset((
    "what", "is", "are", "will", "be", "the", "our", "company", "companywide",
    "company-wide", "corporate", "full", "entire", "whole", "all", "total", "overall",
    "this", "month", "monthly", "month-end", "for", "at", "of", "sales", "revenue",
    "forecast", "forecasted", "estimate", "estimated", "expected", "expect", "outlook",
    "projection", "projected", "closing", "close", "end", "how", "much", "do", "we",
    "you", "think", "please", "tell", "me", "show", "give",
))
_EN_COMPANY = re.compile(
    r"\b(?:company[- ]?wide|(?:full|entire|whole) company|(?:our|the) company"
    r"|(?:total|overall|all) (?:sales|revenue))\b"
)

SQL_PROMPT_RULE = """## 월 마감 예상 매출의 조회 계약
매출/실적의 월 마감 예상·예측·전망 질문에만 아래 규칙을 적용한다.
현재 대화의 국가·팀·브랜드·제품·채널 등 조건, 접근 가능한 브랜드 제한,
사용자가 요구한 집계 구분을 모두 보존한다. 전사 질문이 아닌 것을 전사로 바꾸지 않는다.
매출은 SALES_ALL_Backup의 Sales1_R로 집계한다. WHERE에는 조회 월의 첫날부터
다음 달 첫날 미만까지 월 전체 범위를 넣고, 오늘 이후의 유효한 등록 행도 포함한다.
기준일은 별도 지정이 없으면 한국시간 오늘(CURRENT_DATE('Asia/Seoul'))이다.
기준일까지/이후의 구분은 WHERE의 오늘 상한이 아니라 SUM의 CASE 안에서 한다.
각 결과 행에는 다음 별칭과 의미를 모두 갖춘 열을 반드시 반환한다:
- outlook_period_start: 조회 월 첫날인 DATE
- outlook_period_end: 조회 월 마지막 날인 DATE
- outlook_as_of: 기준일인 DATE (오늘보다 미래인 날짜를 기준일로 삼지 않는다)
- outlook_row_count: 해당 구분의 원본 등록 행 수 COUNT(*)
- revenue_through_as_of: COALESCE(SUM(CASE WHEN DATE(Date) <= 기준일 THEN Sales1_R ELSE 0 END), 0)
- revenue_after_as_of: COALESCE(SUM(CASE WHEN DATE(Date) > 기준일 THEN Sales1_R ELSE 0 END), 0)
- revenue_in_period: COALESCE(SUM(Sales1_R), 0), 해당 월 전체 등록 합계
국가·팀·브랜드 등 집계 구분은 별도 열로 보존한다. 이름이 특수한 구분은 사람이
읽을 수 있는 outlook_label 열도 반환한다. 각 행의 두 분해 금액 합은 월 전체 합계와 같아야 한다.
COUNT(DISTINCT DATE(Date))는 기록이 있는 날짜 수일 뿐 경과 일수나 기준일이 아니다.
이 날짜 개수로 날짜를 만들거나, 월 전체 등록액을 일수로 나누어 확대하지 않는다.
estimated_revenue, run_rate, forecast 등의 추정 금액을 임의로 생성하지 않는다.
월 전체 등록 합계는 미등록 매출·취소·날짜 변경을 반영한 검증된 마감 예상치가 아니다.
"""


def _today(value: date | None) -> date:
    return value if value is not None else datetime.now(_KST).date()


def is_month_close_query(query: str) -> bool:
    """Recognize sales month-close intent without taking unrelated forecasts."""
    if not isinstance(query, str):
        return False
    lowered = query.casefold()
    compact = re.sub(r"\s+", "", lowered)
    if not re.search(r"매출|실적|\b(?:sales|revenue)\b", lowered):
        return False
    if re.search(
        r"수량|물량|주문|발주|재고|프로모션|캘린더|일정|행사|메일|이메일"
        r"|\b(?:quantity|units|orders?|inventory|promotion|calendar|e-?mail|gmail)\b",
        lowered,
    ):
        return False
    if "매출" not in compact and not re.search(r"\b(?:sales|revenue)\b", lowered):
        if re.search(r"광고|집행|전환|클릭|노출|마케팅", compact):
            return False
    explicit_month = bool(
        _CURRENT_MONTH.search(compact) or _EXPLICIT_MONTH.search(compact)
        or "월말" in compact
        or re.search(r"\b(?:this month|monthly|month[- ]end)\b", lowered)
    )
    if not explicit_month and re.search(
        r"올해|내년|작년|분기|연말|주간|이번주|다음주|\b(?:year|annual|quarter|week)\b",
        lowered,
    ):
        return False
    if not explicit_month and "마감" not in compact:
        return False
    return bool(
        re.search(r"예상|예측|전망|마감(?:할|될|하겠)", compact)
        or re.search(r"(?:월말|마감).*(?:될까|될거|될것|할까|할거|할것)", compact)
        or re.search(
            r"\b(?:forecast(?:ed)?|expected|expect|projected|projection|estimate[ds]?|outlook)\b"
            r"|\bwill\b.*\bclose\b",
            lowered,
        )
    )


def _company_month(query: str, current: date) -> tuple[date, date] | None:
    """Only consume known company-wide wording; preserve every other scope."""
    compact = re.sub(r"\s+", "", query.casefold()).strip("?!.,")
    explicit = list(_EXPLICIT_MONTH.finditer(compact))
    relative = list(_CURRENT_MONTH.finditer(compact))
    if explicit or relative:
        if len(explicit) + len(relative) != 1 or not _COMPANY_SCOPE.search(compact):
            return None
        if explicit:
            match = explicit[0]
            year, month = int(match.group(1) or current.year), int(match.group(2))
        else:
            match = relative[0]
            year, month = current.year, current.month
        rest = compact[:match.start()] + compact[match.end():]
        rest = _COMPANY_SCOPE.sub("", rest)
        # Particles attached to a removed period/scope must stay local. Never
        # strip arbitrary syllables from unknown country/product/team names.
        rest = re.sub(r"^(?:의|은|는)", "", rest)
        position = 0
        while position < len(rest):
            token = _KO_TOKEN.match(rest, position)
            if not token:
                return None
            position = token.end()
    elif "월말" in compact and _COMPANY_SCOPE.search(compact):
        # Add an explicit current-month token and use the same full consumption.
        return _company_month("이번달" + query, current)
    else:
        normalized = re.sub(r"\s+", " ", query.casefold()).strip(" ?!.,")
        words = normalized.split()
        if not _EN_COMPANY.search(normalized) or not words:
            return None
        if not set(words) <= _EN_WORDS or not re.search(
            r"\b(?:this month|monthly|month-end)\b", normalized,
        ):
            return None
        year, month = current.year, current.month
    try:
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        end + timedelta(days=1)
    except (ValueError, OverflowError):
        return None
    return start, end


def _brand_condition(brand_filter: str | None) -> str:
    if brand_filter is None or brand_filter == "":
        return ""
    if not isinstance(brand_filter, str):
        raise ValueError("Invalid brand restriction")
    codes = [code.strip() for code in brand_filter.split(",")]
    if not codes or any(not re.fullmatch(r"[A-Za-z0-9]+", code) for code in codes):
        raise ValueError("Invalid brand restriction")
    values = ", ".join("'" + code + "'" for code in dict.fromkeys(codes))
    return f"\n  AND Brand IN ({values})"


def build_company_sql(
    query: str, brand_filter: str | None = None, today: date | None = None,
) -> str | None:
    """Build the one known broad-company query; unsupported scopes return None.

    Malformed access restrictions raise rather than becoming unrestricted SQL.
    """
    if not is_month_close_query(query):
        return None
    current = _today(today)
    period = _company_month(query, current)
    if period is None:
        return None
    brand_condition = _brand_condition(brand_filter)
    from app.config import get_settings

    table = get_settings().sales_table_full_path
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+", table):
        raise ValueError("Invalid sales table configuration")
    start, end = period
    next_start = end + timedelta(days=1)
    return f"""SELECT
  DATE '{start.isoformat()}' AS outlook_period_start,
  DATE '{end.isoformat()}' AS outlook_period_end,
  DATE '{current.isoformat()}' AS outlook_as_of,
  COUNT(*) AS outlook_row_count,
  COALESCE(SUM(CASE WHEN DATE(Date) <= DATE '{current.isoformat()}'
    THEN Sales1_R ELSE 0 END), 0) AS revenue_through_as_of,
  COALESCE(SUM(CASE WHEN DATE(Date) > DATE '{current.isoformat()}'
    THEN Sales1_R ELSE 0 END), 0) AS revenue_after_as_of,
  COALESCE(SUM(Sales1_R), 0) AS revenue_in_period
FROM `{table}`
WHERE Date >= DATETIME '{start.isoformat()} 00:00:00'
  AND Date < DATETIME '{next_start.isoformat()} 00:00:00'{brand_condition}
LIMIT 1000"""


_NO_SPLIT = (
    "기준일까지 누적매출·이후 날짜 등록분·월 전체 등록 합계가 같은 기준으로 확인되지 "
    "않아, 신뢰할 수 있는 마감 예상치를 제시할 수 없습니다. 조회 조건을 유지한 채 "
    "월 전체 등록분을 기준일 전후로 나누어 다시 확인해야 합니다."
)
_LIMITATION = (
    "**월 전체 등록 합계는 확정된 마감 예상치가 아닙니다.** 아직 등록되지 않은 매출, "
    "취소, 날짜 변경은 확인되지 않았습니다. 등록 금액을 날짜로 구분한 결과이며, "
    "이후 등록분의 실현 여부까지 검증한 수치는 아닙니다."
)
_LABEL_NAMES = {
    "outlook_label": "구분", "brand": "브랜드", "brand_name": "브랜드", "브랜드": "브랜드",
    "country": "국가", "country_name": "국가", "국가": "국가",
    "team_new": "팀", "team": "팀", "team_name": "팀", "팀": "팀",
    "channel": "채널", "mall_classification": "채널", "채널": "채널",
    "product": "제품", "product_name": "제품", "제품": "제품",
    "continent1": "대륙", "continent2": "권역", "대륙": "대륙", "권역": "권역",
    "buyer": "바이어", "company_name": "거래처", "바이어": "바이어",
}


def _date_value(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("Expected a calendar date")


def _number(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Expected a finite number")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Expected a finite number")
    return number


def _label(row: Mapping) -> str:
    labels = []
    for key, value in row.items():
        name = _LABEL_NAMES.get(str(key).casefold())
        if name:
            safe = "미지정" if value is None else str(value)
            safe = re.sub(r"\s+", " ", safe)[:160]
            for before, after in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                                  ("|", "&#124;"), ("[", "&#91;"), ("`", "&#96;")):
                safe = safe.replace(before, after)
            labels.append(f"{name}: {safe}")
    return " · ".join(labels)


@dataclass(frozen=True)
class _Observation:
    start: date
    end: date
    as_of: date
    count: int
    through: Decimal
    after: Decimal
    total: Decimal
    label: str


def _observation(row: Mapping, current: date) -> _Observation:
    if not isinstance(row, Mapping) or not _CONTRACT <= row.keys():
        raise ValueError("Incomplete month-close result")
    start, end, as_of = (
        _date_value(row[key])
        for key in ("outlook_period_start", "outlook_period_end", "outlook_as_of")
    )
    if start.day != 1 or end != date(start.year, start.month, calendar.monthrange(start.year, start.month)[1]):
        raise ValueError("Expected a complete calendar month")
    if as_of > current:
        raise ValueError("Future as-of date")
    count = _number(row["outlook_row_count"])
    if count < 0 or count != count.to_integral_value():
        raise ValueError("Invalid source row count")
    through, after, total = (_number(row[key]) for key in _MONEY_FIELDS)
    with localcontext() as ctx:
        ctx.prec = 50
        tolerance = max(Decimal(1), abs(total) * Decimal("1e-10"))
        if abs(through + after - total) > tolerance:
            raise ValueError("Sales split does not reconcile")
    if count == 0 and any((through, after, total)):
        raise ValueError("Nonzero money without registered rows")
    if as_of < start and through != 0:
        raise ValueError("Elapsed sales in a future month")
    if as_of >= end and after != 0:
        raise ValueError("Future sales in a completed month")
    return _Observation(start, end, as_of, int(count), through, after, total, _label(row))


def _amount(value: Decimal) -> str:
    with localcontext() as ctx:
        ctx.prec = max(50, len(value.as_tuple().digits) + abs(value.adjusted()) + 5)
        rounded = value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        won = f"{rounded:,.0f}원"
        if abs(value) < Decimal("100000000"):
            return won
        eok = (value / Decimal("100000000")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return f"약 {eok:,.1f}억 원 ({won})"


def render_answer(
    query: str, sql: str, rows: list, today: date | None = None,
) -> str | None:
    """Show only reconciled facts, suppressing old or unverified projections.

    A complete reserved result contract also identifies rewritten close queries.
    SQL is accepted for a uniform formatter interface; the caller presents SQL
    details separately, and raw query text is never inserted into this answer.
    """
    has_contract = bool(
        rows and isinstance(rows[0], Mapping) and _CONTRACT <= rows[0].keys()
    )
    if not is_month_close_query(query) and not has_contract:
        return None
    if not rows:
        return (
            "조회 조건에 맞는 등록 데이터가 없습니다. 기준일까지의 누적매출과 이후 날짜 "
            "등록분을 확인할 수 없어 마감 예상치를 제시할 근거가 없습니다."
        )
    try:
        observations = [_observation(row, _today(today)) for row in rows]
    except (ValueError, InvalidOperation, OverflowError, TypeError):
        return _NO_SPLIT
    periods = {(item.start, item.end, item.as_of) for item in observations}
    first = observations[0]
    same_period = len(periods) == 1
    if same_period:
        opening = (
            f"조회 기간은 **{first.start.isoformat()} ~ {first.end.isoformat()}**, "
            f"누적 구분 기준일은 **{first.as_of.isoformat()}**입니다."
        )
    else:
        opening = "조회 기간별 등록 금액을 각 기준일 전후로 구분한 결과입니다."
    if not any(item.count for item in observations):
        return opening + "\n\n조회 조건에 맞는 등록 데이터가 없습니다. 마감 예상치를 제시할 근거가 없습니다."
    lines = [opening]
    if same_period and first.as_of < first.start:
        lines.append("조회 월은 기준일 이후이므로 현재 등록된 금액은 모두 이후 날짜 등록분입니다.")
    elif same_period and first.as_of < first.end:
        lines.append(
            f"누적 구간은 {first.start.isoformat()} ~ {first.as_of.isoformat()}, "
            f"이후 등록 구간은 {(first.as_of + timedelta(days=1)).isoformat()} ~ {first.end.isoformat()}입니다."
        )
    if len(observations) == 1 and not first.label:
        table = [
            "| 구분 | 등록 금액 |", "|---|---:|",
            f"| 기준일까지 누적매출 | {_amount(first.through)} |",
            f"| 이후 날짜 등록분 | {_amount(first.after)} |",
            f"| 월 전체 등록 합계 | {_amount(first.total)} |",
        ]
    else:
        labels = [
            ("" if same_period else f"{item.start.isoformat()} ~ {item.end.isoformat()} / 기준일 {item.as_of.isoformat()} · ")
            + item.label for item in observations
        ]
        if any(not label for label in labels) or len(set(labels)) != len(labels):
            return _NO_SPLIT
        table = [
            "| 조회 구분 | 기준일까지 누적매출 | 이후 날짜 등록분 | 월 전체 등록 합계 |",
            "|---|---:|---:|---:|",
        ]
        for label, item in zip(labels, observations):
            values = " | ".join(_amount(value) for value in (item.through, item.after, item.total)) if item.count else "등록 데이터 없음 | — | —"
            table.append(f"| {label} | {values} |")
    lines.extend(("\n".join(table), "원 금액은 원 단위로 반올림했습니다.", _LIMITATION))
    return "\n\n".join(lines)
