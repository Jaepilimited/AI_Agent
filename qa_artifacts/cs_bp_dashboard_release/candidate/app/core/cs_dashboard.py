"""Read-only, deterministic answers from the CS dashboard's aggregate API.

The dashboard uses Google Sheets, including overseas CS. Its population is
different from the Shopify refund table. Unsupported questions return None so
the caller can use the separately labelled SQL path. Raw records never enter
a prompt, a cache, a log, or an answer.
"""
from __future__ import annotations

import asyncio
import calendar
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from app.core.cs_metrics import DASHBOARD_URL


_KST = timezone(timedelta(hours=9))
_ENDPOINTS = {
    "domestic": "/api/dashboard/summary",
    "overseas": "/api/overseas/dashboard/summary",
}
_SOURCES = {"domestic": "google_sheets", "overseas": "google_sheets_overseas"}
_REGION = re.compile(r"(?:@@)?(?P<region>국내|한국|해외|글로벌|domestic|overseas)[ _]*(?:(?P<channel>자사몰)\s*)?cs", re.I)
_ALIASES = {"국내cs": "domestic", "domesticcs": "domestic", "한국cs": "domestic",
            "해외cs": "overseas", "글로벌cs": "overseas", "overseascs": "overseas"}
_CHANNELS = ("스마트스토어", "네이버", "무신사", "지그재그", "자사몰", "에이블리",
             "올리브영", "w컨셉", "코스트코코리아", "화해")
_CLAIMS = {"반품": "return", "교환": "exchange", "재배송": "redelivery"}
_STATUSES = {"미완료": "in_progress", "진행중": "in_progress", "처리중": "in_progress", "완료": "completed",
             "예정": "scheduled", "철회": "cancelled", "미확인": "unknown"}
_VIEWS = {"daily": "일별 추이", "channel": "채널별 건수", "product": "상품별 건수",
          "reason": "사유별 건수", "status": "상태별 건수", "type": "유형별 건수",
          "overview": "요약"}
_VIEW_WORDS = {
    "daily": r"일별|일자별|날짜별|매일|추이",
    "channel": r"채널별|몰별|판매처별",
    "product": r"상품별|제품별",
    "reason": r"사유별|이유별|사유|이유",
    "status": r"상태별|처리상태별",
    "type": r"유형별|접수유형별",
}
_UNSUPPORTED = re.compile(
    r"shopify|쇼피파이|bigquery|빅쿼리|\bsql\b|국가|나라|고객|이메일|주문|고유|중복|"
    r"환율|원화|한화|krw|지급|정산|매출|광고|재고|원가|손익|마케팅|"
    r"완료일|완료기준|완료\s*기준|처리일|접수일별\s*완료|소요|리드타임|검품|수량|"
    r"매뉴얼|정책|규정|가이드|응대|방법|담당|조직|분기|주별|주간|월별|연도별|"
    r"비교|비중|비율|대비|증감|전월|작년|지난해|최근|이번주|지난주|오늘|어제|누적|이상|이하|초과|미만|제외",
    re.I,
)


@dataclass(frozen=True)
class _Query:
    source: str
    params: dict[str, str]
    view: str


def _today() -> date:
    return datetime.now(_KST).date()


def _source_key(value: str) -> str | None:
    return _ALIASES.get(re.sub(r"[\s_@]+", "", str(value)).casefold())


