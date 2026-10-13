"""Exact domestic CS order lookups with deterministic, readable detail tables.

No model, SQL cache, or date inference is involved. A supported lookup reads
all processing records for one external order number; unsupported constraints
return None so the existing SQL path can honour them.
"""
from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.core.bigquery import get_bigquery_client
from app.core.cs_metrics import DASHBOARD_URL, DOMESTIC_TABLE
from app.core.security import validate_sql


_ORDER = re.compile(r"(?<![A-Za-z0-9])([0-9]{10,32})(?![A-Za-z0-9])")
_ORDER_VALUE = re.compile(r"[0-9]{10,32}\Z")
_CS = re.compile(r"(?<![a-z])cs(?![a-z])", re.I)
_UNSUPPORTED = re.compile(
    r"해외|글로벌|overseas|shopify|쇼피파이|\b(?:sql|bigquery)\b|빅쿼리|"
    r"정책|매뉴얼|규정|작성|양식|응대|템플릿|방법|가이드|번역|메일|이메일|고객명|전화|주소|"
    r"합계|총계|총액|통계|집계|비교|분석|건수|비율|비중|차트|그래프|추이|"
    r"매출|광고|재고|원가|손익|지급|입금|정산|환율|usd|달러|"
    r"이상|이하|초과|미만|제외|대신|뿐|만\s*(?:알려|보여|조회)", re.I,
)
_CHANNELS = {
    "스마트스토어": "스마트스토어", "네이버": "스마트스토어", "자사몰": "자사몰",
    "무신사": "무신사", "지그재그": "지그재그", "올리브영": "올리브영",
    "에이블리": "에이블리", "w컨셉": "W컨셉", "코스트코코리아": "코스트코코리아", "화해": "화해",
}
_CLAIM_NAMES = {"return": "반품", "redelivery": "재배송", "exchange": "교환",
                "collection": "수거", "cancellation": "취소"}
_STATUS_NAMES = {"completed": "완료", "in_progress": "진행 중", "scheduled": "예정",
                 "cancelled": "철회", "unknown": "미확인"}
_DOMESTIC_ALIASES = {"국내cs", "domesticcs", "한국cs"}
_TITLE = re.compile(r"^\*\*국내 CS 상세 기록 · 주문번호 ([0-9]{10,32})\*\*")
_MAX_DISPLAY = 100


@dataclass(frozen=True)
class _Request:
    order_no: str
    channel: str | None = None
    compensation: bool = False


def _previous_request(context: str) -> _Request | None:
    """Only our latest detail answer can supply an omitted order number."""
    context = context or ""
    markers = list(re.finditer(r"(?m)^(AI|사용자):[ \t]*", context))
    if markers:
        if markers[-1].group(1) != "AI":
            return None
        context = context[markers[-1].end():]
    context = re.split(r"(?m)^\[(?:이전 조회 상태|직전 실행 SQL)", context, maxsplit=1)[0].strip()
    match = _TITLE.match(context)
    if not match:
        return None
    channel_match = re.search(r"(?m)^조회 조건: 채널 \*\*([^*\n]+)\*\*", context)
    channel = channel_match.group(1) if channel_match else None
    if channel is not None and channel not in _CHANNELS.values():
        return None
    return _Request(match.group(1), channel)


