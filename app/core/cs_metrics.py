"""CS 운영 집계 계약. 원본은 DB 정보 시트의 국내 CS / 해외 CS 탭이다.

두 테이블은 기존 ETL이 갱신한다. 제품 상담·CS 문서는 BP에서 함께 검색한다.
"""
from __future__ import annotations

import re

DOMESTIC_TABLE = "skin1004-319714.cs_dashboard.domestic_cs_records"
OVERSEAS_TABLE = "skin1004-319714.cs_dashboard.overseas_cs_refunds"
DASHBOARD_URL = "http://34.64.99.254:8061/"
SCHEMA_SHEET_URL = "https://docs.google.com/spreadsheets/d/1iwFzA_ksXwC2W_AkrW-aVBjcBFkHt_96usQHoMy6wh0/edit"

DOMESTIC_KEYWORDS = ["국내cs", "국내 cs", "한국 cs", "domestic cs", "domestic_cs_records",
                     ("국내", "cs")]
OVERSEAS_KEYWORDS = ["해외cs", "해외 cs", "글로벌cs", "글로벌 cs", "overseas cs",
                     "overseas_cs_refunds", ("해외", "cs"), ("글로벌", "cs"),
                     ("shopify", "환불"), ("쇼피파이", "환불")]


def is_cs_metrics_query(query: str) -> bool:
    """지역이 명시된 CS 실적 질문만 잡고 상담·매뉴얼·조직 질문은 보존한다."""
    q = (query or "").casefold()
    domain = bool(re.search(r"(?:국내|한국|해외|글로벌|domestic|overseas)\s*(?:자사몰\s*)?cs\b", q))
    # 한국어 조사 바로 앞의 CS에도 경계를 허용한다.
    domain = domain or bool(re.search(r"(?:국내|한국|해외|글로벌)\s*(?:자사몰\s*)?cs(?=[가-힣])", q))
    domain = domain or bool(re.search(r"(?:국내|해외).{0,8}(?:국내|해외)\s*cs", q))
    domain = domain or (any(w in q for w in ("shopify", "쇼피파이")) and "환불" in q)
    if not domain:
        return False
    if any(w in q for w in ("매뉴얼", "정책", "규정", "가이드", "응대", "답변 예시", "담당자", "담당 팀", "조직도")):
        return False
    return any(w in q for w in (
        "건수", "몇 건", "몇건", "집계", "통계", "현황", "추이", "사유", "유형", "상태",
        "접수", "인입", "처리", "완료", "환불", "보상", "주문", "금액", "비중", "비율", "분포",
        "월별", "국가별", "채널별", "기간별", "비교", "데이터", "실적", "분석", "count", "refund",
    ))


def tables_in_sql(sql: str) -> set[str]:
    code = _code_mask(sql or "")
    return {table for table in (DOMESTIC_TABLE, OVERSEAS_TABLE)
            if re.search(r"\b(?:FROM|JOIN)\s+`?(?:skin1004-319714\.)?"
                         + re.escape(table.split(".", 1)[1]) + r"`?(?![\w.])", code, re.I)}


_STATUS = {"완료": "completed", "처리완료": "completed", "진행중": "in_progress",
           "처리중": "in_progress", "예정": "scheduled", "취소": "cancelled",
           "철회": "cancelled", "미확인": "unknown", "알수없음": "unknown"}
_CLAIM = {"반품": "return", "교환": "exchange", "재배송": "redelivery",
          "취소": "cancellation", "주문취소": "cancellation", "수거": "collection"}