def _period(query: str) -> tuple[dict[str, str], str] | None:
    """Consume one supported period; leave any other constraint for rejection."""
    iso = list(re.finditer(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", query))
    if iso:
        if len(iso) != 2 or not re.fullmatch(r"\s*(?:~|～|–|—|부터|to|-)\s*", query[iso[0].end():iso[1].start()]):
            return None
        try:
            start, end = (date.fromisoformat(m.group()) for m in iso)
        except ValueError:
            return None
        rest = query[:iso[0].start()] + " " + query[iso[1].end():]
        rest = re.sub(r"^\s*까지", "", rest) if not query[:iso[0].start()].strip() else rest.replace("까지", "")
    else:
        months = list(re.finditer(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월", query))
        relative = list(re.finditer(r"이번\s*달|지난\s*달", query))
        if len(months) + len(relative) > 1:
            return None
        if months:
            found = months[0]
            year, month = int(found.group(1) or _today().year), int(found.group(2))
            try:
                start = date(year, month, 1)
                end = date(year, month, calendar.monthrange(year, month)[1])
            except ValueError:
                return None
        elif relative:
            found = relative[0]
            start = _today().replace(day=1)
            if "지난" in found.group():
                start = (start - timedelta(days=1)).replace(day=1)
            end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        else:
            return {}, query
        rest = query[:found.start()] + " " + query[found.end():]
    if start > end:
        return None
    return {"startDate": start.isoformat(), "endDate": end.isoformat()}, rest


def _previous_query(context: str) -> _Query | None:
    """Inherit only the latest assistant's own dashboard answer and fixed link."""
    context = context or ""
    markers = list(re.finditer(r"(?m)^(AI|사용자):[ \t]*", context))
    if markers:
        if markers[-1].group(1) != "AI":
            return None
        context = context[markers[-1].end():]
    context = re.split(r"(?m)^\[(?:이전 조회 상태|직전 실행 SQL)", context, maxsplit=1)[0].strip()
    if not re.match(r"^\*\*(?:국내|해외) CS 대시보드(?: 기준)? · ", context):
        return None
    base = urlsplit(DASHBOARD_URL)
    matches = list(re.finditer(r"\[(CS 대시보드에서 [^\]\n]+ 보기)\]\((http://[^\s)]+)\)", context))
    for match in reversed(matches):
        try:
            url = urlsplit(match.group(2).replace("&amp;", "&"))
        except ValueError:
            continue
        if url.scheme != base.scheme or url.netloc != base.netloc or url.fragment:
            continue
        allowed_paths = {"/dashboard", "/reports/channel", "/reports/product", "/reports/reason",
                         "/data/naver-smartstore", "/data/channels", "/global/dashboard", "/global/data/store"}
        if url.path not in allowed_paths:
            continue
        parsed = parse_qs(url.query, keep_blank_values=True)
        allowed_keys = {"startDate", "endDate", "scope", "channel", "claimType", "status"}
        if set(parsed) - allowed_keys or any(len(v) != 1 for v in parsed.values()):
            return None
        params = {k: v[0] for k, v in parsed.items()}
        try:
            start, end = date.fromisoformat(params["startDate"]), date.fromisoformat(params["endDate"])
        except (KeyError, ValueError):
            return None
        if start > end or params.get("scope", "all") not in ("all", "smartstore", "others"):
            return None
        if params.get("channel", "all").casefold() not in ("all", *_CHANNELS):
            return None
        if params.get("claimType", "all") not in ("all", *_CLAIMS.values()):
            return None
        if params.get("status", "all") not in ("all", *_STATUSES.values()):
            return None
        source = "overseas" if url.path.startswith("/global/") else "domestic"
        view = next((k for k, label in _VIEWS.items() if label in match.group(1)), "overview")
        return _Query(source, params, view)
    return None


def _parse_query(query: str, enabled_sources=None, conversation_context: str = "") -> _Query | None:
    if not isinstance(query, str) or not query.strip() or len(query) > 700 or _UNSUPPORTED.search(query):
        return None
    q = query.casefold().strip()
    regions = {"domestic" if m.group("region") in ("국내", "한국", "domestic") else "overseas"
               for m in _REGION.finditer(q)}
    if len(regions) > 1:
        return None
    allowed = None if enabled_sources is None else {_source_key(s) for s in enabled_sources}
    selected = {s for s in (allowed or ()) if s}
    prior = _previous_query(conversation_context) if conversation_context else None
    region = next(iter(regions), None)
    source = region or (prior.source if prior else next(iter(selected)) if len(selected) == 1 else None)
    if source is None or (allowed is not None and source not in allowed):
        return None
    q = _REGION.sub(lambda match: " " + (match.group("channel") or "") + " ", q)
    # A requested link is presentation, never a reason to drop the data request.
    q = re.sub(r"(?:원본\s*)?링크(?:도)?(?:\s*(?:같이|함께|포함))?", " ", q)
    parsed_period = _period(q)
    if parsed_period is None:
        return None
    period, q = parsed_period
    # A new region+period is self-contained; otherwise a follow-up must retain
    # the exact prior dashboard filters. Unrecognised context cannot be guessed.
    inherit = bool(conversation_context and not (region and period))
    if inherit and (prior is None or prior.source != source):
        return None
    params = {"scope": "all", "channel": "all", "claimType": "all", "status": "all"}
    if inherit:
        params.update(prior.params)
    params.update(period)
    views = {view for view, pattern in _VIEW_WORDS.items() if re.search(pattern, q)}
    if len(views) > 1:
        return None
    view = next(iter(views), prior.view if inherit else "overview")
    if source == "overseas" and view == "product":
        return None
    if view != "overview" and re.search(r"금액|보상|환불액|총액|달러|usd", q):
        return None  # Grouped money needs its own contract, not a count chart.
    if source == "domestic" and re.search(r"환불액|환불\s*금액", q):
        return None  # Domestic dashboard compensation is not confirmed refunds.
    if source == "domestic" and re.search(r"usd|달러", q):
        return None  # The domestic metric is KRW; no FX conversion is provided.
    for pattern in _VIEW_WORDS.values():
        q = re.sub(pattern, " ", q)
    others = re.search(r"자사몰\s*[·ㆍ/와및+ ]+\s*입점몰", q)
    if others:
        if source != "domestic":
            return None
        params.update(scope="others", channel="all")
        q = q[:others.start()] + " " + q[others.end():]
    channels = [ch for ch in _CHANNELS if ch in q]
    if len(channels) > 1:
        return None
    if channels:
        channel = channels[0]
        if source == "overseas" and channel != "자사몰":
            return None
        channel = "스마트스토어" if channel == "네이버" else "W컨셉" if channel == "w컨셉" else channel
        params.update(channel=channel, scope="smartstore" if channel == "스마트스토어" else "all")
        q = q.replace(channels[0], " ")
    claims = [word for word in _CLAIMS if word in q]
    if len(claims) > 1:
        return None
    if claims:
        params["claimType"] = _CLAIMS[claims[0]]
        q = q.replace(claims[0], " ")
    statuses = list(dict.fromkeys(re.findall("|".join(sorted(_STATUSES, key=len, reverse=True)), re.sub(r"\s+", "", q))))
    if len(statuses) > 1:
        return None
    if statuses:
        word = statuses[0]
        params["status"] = _STATUSES[word]
        q = re.sub(r"\s*".join(map(re.escape, word)), " ", q)
    # A positive vocabulary, rather than a list of known countries/products,
    # makes unimplemented filters fall through instead of silently vanishing.
    words = ("대시보드", "데이터", "알려주세요", "알려줘", "보여주세요", "보여줘", "그려주세요", "그려줘",
             "정리해주세요", "정리해줘", "설명해줘", "시각화해줘", "시각화", "그래프", "차트", "도표",
             "처리기록", "처리 기록", "처리", "기록", "접수", "인입", "환불액", "환불금액", "환불",
             "보상금액", "보상액", "보상", "실보상금액", "실보상", "총액", "금액", "건수", "몇건", "몇 건",
             "총", "전체", "현황", "통계", "집계", "요약", "분석", "분포", "비중", "비율", "달러", "usd",
             "어떻게", "얼마", "몇", "있어", "있나요", "인가요", "인지", "현재", "알려", "정리", "확인", "보여",
             "해줘", "해주세요", "줘", "주세요", "그럼", "그러면", "이번에는", "이번엔", "각각", "및")
    for word in sorted(words, key=len, reverse=True):
        q = q.replace(word, " ")
    q = re.sub(r"[\s,.?!:;~·ㆍ/()+\-]+", "", q)
    if re.sub(r"으로|에서|대로|까지|부터|별로|별|로|의|은|는|이|가|을|를|와|과|도|만|수|건|좀|해|야", "", q):
        return None
    if not (views or any(w in query for w in ("현황", "건수", "몇 건", "몇건", "접수", "환불", "보상", "금액", "요약", "집계", "통계", "데이터", "분석")) or inherit):
        return None
    return _Query(source, params, view)


def _fetch_summary(source: str, params: dict[str, str]) -> dict:
    endpoint = _ENDPOINTS[source]
    with httpx.Client(timeout=8.0, follow_redirects=False) as client:
        response = client.get(DASHBOARD_URL.rstrip("/") + endpoint, params=params)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("source") != _SOURCES[source]:
        raise ValueError("unexpected_dashboard_source")
    return payload


def _number(value, *, count=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid_aggregate")
    if count and int(value) != value:
        raise ValueError("non_integer_count")
    return value


def _label(value, default="미기재") -> str:
    if value is None or value == "":
        return default
    if not isinstance(value, str) or len(value) > 180:
        raise ValueError("invalid_aggregate_label")
    # Even an unexpected email or phone in an aggregate label must not escape.
    if re.search(r"@|https?://|\d{3}[- .]?\d{3,4}[- .]?\d{4}|\d{10,}", value):
        raise ValueError("unsafe_aggregate_label")
    return re.sub(r"[\r\n\t]", " ", value).replace("|", "\\|").replace("`", "").replace("<", "&lt;").replace(">", "&gt;").replace("[", "\\[").replace("]", "\\]")


def _metric(payload: dict, key: str, unit: str, count=False):
    metric = payload["metrics"][key]
    if not isinstance(metric, dict) or metric.get("unit") != unit:
        raise ValueError("unexpected_metric_unit")
    return _number(metric["value"], count=count)


def _groups(payload: dict, view: str, total: int) -> list[tuple[str, int]]:
    if view == "daily":
        points = payload["charts"]["dailyTrend"]
        if not isinstance(points, list) or any(not isinstance(p, dict) for p in points):
            raise ValueError("invalid_daily_points")
        groups = [(date.fromisoformat(p["date"]).isoformat(), int(_number(p["total"], count=True))) for p in points]
        if sum(n for _, n in groups) != total or len({k for k, _ in groups}) != len(groups):
            raise ValueError("incomplete_daily_aggregates")
        return sorted(groups)
    if view == "type":
        points = payload["charts"]["typeShare"]
        if not isinstance(points, list) or any(not isinstance(p, dict) for p in points):
            raise ValueError("invalid_type_points")
        groups = [(_label(p["label"]), int(_number(p["value"], count=True))) for p in points]
        if sum(n for _, n in groups) != total:
            raise ValueError("incomplete_type_aggregates")
        return groups
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != total or _number(payload["total"], count=True) != total:
        raise ValueError("incomplete_dashboard_rows")
    column = {"channel": "salesChannel", "product": "productName", "reason": "reason", "status": "processStatus"}[view]
    counts = Counter()
    status_names = {"completed": "처리 완료", "in_progress": "진행 중", "scheduled": "예정",
                    "cancelled": "철회", "unknown": "미확인"}
    prefix = "스킨1004 마다가스카르 센텔라"
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_dashboard_row")
        label = _label(row.get(column))
        if view == "product" and label.startswith(prefix):
            tail = label[len(prefix):].strip()
            label = ("센텔라 " + tail if tail.startswith("앰플") else tail) if tail else label
        if view == "status":
            label = status_names.get(label, label)
        counts[label] += 1
    return sorted(counts.items(), key=lambda p: (-p[1], p[0]))


def _link(spec: _Query, params: dict[str, str]) -> str:
    if spec.source == "overseas":
        path = "/global/dashboard"
    elif params.get("scope") == "others":
        path = "/data/channels"
    elif spec.view in ("channel", "product", "reason"):
        path = "/reports/" + spec.view
    elif params.get("scope") == "smartstore":
        path = "/data/naver-smartstore"
    elif params.get("channel", "all") != "all":
        path = "/data/channels"
    else:
        path = "/dashboard"
    return DASHBOARD_URL.rstrip("/") + path + "?" + urlencode(params)


def _render(spec: _Query, payload: dict) -> str:
    if payload.get("source") != _SOURCES[spec.source]:
        raise ValueError("unexpected_dashboard_source")
    start = date.fromisoformat(payload["period"]["start"]).isoformat()
    end = date.fromisoformat(payload["period"]["end"]).isoformat()
    if start > end or any(spec.params.get(k, v) != v for k, v in (("startDate", start), ("endDate", end))):
        raise ValueError("unexpected_dashboard_period")
    params = {**spec.params, "startDate": start, "endDate": end}
    total = int(_metric(payload, "totalCount", "건", count=True))
    region = "국내" if spec.source == "domestic" else "해외"
    # Keep the filter-bearing link before long tables: conversation history
    # truncates assistant text, but this anchor must survive for follow-ups.
    pieces = [f"**{region} CS 대시보드 기준 · {start} ~ {end}**", f"대시보드 접수 기록은 **{total:,}건**입니다.",
              f"[CS 대시보드에서 {_VIEWS[spec.view]} 보기]({_link(spec, params)})"]
    filters = []
    if params.get("channel", "all") != "all":
        filters.append("채널 " + _label(params["channel"]))
    elif params.get("scope") == "others":
        filters.append("자사몰·입점몰")
    for key, names in (("claimType", _CLAIMS), ("status", _STATUSES)):
        if params.get(key, "all") != "all":
            filters.append(next(k for k, v in names.items() if v == params[key]))
    if filters:
        pieces.append("조회 조건: " + " · ".join(filters))
    if spec.view == "overview":
        unit = "원" if spec.source == "domestic" else "USD"
        amount = _metric(payload, "totalCompensationAmount", unit)
        done = int(_metric(payload, "completedCount", "건", count=True))
        progress = int(_metric(payload, "inProgressCount", "건", count=True))
        exchange = int(_metric(payload, "exchangeCount", "건", count=True))
        returns = int(_metric(payload, "returnCount", "건", count=True))
        amount_text = f"{amount:,.0f}원" if unit == "원" else f"${amount:,.2f} USD"
        pieces.append("| 대시보드 지표 | 값 |\n|---|---:|\n"
                      f"| 반품 | {returns:,}건 |\n| 교환 | {exchange:,}건 |\n"
                      f"| 처리 완료 | {done:,}건 |\n| 현재 미완료 | {progress:,}건 |\n"
                      f"| 보상 금액 | {amount_text} |")
        pieces.append("보상 금액은 대시보드 표시 금액이며 실제 지급 확인액을 뜻하지 않습니다.")
        pieces.append("현재 미완료는 대시보드의 현재 상태 지표로, 선택 기간의 완료 건수와 합산하지 않습니다.")
    else:
        groups = _groups(payload, spec.view, total)
        if spec.view == "daily" and any(not (start <= key <= end) for key, _ in groups):
            raise ValueError("daily_point_outside_period")
        if len(groups) > 400:
            raise ValueError("too_many_aggregate_groups")
        title = _VIEWS[spec.view]
        pieces.append(f"| {title.replace(' 건수', '')} | 건수 |\n|---|---:|\n" +
                      "\n".join(f"| {key} | {count:,} |" for key, count in groups))
        if groups:
            from app.core.chart import build_chartjs_config
            chart = build_chartjs_config({"chart_type": "line" if spec.view == "daily" else "bar",
                                          "x_column": "항목", "y_column": "건수", "title": region + " CS " + title,
                                          "x_label": title.replace(" 건수", ""), "y_label": "건"},
                                         [{"항목": key, "건수": count} for key, count in groups])
            if chart:
                pieces.append("```chart-config\n" + chart + "\n```")
        if spec.view == "product":
            pieces.append("대시보드의 대표 상품명별 접수 기록 수입니다. 상품 배열을 펼친 수량과는 기준이 다릅니다.")
    if spec.source == "overseas":
        pieces.append("해외 CS 구글시트 대시보드 기준이며, Shopify 환불 테이블과 집계 대상이 다릅니다.")
    return "\n\n".join(pieces)


def answer_query_sync(query: str, enabled_sources=None, conversation_context: str = "") -> str | None:
    """Answer supported dashboard queries, or link to them if the API fails.

    None means the request is unsupported, not that a different population can
    replace a failed dashboard read. The async interface has the same contract.
    """
    spec = _parse_query(query, enabled_sources, conversation_context)
    if spec is None:
        return None
    try:
        return _render(spec, _fetch_summary(spec.source, spec.params))
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
        # No payload or exception text is logged: API rows may contain PII.
        region = "국내" if spec.source == "domestic" else "해외"
        params = spec.params
        period = (f"{params['startDate']} ~ {params['endDate']}" if "startDate" in params
                  else "대시보드 기본 기간")
        return (f"**{region} CS 대시보드 · {period}**\n\n"
                "현재 셀라에서 대시보드 집계를 불러오지 못했습니다. 원본 화면에서 확인해 주세요.\n\n"
                f"[CS 대시보드에서 {_VIEWS[spec.view]} 보기]({_link(spec, params)})")


async def answer_query(query: str, enabled_sources=None, conversation_context: str = "") -> str | None:
    return await asyncio.to_thread(answer_query_sync, query, enabled_sources, conversation_context)