def _parse(query: str, conversation_context: str = "") -> _Request | None:
    if not isinstance(query, str) or not query.strip() or len(query) > 700 or _UNSUPPORTED.search(query):
        return None
    q = query.casefold().strip()
    ids = set(_ORDER.findall(q))
    if len(ids) > 1:
        return None
    prior = _previous_request(conversation_context)
    if ids:
        # Bare digits require CS intent; an unrelated order/sales question must
        # not silently become a domestic CS question because of older history.
        if not _CS.search(q):
            return None
        order_no = next(iter(ids))
        channel = None
        q = _ORDER.sub(" ", q)
    else:
        if prior is None:
            return None
        order_no, channel = prior.order_no, prior.channel
        if not re.search(r"접수|완료일|송장|상품|제품|수량|사유|상태|유형|보상|상세|정보|다시", q):
            return None
    channels = {_CHANNELS[word] for word in _CHANNELS if word in q}
    if len(channels) > 1:
        return None
    if channels:
        channel = next(iter(channels))
    for word in sorted(_CHANNELS, key=len, reverse=True):
        q = q.replace(word, " ")
    compensation = "보상" in q
    # Every remaining word must describe an output field or an ordinary lookup
    # request. Dates, claim filters, products, brands, and other conditions stay
    # unconsumed and fall through rather than being ignored.
    words = (
        "주문번호", "주문 번호", "주문정보", "주문 정보", "주문", "국내", "한국", "domestic",
        "처리 기록", "처리기록", "처리", "기록", "접수일", "접수 날짜", "접수날짜", "접수",
        "완료일", "완료 날짜", "완료날짜", "출고송장", "출고 송장", "회수송장", "회수 송장",
        "송장번호", "송장 번호", "송장", "번호", "상품명", "제품명", "상품", "제품", "수량",
        "상세사유", "상세 사유", "사유", "접수유형", "접수 유형", "유형", "처리상태", "처리 상태", "상태",
        "보상금액", "보상 금액", "보상액", "보상", "원본 기록액", "원본기록액",
        "상세정보", "상세 정보", "상세내역", "상세 내역", "상세", "정보", "내역", "데이터",
        "알려주세요", "알려줘", "알려", "보여주세요", "보여줘", "보여", "조회해줘", "조회",
        "확인해줘", "확인", "정리해줘", "정리", "설명해줘", "설명", "주세요", "해줘", "줘",
        "언제인지", "언제야", "언제", "뭔지", "뭐야", "무엇인지", "무엇", "어떤지", "어때",
        "다시", "그럼", "그러면", "그 주문", "해당", "직전", "아까", "같이", "함께", "전부", "모두",
        "링크", "대시보드", "좀", "등", "및",
    )
    q = _CS.sub(" ", q)
    # Consume the complete field phrase before shorter words like '송장 번호'.
    # Otherwise '출고송장 번호' loses its suffix first and leaves stray '출고'.
    q = re.sub(r"(?:출고|회수)\s*송장(?:\s*번호)?", " ", q)
    for word in sorted(words, key=len, reverse=True):
        q = q.replace(word, " ")
    q = re.sub(r"[\s,.?!:;()\[\]/·ㆍ]+", "", q)
    q = re.sub(r"으로|에서|인지|까지|별로|의|은|는|이|가|을|를|와|과|도|랑|로|요|야", "", q)
    if q:
        return None
    return _Request(order_no, channel, compensation)


def is_order_detail_query(query: str, conversation_context: str = "") -> bool:
    """Pure intent check for routing; no network, permissions, or model calls."""
    return _parse(query, conversation_context) is not None


def _source_allowed(enabled_sources) -> bool:
    if enabled_sources is None:
        return True
    return any(re.sub(r"[\s_@]+", "", str(source)).casefold() in _DOMESTIC_ALIASES
               for source in enabled_sources)


def _build_sql(request: _Request) -> str:
    if not _ORDER_VALUE.fullmatch(request.order_no):
        raise ValueError("invalid_order_number")
    if request.channel is not None and request.channel not in _CHANNELS.values():
        raise ValueError("invalid_channel")
    fields = ["order_no", "channel", "claim_type", "process_status", "received_at", "completed_at",
              "reason_normalized", "reasons", "reason_details", "outbound_invoice_nos", "return_invoice_nos",
              "ARRAY(SELECT AS STRUCT item.product_name, item.quantity FROM UNNEST(items) AS item) AS items"]
    if request.compensation:
        fields.append("compensation_amount")
    sql = "SELECT\n  " + ",\n  ".join(fields) + f"\nFROM `{DOMESTIC_TABLE}`\nWHERE order_no = '{request.order_no}'"
    if request.channel:
        sql += f"\n  AND channel = '{request.channel}'"
    return sql + "\nORDER BY received_at, completed_at, claim_type, channel\nLIMIT 101"


def _text(value) -> str:
    if value is None or value == "":
        return "미기재"
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise ValueError("unexpected_detail_value")
    escaped = html.escape(str(value), quote=True)
    escaped = re.sub(r"[\r\n\t]+", " ", escaped)
    return re.sub(r"([\\`*_|\[\]{}!])", r"\\\1", escaped)


def _date(value) -> str:
    if value is None or value == "":
        return "미기재"
    if isinstance(value, datetime):
        raise ValueError("unexpected_timestamp_for_domestic_date")
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(value).isoformat()


def _list_text(values) -> str:
    if values is None or values == []:
        return "미기재"
    if not isinstance(values, (list, tuple)):
        raise ValueError("unexpected_repeated_field")
    return " · ".join(_text(value) for value in values)


def _quantity(value) -> str:
    if value is None:
        return "미기재"
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("unexpected_quantity")
    return f"{value:,}개"


def _money(value) -> str:
    if value is None:
        return "미기재"
    if isinstance(value, bool):
        raise ValueError("unexpected_money")
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("non_finite_money")
    # Domestic schema defines these recorded amounts in KRW.
    return f"{amount:,.0f}원" if amount == amount.to_integral_value() else f"{amount:,f}원"


def _header(request: _Request) -> list[str]:
    parts = [f"**국내 CS 상세 기록 · 주문번호 {request.order_no}**"]
    if request.channel:
        parts.append(f"조회 조건: 채널 **{_text(request.channel)}**")
    return parts