# 국가 표기의 번역 사전이며 현재 데이터의 국가 목록은 value_lists에서 실측한다.
_COUNTRY = {
    "미국": "United States", "캐나다": "Canada", "멕시코": "Mexico", "스페인": "Spain",
    "프랑스": "France", "이탈리아": "Italy", "독일": "Germany", "영국": "United Kingdom",
    "호주": "Australia", "아랍에미리트": "United Arab Emirates", "아르헨티나": "Argentina",
    "콜롬비아": "Colombia", "벨기에": "Belgium", "러시아": "Russia", "아르메니아": "Armenia",
    "스위스": "Switzerland", "라트비아": "Latvia", "슬로바키아": "Slovakia", "아일랜드": "Ireland",
    "카자흐스탄": "Kazakhstan", "조지아": "Georgia", "폴란드": "Poland", "핀란드": "Finland",
    "네덜란드": "Netherlands", "리투아니아": "Lithuania", "사우디아라비아": "Saudi Arabia",
    "그리스": "Greece", "한국": "South Korea", "일본": "Japan", "중국": "China",
    "인도네시아": "Indonesia", "말레이시아": "Malaysia", "싱가포르": "Singapore",
    "필리핀": "Philippines", "베트남": "Vietnam", "태국": "Thailand", "대만": "Taiwan",
    "홍콩": "Hong Kong", "인도": "India", "브라질": "Brazil", "뉴질랜드": "New Zealand",
    "USA": "United States", "US": "United States", "UK": "United Kingdom", "UAE": "United Arab Emirates",
}
_QUOTED = re.compile(r"'(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/")


def _code_mask(sql: str) -> str:
    """리터럴·주석의 위치만 가려 SQL 코드의 오프셋을 보존한다."""
    return _QUOTED.sub(lambda m: " " * len(m.group()), sql)


def _map_column_literals(sql: str, column: str, mapping: dict[str, str]) -> str:
    def translate(value: str) -> str:
        key = re.sub(r"\s+", "", value)
        return mapping.get(key, mapping.get(value, value))

    col = rf"(?<![\w])`?{column}`?"
    pattern = re.compile(
        rf"(?P<prefix>{col}\s*(?:=|!=|<>|(?:NOT\s+)?LIKE)\s*)"
        r"(?P<quote>['\"])(?P<value>[^'\"]*)(?P=quote)"
        rf"|(?P<in_prefix>{col}\s+(?:NOT\s+)?IN\s*\()"
        r"(?P<body>(?:'[^']*'|\"[^\"]*\"|[^)])*)(?P<close>\))", re.I)
    mask = _code_mask(sql)

    def replace(match):
        if not mask[match.start():match.start() + 1].strip():
            return match.group()
        if match.group("prefix"):
            raw = match.group("value")
            stripped = raw.strip("%")
            value = raw.replace(stripped, translate(stripped)) if stripped else raw
            quote = match.group("quote")
            return match.group("prefix") + quote + value + quote
        body = re.sub(r"(['\"])([^'\"]*)\1", lambda m: m.group(1) + translate(m.group(2)) + m.group(1), match.group("body"))
        return match.group("in_prefix") + body + match.group("close")

    return pattern.sub(replace, sql)


def normalize_sql(sql: str) -> str:
    """CS 필터만 표준 코드로 옮긴다. 집계 단위나 사용자가 고른 날짜는 바꾸지 않는다."""
    tables = tables_in_sql(sql)
    if not tables:
        return sql
    # BigQuery의 dataset.table 단축 표기도 같은 허용목록과 출처 공시를 탄다.
    for table in tables:
        short = table.split(".", 1)[1]
        sql = re.sub(r"(\b(?:FROM|JOIN)\s+)`?" + re.escape(short) + r"`?(?![\w.])",
                     lambda m: m.group(1) + "`" + table + "`", sql, flags=re.I)
    sql = _map_column_literals(sql, "process_status", _STATUS)
    if DOMESTIC_TABLE in tables:
        sql = _map_column_literals(sql, "claim_type", _CLAIM)
    if OVERSEAS_TABLE in tables:
        sql = _map_column_literals(sql, "country_name", _COUNTRY)
    return sql