def _render(request: _Request, rows: list) -> str:
    if not isinstance(rows, list):
        raise ValueError("unexpected_query_result")
    parts = _header(request)
    parts.append(f"출처: 국내 CS 상세 기록 · [CS 대시보드 열기]({DASHBOARD_URL})")
    if not rows:
        parts.append("이 주문번호와 조회 조건에 해당하는 국내 CS 처리 기록을 찾지 못했습니다.")
        return "\n\n".join(parts)
    truncated = len(rows) > _MAX_DISPLAY
    shown = rows[:_MAX_DISPLAY]
    parts.append("처리 기록이 100건을 넘어 **100건까지 표시했습니다. 추가 기록이 있습니다.**" if truncated
                 else f"이 주문에 연결된 처리 기록 **{len(rows)}건**입니다.")
    for index, row in enumerate(shown, 1):
        if not isinstance(row, dict) or row.get("order_no") != request.order_no:
            raise ValueError("unexpected_order_result")
        if request.channel and row.get("channel") != request.channel:
            raise ValueError("unexpected_channel_result")
        claim = _CLAIM_NAMES.get(row.get("claim_type"), "미기재" if not row.get("claim_type") else "미분류")
        status = _STATUS_NAMES.get(row.get("process_status"), "미기재" if not row.get("process_status") else "미분류")
        parts.append(f"**처리 기록 {index} · {claim}**")
        details = [("판매 채널", _text(row.get("channel"))), ("접수 유형", claim), ("처리 상태", status),
                   ("접수일", _date(row.get("received_at"))), ("완료일", _date(row.get("completed_at"))),
                   ("출고 송장번호", _list_text(row.get("outbound_invoice_nos"))),
                   ("회수 송장번호", _list_text(row.get("return_invoice_nos"))),
                   ("표준 사유", _text(row.get("reason_normalized"))),
                   ("원본 사유", _list_text(row.get("reasons"))),
                   ("상세 사유", _list_text(row.get("reason_details")))]
        if request.compensation:
            details.append(("원본 보상 기록액", _money(row.get("compensation_amount"))))
        parts.append("| 항목 | 내용 |\n|---|---|\n" + "\n".join(f"| {key} | {value} |" for key, value in details))
        items = row.get("items")
        if items is None:
            items = []
        if not isinstance(items, (list, tuple)):
            raise ValueError("unexpected_items")
        item_lines = []
        for item in items:
            # BigQuery nested STRUCT values can be Row objects rather than dicts.
            if not callable(getattr(item, "get", None)):
                raise ValueError("unexpected_item")
            item_lines.append(f"| {_text(item.get('product_name'))} | {_quantity(item.get('quantity'))} |")
        parts.append("| 접수 상품 | 수량 |\n|---|---:|\n" + ("\n".join(item_lines) if item_lines else "| 미기재 | 미기재 |"))
    parts.append("미기재는 원본 값이 비어 있다는 뜻이며, 0개 또는 해당 이력이 없다는 뜻으로 단정하지 않습니다.")
    if request.compensation:
        parts.append("보상액은 원본에 기록된 금액이며 실제 지급 확인액이 아닙니다. 미기재를 0원이나 미지급으로 해석하지 않습니다.")
    return "\n\n".join(parts)


def answer_query_sync(query: str, enabled_sources=None, conversation_context: str = "", brand_filter=None) -> str | None:
    """Read supported domestic details; None preserves unsupported/scoped queries.

    Account brand_filter constrains sales/product data, not this shared CS
    source: the CS table has no brand column or existing brand row filter.
    Source scope still controls access. A brand condition in the question is
    unsupported and remains unconsumed by the parser, so it cannot be ignored.
    """
    if not _source_allowed(enabled_sources):
        return None
    request = _parse(query, conversation_context)
    if request is None:
        return None
    try:
        sql = _build_sql(request)
        valid, _ = validate_sql(sql, allowed_tables=[DOMESTIC_TABLE])
        if not valid:
            raise ValueError("detail_query_validation_failed")
        rows = get_bigquery_client().execute_query(sql, timeout=30, max_rows=101)
        return _render(request, rows)
    except Exception:
        # Provider errors can contain query text and credentials. Return neither
        # them nor an invented empty result; a retry must remain an actual read.
        return "\n\n".join(_header(request) + [
            "지금 국내 CS 상세 기록을 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            f"[CS 대시보드 열기]({DASHBOARD_URL})",
        ])


async def answer_query(query: str, enabled_sources=None, conversation_context: str = "", brand_filter=None) -> str | None:
    return await asyncio.to_thread(answer_query_sync, query, enabled_sources, conversation_context, brand_filter)