def validation_error(sql: str) -> str:
    """명확한 직접 합산 실수만 차단한다. 주문별 중복 제거한 CTE는 허용한다."""
    if OVERSEAS_TABLE not in tables_in_sql(sql):
        return ""
    code = _code_mask(normalize_sql(sql))
    # 직접 환불 행을 읽는 SELECT 투영부에 주문 전체 금액 SUM이 있으면 중복된다.
    # SELECT가 하나 더 있는 CTE/하위 쿼리를 넘어서 매칭하지 않는다.
    for match in re.finditer(r"\bSELECT\b((?:(?!\bSELECT\b|\bFROM\b)[\s\S])*)"
                             r"\bFROM\s+`?" + re.escape(OVERSEAS_TABLE) + r"`?\b", code, re.I):
        if re.search(r"\bSUM\s*\(\s*(?:DISTINCT\s+)?(?:\w+\.)?`?payment_amount_(?:usd|krw)\b", match.group(1), re.I):
            return ("해외 환불 행에는 주문 전체 결제금액이 반복됩니다. 결제금액은 주문 ID별로 "
                    "먼저 중복 제거해 집계하세요. 환불 금액은 refund_amount_usd 또는 refund_amount_krw를 사용하세요.")
    # 알 수 없는 환산액을 0으로 확정하는 것은 허용하지 않는다.
    amount = r"(?:\w+\.)?`?refund_amount_krw`?"
    if re.search(r"\b(?:COALESCE|IFNULL)\s*\(\s*(?:" + amount
                 + r"|SUM\s*\(\s*" + amount + r"\s*\))\s*,\s*0(?:\.0+)?\s*\)", code, re.I):
        return "원화 환불액 미확보는 0원이 아닙니다. NULL을 유지하고 미환산 건수를 별도로 집계하세요."
    return ""


class CSResultRows(list):
    """SQL에서 고유 집계한 열과 통화 정보를 화면 합계까지 전달한다."""

    def __init__(self, rows, non_additive_columns, tables, count_columns=()):
        super().__init__(rows)
        self.non_additive_columns = set(non_additive_columns)
        self.cs_tables = set(tables)
        self.count_columns = set(count_columns)


def prepare_results(sql: str, results):
    tables = tables_in_sql(sql)
    if not tables or not results:
        return results
    columns = set(results[0])
    if len(tables) > 1:
        # 국내 처리기록 + 해외 환불을 하나의 CS 합계로 섞지 않는다.
        excluded = columns
    else:
        excluded = {col for col in columns if re.search(r"distinct|unique|고유|중복.?제거", col, re.I)}
        for match in re.finditer(r"\bCOUNT\s*\(\s*DISTINCT\b[^)]*\)\s+(?:AS\s+)?(?:`([^`]+)`|(\w+))", sql, re.I):
            excluded.add(match.group(1) or match.group(2))
        # 중복 제거한 주문금액도 기간·유형 축 사이에서 같은 주문이 재등장한다.
        if "payment_amount_" in sql.casefold():
            excluded |= {col for col in columns if re.search(r"amount|payment|paid|금액|결제", col, re.I)}
    count_columns = set()
    for match in re.finditer(r"\b(?:COUNT|COUNTIF)\s*\((?:[^()]|\([^()]*\))*\)\s+(?:AS\s+)?(?:`([^`]+)`|(\w+))", sql, re.I):
        count_columns.add(match.group(1) or match.group(2))
    return CSResultRows(results, excluded, tables, count_columns)


def notice(sql: str, results=None) -> str:
    """모든 답변 경로에서 표보다 먼저 보여 주는 집계 범위 안내."""
    tables = tables_in_sql(sql)
    lines = []
    low = (sql or "").casefold()
    if DOMESTIC_TABLE in tables:
        lines.append("국내 자료는 **CS 처리 기록** 단위입니다. 고유 CS 사건 수와 다르며, 주문수는 중복 주문을 제외합니다.")
        if "compensation" in low:
            lines.append("보상액은 원본에 기록된 금액이며 **실제 지급 확인액이 아닙니다**.")
            if "eligible_recorded_compensation" in low:
                lines.append("집계 대상 보상액은 완료·비철회·검토 제외 기준입니다.")
            else:
                lines.append("원본 보상 기록액에는 철회·미완료·검토 대상이 포함될 수 있습니다.")
        if "source_order_amount" in low or "purchase_amount" in low or "paid_amount" in low:
            lines.append("국내 주문금액은 채널별 원본 구매금액·실결제금액의 기준이 달라 통합 순결제액으로 해석할 수 없습니다.")
    if OVERSEAS_TABLE in tables:
        lines.append("해외 자료는 **Shopify 환불 기록**입니다. 전체 해외 CS 접수 건수나 전체 반품 건수를 뜻하지 않습니다.")
        lines.append("해외 CS 대시보드의 구글시트 집계와는 별도 자료입니다.")
        if "refund_amount_krw" in low:
            lines.append("원화 환불액은 환산액이 있는 건만 집계합니다. 환율 미확보 건은 0원이 아닙니다.")
        if "return_type_normalized" in low or "intake_type" in low:
            lines.append("유형·사유의 미분류는 분류값이 미기재된 상태이며, 해당 사유의 발생이 없다는 뜻이 아닙니다.")
    if DOMESTIC_TABLE in tables:
        path, label = "dashboard", "국내 CS 대시보드"
        if re.search(r"\b(?:product_name|items)\b", low):
            path, label = "reports/product", "국내 CS 상품별 시각화"
        elif "reason_normalized" in low:
            path, label = "reports/reason", "국내 CS 사유별 시각화"
        elif re.search(r"\bchannel\b", low):
            path, label = "reports/channel", "국내 CS 판매처별 시각화"
        lines.append(f"관련 화면: [{label}]({DASHBOARD_URL}{path}) · 화면에서 기간과 조건을 선택할 수 있습니다.")
    if OVERSEAS_TABLE in tables:
        lines.append(f"관련 화면: [해외 CS 대시보드]({DASHBOARD_URL}global/dashboard) · 화면에서 기간과 조건을 선택할 수 있습니다.")
    return "".join("> " + line + "\n" for line in lines) + ("\n" if lines else "")


def answer_fact(sql: str) -> str:
    if not tables_in_sql(sql):
        return ""
    return ("\n## CS 집계 해석 (표·요약·인사이트에 모두 적용)\n" + notice(sql)
            + "기간 기준은 실행 SQL에서 사용한 접수일/환불 생성일/완료일을 정확히 밝힌다. "
            "국내 처리 기록과 해외 환불 건을 합쳐 전체 CS 접수로 부르지 않는다. "
            "월별·유형별 고유 주문수/주문금액은 같은 주문이 재등장할 수 있어 행을 더한 값을 전체 합계로 쓰지 않는다. "
            "해외 환불액 USD는 달러, KRW는 원화이며 통화를 바꾸지 않는다. "
            "환불액은 성공 거래액으로, 일부 성공분도 포함될 수 있다. "
            "미분류·미기재를 0건이나 문제가 없다는 뜻으로 해석하지 않는다. "
            "미완료 건의 환율 미확보를 ETL 오류라고 추정하지 않는다. "
            "해외 received_at은 연결된 반품 생성일이며 전체 접수일이 아니다. "
            "현재 결과는 상세 데이터 조회 기준이다. CS 대시보드를 실시간 조회한 결과라고 말하지 않는다. "
            "특히 해외 대시보드는 구글시트 자료이고 이 SQL은 Shopify 환불 자료이므로 같은 집계로 설명하지 않는다. "
            "후속 질문도 현재 자료로 답할 수 있는 범위에서만 제안한다.\n")


def answer_title(query: str, sql: str) -> str:
    if tables_in_sql(sql) == {OVERSEAS_TABLE} and re.search(r"인입|접수", query or ""):
        return "해외 CS 환불 기록 조회"
    return query
