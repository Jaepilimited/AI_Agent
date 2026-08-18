"""Text-to-SQL Agent using LangGraph.

Workflow: generate_sql → validate_sql → execute_sql → format_answer
"""

import hashlib
import json
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import structlog
from langgraph.graph import END, StateGraph

from app.config import get_settings
from app.core.bigquery import get_bigquery_client
import concurrent.futures

from app.core.llm import MODEL_CLAUDE, MODEL_GEMINI, get_flash_client, get_llm_client
from app.core.prompt_fragments import LANGUAGE_DETECTION_RULE
from app.core.security import (
    FI_ACCESS_DENIED_MESSAGE,
    SOURCE_SCOPE_DENIED_PREFIX,
    sanitize_sql,
    validate_sql,
)
from app.models.state import AgentState

logger = structlog.get_logger(__name__)


# ── SQL Cache ──────────────────────────────────────────────
# Caches (query → SQL) to skip LLM generation for repeated questions.
# In-memory LRU + MariaDB persistence.

_sql_cache: OrderedDict = OrderedDict()  # query_hash → {sql, tables} (LRU order)
_SQL_CACHE_MAX = 500


# Queries containing any of these get today's date folded into their cache key
# (see _cache_key) so "이번 달 매출" doesn't replay June's SQL after the month
# rolls over to July. Absolute-date queries ("2025년 3월 매출") don't need this.
_RELATIVE_DATE_KEYWORDS = (
    "이번 달", "이번달", "지난 달", "지난달", "올해", "작년", "어제", "오늘",
    "최근", "요즘", "이번 주", "이번주", "지난 주", "지난주", "this month", "last month",
)


def _cache_key(query: str, brand_filter: Optional[str] = None) -> str:
    """Normalize query and build cache key hash.

    Relative-date queries (오늘 날짜 기준) fold today's date into the key so
    the cached SQL expires across day/month boundaries; date-free queries
    keep a stable key so they can be reused indefinitely (subject to TTL).
    """
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    date_component = datetime.now().strftime("%Y-%m-%d") if any(
        kw in normalized for kw in _RELATIVE_DATE_KEYWORDS
    ) else ""
    raw = f"{normalized}|{brand_filter or ''}|{date_component}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _build_brand_section(brand_filter: Optional[str]) -> str:
    """Prompt fragment enforcing the user's group brand restriction."""
    if not brand_filter:
        return ""
    brands = [b.strip() for b in brand_filter.split(",") if b.strip()]
    brand_in = ", ".join(f"'{b}'" for b in brands)
    return (
        f"\n\n## ⚠️ 브랜드 필터 (최우선 적용)\n"
        f"매출/제품 관련 SQL에 반드시 `WHERE Brand IN ({brand_in})` 조건을 추가하세요.\n"
    )


def _extract_tables_from_sql(sql: str) -> set:
    """Extract BigQuery table paths from SQL for cache validation."""
    return set(re.findall(r'`(skin1004-319714\.[^`]+)`', sql))


# Large tables that MUST have a date filter for acceptable scan performance.
_LARGE_TABLES_REQUIRING_DATE_FILTER = [
    "SALES_ALL_Backup",
    "integrated_ad",
    "Integrated_marketing_cost",
]


# 전 테이블 공통: Country 값은 한국어 국가명. 프롬프트 지시(기본 규칙 + 스키마 경고
# + 말미 리마인더)에도 LLM이 확률적으로 영어 리터럴을 뽑는 사례가 남는다
# (2026-08-10 '인도네시아쪽은?' 실측 — 같은 코드에서 한국어/영어가 갈렸다).
# 프롬프트는 확률을 높일 뿐이므로, 생성 후 결정적으로 교정한다.
_COUNTRY_EN2KR = {
    "INDONESIA": "인도네시아", "USA": "미국", "UNITED STATES": "미국",
    "AMERICA": "미국", "JAPAN": "일본", "CHINA": "중국", "TAIWAN": "대만",
    "HONG KONG": "홍콩", "KOREA": "한국", "SOUTH KOREA": "한국",
    "THAILAND": "태국", "VIETNAM": "베트남", "SINGAPORE": "싱가포르",
    "MALAYSIA": "말레이시아", "PHILIPPINES": "필리핀", "AUSTRALIA": "호주",
    "GERMANY": "독일", "UK": "영국", "UNITED KINGDOM": "영국",
    "FRANCE": "프랑스", "CANADA": "캐나다", "INDIA": "인도", "RUSSIA": "러시아",
    "SPAIN": "스페인", "ITALY": "이탈리아", "NETHERLANDS": "네덜란드",
    "POLAND": "폴란드", "BRAZIL": "브라질", "MEXICO": "멕시코",
    "SAUDI ARABIA": "사우디아라비아", "UAE": "아랍에미리트",
    "UNITED ARAB EMIRATES": "아랍에미리트", "EGYPT": "이집트",
    "KAZAKHSTAN": "카자흐스탄", "UKRAINE": "우크라이나",
    "CAMBODIA": "캄보디아", "MYANMAR": "미얀마", "LAOS": "라오스",
}

_re_country_eq = re.compile(
    r"(Country\s*(?:=|!=|<>)\s*)'([A-Za-z][A-Za-z .]*)'", re.IGNORECASE)
_re_country_like = re.compile(
    r"(Country\s+LIKE\s*)'(%?)([A-Za-z][A-Za-z .]*)(%?)'", re.IGNORECASE)
_re_country_in = re.compile(
    r"(Country\s+(?:NOT\s+)?IN\s*\()([^)]*)(\))", re.IGNORECASE)


def _localize_country_literals(sql: str) -> str:
    """Country 비교의 영어 국가명 리터럴을 한국어로 교정 (사전에 없는 값은 유지)."""
    def _eq(m):
        kr = _COUNTRY_EN2KR.get(m.group(2).strip().upper())
        return m.group(1) + "'" + kr + "'" if kr else m.group(0)

    def _like(m):
        kr = _COUNTRY_EN2KR.get(m.group(3).strip().upper())
        if not kr:
            return m.group(0)
        return m.group(1) + "'" + m.group(2) + kr + m.group(4) + "'"

    def _in(m):
        body = re.sub(
            r"'([A-Za-z][A-Za-z .]*)'",
            lambda mm: "'" + _COUNTRY_EN2KR.get(
                mm.group(1).strip().upper(), mm.group(1)) + "'",
            m.group(2))
        return m.group(1) + body + m.group(3)

    sql = _re_country_eq.sub(_eq, sql)
    sql = _re_country_like.sub(_like, sql)
    sql = _re_country_in.sub(_in, sql)
    return sql


# ── 팀명 ↔ Team_NEW 코드 (2026-08-11 공식 팀명 확정) ─────────────────────────
# 데이터에는 코드(B2B1·JBT…)만 들어 있는데 사내에서는 한글 팀명을 쓴다.
# 둘 다 통해야 하므로 (1) 질문·SQL 의 한글명은 코드로 교정하고,
# (2) 답변 표시는 한글명을 쓴다. 국가 리터럴과 같은 이유로 프롬프트만으로는
# 보증되지 않아 생성 후 결정적으로 교정한다.
TEAM_CODE2KR = {
    "B2B1": "영업1팀", "B2B2": "영업2팀",
    # DT1·DT2 는 팀이고 각각 유통1본부·유통2본부 소속이다. 조직도에는 그 아래
    # 리테일_UMMA·리테일1~3 / 뉴비즈1·뉴비즈2·코스트코 같은 이름이 더 있지만
    # Team_NEW 에는 없다 — 그 단위로는 나눌 수 없다 (2026-08-11 조직도 대조).
    "DT1": "유통1팀", "DT2": "유통2팀",
    "EAST1": "동남아시아1팀", "EAST2": "동남아시아2팀",
    "WEST_MKT": "서구권마케팅팀", "WEST_Ecomm": "서구권이커머스팀",
    "CBT": "중국사업팀", "JBT": "일본사업팀", "KBT": "한국사업팀",
    "BCM": "브랜드커뮤니케이션팀",
}

# 본부(Division) → 소속 Team_NEW 코드 (2026-08-11 조직도 확정).
# "본부별"·"사업부별" 질문은 이 그룹으로 묶는다.
TEAM_DIVISIONS = {
    "글로벌마케팅본부": ["CBT", "EAST1", "EAST2", "JBT", "KBT", "WEST_Ecomm", "WEST_MKT"],
    "영업1본부": ["B2B1", "B2B2"],
    "유통1본부": ["DT1"],
    "유통2본부": ["DT2"],
    "상품본부": ["BCM"],
}
TEAM_CODE2DIVISION = {
    code: div for div, codes in TEAM_DIVISIONS.items() for code in codes
}

# 답변에 쓰는 팀 표기 규칙 — 포맷 프롬프트 3곳(비스트리밍·스트리밍·fast-answer)이
# 같은 문구를 쓰도록 한 곳에 둔다. 예전에는 세 곳이 제각각 "KBT=국내사업" 같은
# 비공식 설명을 들고 있었다.
TEAM_DISPLAY_RULE = (
    "⚠️ 팀 표기(임의 해석 금지): 아래가 공식 팀명이다. 표·차트 라벨·본문 모두 "
    "`한글팀명(코드)` 형식으로 쓰라 (예: `영업1팀(B2B1)`).\n"
    + ", ".join(f"{c}={kr}" for c, kr in TEAM_CODE2KR.items())
    + "\n`기타`·`OP` 는 정식 팀이 아니다. JBT 를 좀비뷰티 등 다른 의미로 지어내지 마라 "
    "(2026-08-07 실제 오답)."
)

# 조회 키는 공백 제거 + 끝의 '팀' 제거 형태 ('영업 1팀' · '영업1' · '영업1팀' 모두 매칭)
_TEAM_KR2CODE = {
    "영업1": "B2B1", "영업2": "B2B2",
    "유통1": "DT1", "유통2": "DT2",
    "유통1본부": "DT1", "유통2본부": "DT2",
    "동남아시아1": "EAST1", "동남아시아2": "EAST2",
    "동남아1": "EAST1", "동남아2": "EAST2",
    "서구권마케팅": "WEST_MKT", "서구권이커머스": "WEST_Ecomm",
    "중국사업": "CBT", "일본사업": "JBT", "한국사업": "KBT",
    "브랜드커뮤니케이션": "BCM", "브랜드컴": "BCM",
}

# 광고 테이블(integrated_ad)의 팀 컬럼명은 `team` 이고 값 체계는 Team_NEW 와 같다
# (2026-08-11 실측: EAST1·EAST2·WEST_MKT·WEST_Ecomm·CBT·JBT·KBT·기타).
# 두 컬럼이 같은 교정을 받아야 "서구권마케팅팀 광고비" 가 통한다.
# `\bteam\b` 는 `Team_NEW` 와 겹치지 않는다 ('_' 가 단어 문자라 경계가 서지 않음).
_TEAM_COL = r"\b(?:Team_NEW|team)\b"
_re_team_eq = re.compile(rf"({_TEAM_COL}\s*)(=|!=|<>)(\s*)'([^']*)'", re.IGNORECASE)
_re_team_like = re.compile(
    rf"({_TEAM_COL}\s+(?:NOT\s+)?LIKE\s*)'(%?)([^%']*)(%?)'", re.IGNORECASE)
# ⚠️ 본문을 `[^)]*` 로 잡으면 리터럴 안의 괄호에서 잘린다 — 표시형 '영업1팀(B2B1)' 이
# 바로 그 경우다. 따옴표 문자열은 통째로 삼키고, 그 밖에서만 ')' 를 끝으로 본다.
_re_team_in = re.compile(
    rf"({_TEAM_COL}\s+(?:NOT\s+)?IN\s*\()((?:'[^']*'|[^)])*)(\))", re.IGNORECASE)

# 표시형 '영업1팀(B2B1)' 에서 코드만 뽑는다. 답변 표가 이 형식이라 다음 턴 컨텍스트에
# 그대로 들어가고, LLM 이 그 문자열을 필터로 쓰면 0건이 난다 — 우리가 만든 표기가
# 스스로 판 함정이므로 여기서 되돌린다 (2026-08-11 재검토에서 발견).
_re_code_in_parens = re.compile(r"\(([A-Za-z0-9_]+)\)\s*$")


def _team_code(literal: str) -> Optional[str]:
    """한글 팀명 또는 표시형('영업1팀(B2B1)') → 코드. 팀명이 아니면 None."""
    raw = (literal or "").strip()
    m = _re_code_in_parens.search(raw)
    if m and m.group(1) in TEAM_CODE2KR:
        return m.group(1)
    key = re.sub(r"\s+", "", raw)
    key = re.sub(r"팀$", "", key)
    return _TEAM_KR2CODE.get(key)


def _relabel_team_values(results):
    """결과 행의 팀 코드를 '한글팀명(코드)' 로 치환한다.

    표기를 LLM 지시에만 맡기면 확률적으로 코드가 그대로 나온다. 값 자체를 바꾸면
    표·차트 라벨·인사이트가 한 번에 맞는다 (차트는 이 값을 라벨로 그대로 쓴다).
    """
    if not results:
        return results
    # 컬럼명이 team/Team_NEW 만은 아니다 — LLM 이 `Team AS team_code` 처럼 별칭을
    # 붙이면 정확 일치로는 놓친다 (2026-08-11 '본부별 마케팅비' 에서 코드 노출).
    # 값이 팀 코드일 때만 바꾸므로 team_count 같은 숫자 컬럼은 영향이 없다.
    team_cols = [k for k in results[0].keys() if "team" in k.lower() or "팀" in k]
    if not team_cols:
        return results
    for row in results:
        for col in team_cols:
            v = row.get(col)
            # 프로모션 테이블은 team_id 가 'east1'·'west-mkt' 형식이라 코드 표와
            # 키가 다르다 — 두 체계 모두 한글 팀명으로 보이게 한다
            kr = TEAM_CODE2KR.get(v) or _PROMO_ID2KR.get(v)
            if kr:
                row[col] = f"{kr}({v})"
    return results


# ── 프로모션 캘린더(promotion_calendar.promotion) 전용 값 체계 ──────────────
# 이 테이블만 팀·국가 표기가 다르다 (2026-08-11 실측):
#   team_id      = 소문자·하이픈  east1 / west-mkt / west-ecomm / kbt / jbt / cbt / bcm
#   country_code = 2글자 ISO      ID / MY / PH / KR / US / SG / JP / AU / CN / GLOBAL
# 다른 테이블은 Team_NEW 대문자 코드와 **한국어 국가명**을 쓴다. 섞이면 0건이 난다
# — 광고 테이블에서 똑같이 당했으므로 처음부터 결정적으로 교정한다.
_PROMO_TEAM_ID = {code: code.lower().replace("_", "-") for code in TEAM_CODE2KR}
_PROMO_ID2KR = {pid: TEAM_CODE2KR[code] for code, pid in _PROMO_TEAM_ID.items()}

_COUNTRY_KR2ISO = {
    "인도네시아": "ID", "말레이시아": "MY", "필리핀": "PH", "한국": "KR",
    "미국": "US", "싱가포르": "SG", "일본": "JP", "호주": "AU", "중국": "CN",
    "글로벌": "GLOBAL", "전세계": "GLOBAL",
    "대만": "TW", "베트남": "VN", "태국": "TH", "영국": "GB", "독일": "DE",
    "캐나다": "CA", "프랑스": "FR", "스페인": "ES", "인도": "IN",
}

_re_promo_team = re.compile(
    r"(\bteam_id\b\s*(?:=|!=|<>)\s*)'([^']*)'", re.IGNORECASE)
_re_promo_team_in = re.compile(
    r"(\bteam_id\b\s+(?:NOT\s+)?IN\s*\()((?:'[^']*'|[^)])*)(\))", re.IGNORECASE)
_re_country_code = re.compile(
    r"(\bcountry_code\b\s*(?:=|!=|<>)\s*)'([^']*)'", re.IGNORECASE)
_re_country_code_in = re.compile(
    r"(\bcountry_code\b\s+(?:NOT\s+)?IN\s*\()((?:'[^']*'|[^)])*)(\))", re.IGNORECASE)


# Team_NEW 코드는 대소문자가 섞여 있다('WEST_Ecomm') — 대문자 키로 찾을 수 있게 색인
_TEAM_CODE_CI = {code.upper(): code for code in TEAM_CODE2KR}


def _promo_team_id(literal: str) -> Optional[str]:
    """한글 팀명·Team_NEW 코드·표시형 → promotion.team_id 값."""
    raw = (literal or "").strip()
    if raw.lower() in set(_PROMO_TEAM_ID.values()):
        return None  # 이미 올바른 형식
    code = _team_code(raw) or _TEAM_CODE_CI.get(raw.upper())
    return _PROMO_TEAM_ID.get(code) if code else None


def _iso_country(literal: str) -> Optional[str]:
    """한국어 국가명 → 2글자 ISO 코드 (이미 코드면 None)."""
    raw = re.sub(r"\s+", "", literal or "")
    return _COUNTRY_KR2ISO.get(raw)


# ⛔ **묻지 않은 브랜드 필터가 매출을 조용히 깎고 있었다** (2026-08-14 제보로 확인).
#    "26년 7월 미국·인도네시아·말레이시아·호주·멕시코·캐나다 매출" 질문에
#    LLM 이 `Brand IN ('SK','CL','CBT')` 를 스스로 붙여 **우마(UM)를 통째로 뺐다**:
#
#        멕시코  8.15억  ← 답변      /  9.32억  실제  (UM 1.17억 누락)
#        미국   86.55억  ← 답변      / 219.34억 실제  (UM 132.79억 누락 · 61%)
#        캐나다  6.56억  ← 답변      /  17.83억 실제  (UM 11.27억 누락 · 63%)
#
#    답변은 조회 조건에 "대상 브랜드: SK, CL, CBT"라고 적었지만, 국가별 매출을 물은
#    사람이 그 줄을 브랜드 한정으로 읽을 이유가 없다. **틀린 티가 안 나는 실패다.**
#
#    원인은 프롬프트가 자기 자신과 모순인 것이다 — 73행은 "국가별 매출 → 브랜드
#    필터 없이"인데 4곳이 "Brand IN ('SK','CL') 필수"라고 적혀 있었다(그중 하나는
#    국가 질문 예시였다). 프롬프트를 고쳐도 지시는 확률일 뿐이라 여기서 보증한다 —
#    국가 리터럴·팀 리터럴 교정과 같은 계열이다.
#
#    ⚠️ 제품/라인 질문에서는 이 필터가 **맞다** (UM·CBT 는 제품명이 100% 비어 있다).
#       그래서 질문에 제품어나 브랜드명이 하나라도 있으면 건드리지 않는다.
_BRAND_TERMS = ("브랜드", "스킨천사", "스킨1004", "skin1004", "우마", "umma", "um ",
                "좀비뷰티", "좀비", "커먼랩스", "commonlabs", "라인별")
_PRODUCT_TERMS = ("제품", "품목", "sku", "라인", "카테고리", "세트", "앰플", "크림",
                  "토너", "선크림", "패드", "클렌징", "마스크", "세럼", "에센스")
# 스킨천사 계열만 남기고 UM/DD 를 빼는 필터 (이것만 대상으로 한다)
_SUBSET_BRAND_RE = re.compile(
    r"\s*AND\s+Brand\s+IN\s*\(\s*(?:'(?:SK|CL|CBT)'\s*,?\s*){2,3}\)", re.I)


def _strip_unrequested_brand_filter(sql: str, question: str) -> str:
    """묻지 않은 브랜드 축소를 걷어낸다. **프롬프트가 이미 정한 규칙을 강제할 뿐이다.**"""
    if not sql or not question or not _SUBSET_BRAND_RE.search(sql):
        return sql
    q = question.lower()
    if any(t in q for t in _BRAND_TERMS) or any(t in q for t in _PRODUCT_TERMS):
        return sql                      # 브랜드·제품을 물었으면 그 필터가 맞다
    cleaned = _SUBSET_BRAND_RE.sub("", sql)
    if cleaned != sql:
        logger.warning("brand_filter_stripped", question=question[:120],
                       removed=_SUBSET_BRAND_RE.search(sql).group(0).strip())
    return cleaned


def _localize_promotion_literals(sql: str) -> str:
    """promotion 테이블 전용 리터럴 교정 (team_id·country_code)."""
    def _sub_one(m, fn):
        v = fn(m.group(2))
        return m.group(1) + "'" + v + "'" if v else m.group(0)

    def _sub_in(m, fn):
        body = re.sub(
            r"'([^']*)'",
            lambda mm: "'" + (fn(mm.group(1)) or mm.group(1)) + "'",
            m.group(2))
        return m.group(1) + body + m.group(3)

    sql = _re_promo_team.sub(lambda m: _sub_one(m, _promo_team_id), sql)
    sql = _re_promo_team_in.sub(lambda m: _sub_in(m, _promo_team_id), sql)
    sql = _re_country_code.sub(lambda m: _sub_one(m, _iso_country), sql)
    sql = _re_country_code_in.sub(lambda m: _sub_in(m, _iso_country), sql)
    return sql


def _division_codes(literal: str) -> Optional[list]:
    """본부명 리터럴 → 소속 팀 코드 목록. 본부명이 아니면 None."""
    return TEAM_DIVISIONS.get(re.sub(r"\s+", "", literal or ""))


def _localize_team_literals(sql: str) -> str:
    """팀 비교의 한글 팀명·본부명을 코드로 교정 (사전에 없는 값은 유지).

    - 팀명   → 코드            `= '영업1팀'`       → `= 'B2B1'`
    - 표시형 → 코드            `= '영업1팀(B2B1)'` → `= 'B2B1'`
    - 본부명 → 소속 코드 IN절  `= '영업1본부'`     → `IN ('B2B1','B2B2')`
    """
    def _eq(m):
        col, op, gap, lit = m.group(1), m.group(2), m.group(3), m.group(4)
        codes = _division_codes(lit)
        if codes:
            # 본부는 단일 값이 아니라 팀 묶음이다 — 비교 연산 자체를 바꾼다
            joined = ", ".join(f"'{c}'" for c in codes)
            return f"{col}{'NOT IN' if op in ('!=', '<>') else 'IN'} ({joined})"
        code = _team_code(lit)
        return f"{col}{op}{gap}'{code}'" if code else m.group(0)

    def _like(m):
        code = _team_code(m.group(3))
        if not code:
            return m.group(0)
        return m.group(1) + "'" + m.group(2) + code + m.group(4) + "'"

    def _in(m):
        def _one(mm):
            lit = mm.group(1)
            codes = _division_codes(lit)
            if codes:                      # 본부명은 소속 팀 코드들로 펼친다
                return ", ".join(f"'{c}'" for c in codes)
            return "'" + (_team_code(lit) or lit) + "'"

        return m.group(1) + re.sub(r"'([^']*)'", _one, m.group(2)) + m.group(3)

    sql = _re_team_eq.sub(_eq, sql)
    sql = _re_team_like.sub(_like, sql)
    sql = _re_team_in.sub(_in, sql)
    return sql


def _enforce_partition_filter(
    sql: str,
    query: str,
    cache_key: Optional[str] = None,
    brand_filter: Optional[str] = None,
    can_view_fi: bool = False,
    allowed_tables: Optional[set] = None,
) -> str:
    """If SQL targets a large table without any date filter, request Flash re-gen.

    Returns the original sql unchanged when:
    - No large table is targeted
    - A date filter is already present inside the WHERE clause (case-insensitive)

    Returns a re-generated sql when:
    - A large table is targeted AND no date filter found in WHERE clause

    Note: wiki_context is intentionally omitted from the re-gen prompt — the
    sql_generator.txt already includes full schema context; adding wiki text
    would add latency without improving filter correctness.

    Args:
        cache_key: If provided and rewrite succeeds, updates the SQL cache so
                   subsequent identical queries get the corrected SQL (not the
                   filterless one that would trigger enforcement every time).
    """
    if not sql:
        return sql

    targets_large = any(t in sql for t in _LARGE_TABLES_REQUIRING_DATE_FILTER)
    if not targets_large:
        return sql

    where_match = re.search(r'\bWHERE\b(.*)', sql, re.IGNORECASE | re.DOTALL)
    has_date_filter = bool(
        where_match and re.search(r'\bdate\b', where_match.group(1), re.IGNORECASE)
    )
    if has_date_filter:
        return sql

    logger.info("partition_filter_missing_rewrite", sql=sql[:200])
    retry_prompt = (
        _load_prompt("sql_generator.txt", can_view_fi=can_view_fi)
        + f"\n\n## 사용자 질문\n{query}"
        + "\n\n⚠️⚠️ 이전 SQL이 대형 테이블 전체를 스캔합니다 (매우 느림)!"
        + "\n반드시 WHERE Date BETWEEN ... AND ... 날짜 조건을 추가하세요."
        + "\n기간 미지정 시 기본값: DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) ~ CURRENT_DATE()"
        + "\nSELECT * 금지 — 필요한 컬럼만 선택하세요."
        + _build_brand_section(brand_filter)
    )
    try:
        llm = get_flash_client()
        new_sql = llm.generate(retry_prompt, temperature=0.0, max_output_tokens=10000)
        new_sql = sanitize_sql(new_sql)
        new_sql = _localize_country_literals(new_sql)
        new_sql = _localize_team_literals(new_sql)
        new_sql = _localize_promotion_literals(new_sql)
        new_sql = _strip_unrequested_brand_filter(new_sql, query)
        if new_sql and len(new_sql) > 10:
            if allowed_tables is None:
                allowed_tables = _allowed_tables_from_sources(None, can_view_fi)
            is_valid, _ = validate_sql(new_sql, allowed_tables=allowed_tables)
            if is_valid:
                logger.info("partition_filter_rewritten", new_sql=new_sql[:200])
                if cache_key:
                    _cache_store(cache_key, query, new_sql, brand_filter)
                return new_sql
    except Exception as e:
        logger.warning("partition_filter_rewrite_failed", error=str(e))
    return sql


def _cache_lookup(query_hash: str, allowed_tables: Optional[set] = None) -> Optional[str]:
    """Check cache, then validate cached SQL only uses allowed tables."""
    sql = None

    # 1. In-memory (move to end for LRU tracking)
    if query_hash in _sql_cache:
        _sql_cache.move_to_end(query_hash)
        sql = _sql_cache[query_hash]

    # 2. MariaDB persistent cache (30-day TTL — stale rows are treated as a miss)
    if sql is None:
        try:
            from app.db.mariadb import fetch_one
            row = fetch_one(
                "SELECT generated_sql FROM sql_cache "
                "WHERE query_hash = %s AND last_used_at > NOW() - INTERVAL 30 DAY",
                (query_hash,),
            )
            if row:
                sql = row["generated_sql"]
                _sql_cache[query_hash] = sql  # warm in-memory
        except Exception as e:
            logger.debug("sql_cache_db_miss", error=str(e))

    if sql is None:
        return None

    # 3. Validate: cached SQL must only use currently allowed tables
    if allowed_tables is not None:
        sql_tables = _extract_tables_from_sql(sql)
        if sql_tables and not sql_tables.issubset(allowed_tables):
            logger.info("sql_cache_table_mismatch",
                        cached_tables=list(sql_tables),
                        allowed=list(allowed_tables))
            return None  # Cache hit but targets disallowed table → skip

    # Real hit (survived the table-filter check) → bump hit metric, fire-and-forget.
    # (In-memory OrderedDict entries carry no timestamp, so they aren't TTL-filtered;
    # a process restart naturally bounds their lifetime.)
    try:
        from app.db.mariadb import execute
        execute(
            "UPDATE sql_cache SET hit_count = hit_count + 1, last_used_at = NOW() "
            "WHERE query_hash = %s",
            (query_hash,),
        )
    except Exception as e:
        logger.debug("sql_cache_hit_count_update_failed", error=str(e))

    return sql


def _cache_store(query_hash: str, query: str, sql: str, brand_filter: Optional[str] = None) -> None:
    """Store in both in-memory and MariaDB."""
    # In-memory LRU: evict least-recently-used if full
    if query_hash in _sql_cache:
        _sql_cache.move_to_end(query_hash)
    elif len(_sql_cache) >= _SQL_CACHE_MAX:
        _sql_cache.popitem(last=False)  # Remove LRU entry
    _sql_cache[query_hash] = sql

    # MariaDB
    try:
        from app.db.mariadb import execute
        execute(
            "INSERT INTO sql_cache (query_hash, query_text, generated_sql, brand_filter) "
            "VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE generated_sql = VALUES(generated_sql), "
            "last_used_at = NOW()",
            (query_hash, query[:500], sql, brand_filter),
        )
    except Exception as e:
        logger.debug("sql_cache_store_failed", error=str(e))

def invalidate_cache_for_query(query: str, brand_filter: Optional[str] = None) -> None:
    """👎 피드백 시 해당 쿼리의 SQL 캐시 무효화."""
    key = _cache_key(query, brand_filter)
    _sql_cache.pop(key, None)
    try:
        from app.db.mariadb import execute
        execute("DELETE FROM sql_cache WHERE query_hash = %s", (key,))
        logger.info("sql_cache_invalidated", query=query[:80])
    except Exception as e:
        logger.debug("sql_cache_invalidate_failed", error=str(e))


# Load prompts
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# Schema cache: per-table individual caches for lazy loading
_schema_cache_sales: str = ""
_schema_cache_tables: Dict[str, str] = {}  # table_path -> schema text

# Marketing / review / ad tables with keyword triggers for lazy loading
MARKETING_TABLES = [
    ("skin1004-319714.marketing_analysis.integrated_ad", "통합 광고 데이터",
     ["광고", "ad", "advertising", "클릭", "노출", "roas", "cpc", "cpm", "ctr", "cvr", "전환",
      "틱톡광고", "페이스북광고", "메타광고 비용", "메타 광고비", "구글광고", "카카오", "네이버광고",
      "meta", "메타", "tiktok", "google", "amazon ads", "amazonads", "아마존광고",
      "snapchat", "스냅챗", "tokopedia", "토코피디아", "rakuten", "라쿠텐",
      "x광고", "twitter광고", "트위터광고", "dsp", "qoo10광고", "큐텐광고",
      "계정별", "팀별 광고", "매체별", "account_name"]),
    ("skin1004-319714.marketing_analysis.Integrated_marketing_cost", "통합 마케팅 비용",
     ["마케팅", "마캐팅", "marketing", "비용", "roi", "매체", "팀별"]),
    ("skin1004-319714.marketing_analysis.shopify_analysis_sales", "Shopify 판매 데이터",
     ["shopify", "쇼피파이", "반품", "return", "환불"]),
    ("skin1004-319714.Platform_Data.raw_data", "플랫폼 메트릭스",
     ["플랫폼", "platform", "순위", "rank", "가격", "할인", "채널별 제품", "채널별 가격"]),
    ("skin1004-319714.marketing_analysis.influencer_input_ALL_TEAMS", "인플루언서 마케팅",
     ["인플루언서", "influencer", "팔로워", "캠페인", "kol",
      "cpv", "조회수", "좋아요", "댓글수", "저장수", "공유수", "시딩",
      "유가 협업", "무가 협업", "에이전시", "티어", "마케팅 성과"]),
    ("skin1004-319714.marketing_analysis.amazon_search_analytics_catalog_performance", "아마존 검색 분석",
     ["아마존 검색", "amazon search", "장바구니", "cart", "ctr", "전환율", "asin"]),
    # ⛔ **통합 테이블이 정본이다** (2026-08-18). 몰별 테이블만 있던 탓에 "국내몰 리뷰"가
    #    스마트스토어만 세고(42,427 중 4,140), "플래그십 리뷰"는 조회조차 못 했다
    #    (이주훈 님 제보). 통합분을 먼저 두어 키워드가 이쪽에 먼저 걸리게 한다.
    ("skin1004-319714.Review_Data.Korea_mall_Review", "국내몰 리뷰(통합)",
     ["국내몰 리뷰", "국내 리뷰", "국내몰리뷰", "올리브영 리뷰", "무신사 리뷰", "musinsa 리뷰",
      "지그재그 리뷰", "ably 리뷰", "에이블리 리뷰", "w컨셉 리뷰", "29cm 리뷰",
      "스마트스토어 리뷰", "smartstore review", "네이버 리뷰", "국내자사몰 리뷰"]),
    ("skin1004-319714.Review_Data.Oversea_mall_Review", "해외몰 리뷰(통합)",
     ["해외몰 리뷰", "해외 리뷰", "해외몰리뷰", "아마존 리뷰", "amazon review",
      "큐텐 리뷰", "qoo10 review", "큐텐리뷰", "쇼피 리뷰", "shopee review", "쇼피리뷰",
      "해외 자사몰 리뷰"]),
    ("skin1004-319714.Review_Data.Store_Review", "매장(플래그십) 리뷰",
     ["플래그십", "매장 리뷰", "오프라인 리뷰", "스토어 리뷰", "구글맵", "네이버 플레이스",
      "명동", "뉴욕 매장", "shopname", "매장별 리뷰", "별점"]),
    ("skin1004-319714.ad_data.meta data_test", "메타 광고 라이브러리",
     ["메타 광고", "meta ad", "페이스북 광고 라이브러리", "인스타 광고"]),
    ("skin1004-319714.promotion_calendar.promotion", "프로모션 캘린더",
     ["프로모션", "promotion", "행사", "이벤트", "프로모", "기획전",
      "캘린더", "일정", "스케줄", "schedule", "언제 하", "예정",
      "블랙프라이데이", "black friday", "메가와리 일정", "런칭", "launch"]),
]

# Backward-compatible flat schema cache (filled on first full load)
_schema_cache: str = ""


_prompt_cache: dict = {}


def _mask_fi_prompt(prompt: str) -> str:
    """Remove the FI routing row and table section without touching neighbors."""
    prompt = re.sub(
        r"(?m)^\|[^\r\n]*`FI_LLM_Flat`[^\r\n]*\|\s*\r?\n?",
        "",
        prompt,
    )
    return re.sub(
        r"(?ms)^## 테이블 14: FI_LLM_Flat[^\r\n]*(?:\r?\n).*?(?=^## |\Z)",
        "",
        prompt,
    )


def build_team_section() -> str:
    """팀·본부 매핑 프롬프트 섹션을 코드 상수에서 생성한다.

    ⚠️ 조직 매핑을 프롬프트 텍스트에도 적어두면 조직이 바뀔 때 코드와 어긋난다.
    실제로 TEAM_DIVISIONS 를 만들어놓고 프롬프트에는 같은 표를 손으로 또 적어
    두 곳이 따로 놀았다 (2026-08-11 재검토). 진실은 코드 상수 하나뿐이다.
    """
    rows = "\n".join(
        f"| {kr} | `{code}` | {TEAM_CODE2DIVISION.get(code, '-')} |"
        for code, kr in TEAM_CODE2KR.items()
    )
    case_lines = []
    for div, codes in TEAM_DIVISIONS.items():
        cond = (f"Team_NEW = '{codes[0]}'" if len(codes) == 1
                else "Team_NEW IN (" + ",".join(f"'{c}'" for c in codes) + ")")
        case_lines.append(f"    WHEN {cond} THEN '{div}'")
    case_sql = "  CASE\n" + "\n".join(case_lines) + "\n  END AS division"
    div_rows = "\n".join(
        f"| **{div}** | " + " · ".join(f"`{c}`({TEAM_CODE2KR[c]})" for c in codes) + " |"
        for div, codes in TEAM_DIVISIONS.items()
    )
    return f"""#### ⭐ 공식 팀명 ↔ 코드 ↔ 본부 (조직도 기준 — 둘 다 통해야 한다)

**데이터에는 코드만 들어 있다.** 사용자가 한글 팀명으로 물어도 SQL 에는 **반드시 코드**를 쓴다.

| 공식 팀명 | 코드 | 본부 |
|---|---|---|
{rows}

- ✅ `WHERE Team_NEW = 'B2B1'` ← "영업1팀 매출 알려줘"
- ❌ `WHERE Team_NEW = '영업1팀'` ← **0건이 난다. 한글 팀명은 데이터에 없다**
- "동남아1팀"·"영업 1팀"처럼 줄여 쓰거나 띄어 써도 같은 팀이다
- **`기타`·`OP` 는 정식 팀이 아니다** — "팀별"로 나눌 때는
  `AND Team_NEW NOT IN ('기타','OP')` 로 제외한다

#### ⭐ 본부(Division) → 소속 팀

| 본부 | 소속 팀 |
|---|---|
{div_rows}

- "본부별"·"사업부별" 질문은 이 그룹으로 묶는다:
  ```sql
{case_sql}
  ```
- 조직도 팀명의 `GM ` 접두(`GM CBT` 등)는 **데이터에 없다** — 떼고 코드로 조회
- 조직도에는 유통 쪽에 리테일_UMMA·리테일1~3 / 뉴비즈1·2·코스트코 같은 이름이
  더 있지만 `Team_NEW` 에는 없다. 그 단위로 물으면 **나눌 수 없다고 안내**할 것
  (코스트코·ULTA 거래는 전부 `DT2`)"""


def _load_prompt(filename: str, can_view_fi: bool = False) -> str:
    """Load a prompt template from the prompts directory (cached after first read)."""
    cache_key = (filename, bool(can_view_fi))
    if cache_key not in _prompt_cache:
        prompt_path = PROMPTS_DIR / filename
        prompt = prompt_path.read_text(encoding="utf-8")
        prompt = prompt.replace("{{TEAM_SECTION}}", build_team_section())
        # ⛔ 손으로 적은 값 목록은 **반드시 낡는다.** `{{VALUES:이름}}` 을 실측으로 채운다.
        #    2026-08-18 실측: Continent1 의 `남미`·`중미` 가 **`중남미` 로 통합**됐는데
        #    프롬프트만 옛 값을 들고 있었다 → "남미 매출" 은 0건이 난다.
        #    같은 부류로 에콰도르(191개 중 12개만 나열)·메가와리(2026 Q2 누락)를 겪었다.
        # ⚠️ 캐시가 없으면 그 줄만 빠진다 — 배치가 채우고, 자가 점검이 감시한다.
        try:
            from app.core.value_lists import fill as _fill_values
            prompt = _fill_values(prompt)
        except Exception as _e:
            logger.warning("value_list_fill_failed", error=str(_e)[:140])
        if not can_view_fi:
            prompt = _mask_fi_prompt(prompt)
        _prompt_cache[cache_key] = prompt
    return _prompt_cache[cache_key]


# --- Schema context helpers (shared by generate_sql and the tool-loop agent) ---


def _source_table_map(settings) -> dict:
    """데이터소스(@@) 이름 → BigQuery 테이블 경로.

    허용목록 계산과 "어느 소스를 켜야 하나" 안내가 같은 표를 봐야 어긋나지 않는다.
    """
    return {
        "매출": [settings.sales_table_full_path],
        "제품": [f"{settings.gcp_project_id}.{settings.bq_dataset_sales}.Product"],
        "광고": ["skin1004-319714.marketing_analysis.integrated_ad"],
        "마케팅": ["skin1004-319714.marketing_analysis.Integrated_marketing_cost"],
        "Shopify": ["skin1004-319714.marketing_analysis.shopify_analysis_sales"],
        "플랫폼": ["skin1004-319714.Platform_Data.raw_data"],
        "인플루언서": ["skin1004-319714.marketing_analysis.influencer_input_ALL_TEAMS"],
        "아마존검색": ["skin1004-319714.marketing_analysis.amazon_search_analytics_catalog_performance"],
        "프로모션": ["skin1004-319714.promotion_calendar.promotion"],
        # ⛔ 리뷰는 국내/해외/매장 **통합 3종**이 정본이다 (2026-08-18 확정).
        #    구 몰별 소스(아마존·큐텐·쇼피·스마트스토어)를 남겨 두면 화이트리스트에
        #    없는 테이블을 가리켜 "허용되지 않은 테이블" 로 막힌다 — @@ 로만 쓰면
        #    발견이 늦다. 별칭으로 옛 이름을 흡수한다.
        "국내몰 리뷰": ["skin1004-319714.Review_Data.Korea_mall_Review"],
        "해외몰 리뷰": ["skin1004-319714.Review_Data.Oversea_mall_Review"],
        "매장 리뷰": ["skin1004-319714.Review_Data.Store_Review"],
        "메타광고": ["skin1004-319714.ad_data.meta data_test"],
        "손익": ["skin1004-319714.Sales_Integration.FI_LLM_Flat"],
    }


def _allowed_tables_from_sources(
    enabled_sources,
    can_view_fi: bool = False,
) -> Optional[set]:
    """Map enabled_sources labels to allowed BigQuery table paths.

    Returns None when no filtering applies (all tables allowed).
    """
    settings = get_settings()
    _SOURCE_TABLE_MAP = _source_table_map(settings)
    if enabled_sources is None:
        if can_view_fi:
            return None
        allowed_tables = set(settings.allowed_tables)
    else:
        allowed_tables = set()
        for src in enabled_sources:
            for tp in _SOURCE_TABLE_MAP.get(src, []):
                allowed_tables.add(tp)
    if not can_view_fi:
        allowed_tables.discard("skin1004-319714.Sales_Integration.FI_LLM_Flat")
    return allowed_tables


def _source_scope_message(error_msg: str, enabled_sources) -> Optional[str]:
    """데이터소스(@@) 선택 때문에 막힌 것이면, 어떤 소스를 켜야 하는지 알려준다.

    이 안내가 없으면 "데이터를 조회하지 못했습니다" 로 흘러가 사용자가 시스템
    오류로 오해한다. 실제로는 선택 범위 밖을 물었을 뿐이고, 소스만 바꾸면 된다.
    """
    if not enabled_sources:
        return None  # 소스를 안 골랐으면 범위 문제가 아니다
    m = re.search(r"허용되지 않은 테이블입니다:\s*(\S+)", error_msg or "")
    if not m:
        return None
    table = m.group(1).strip()

    # 테이블 → 소스 이름 역매핑 (같은 표를 두 곳에서 쓰지 않도록 재사용)
    settings = get_settings()
    reverse = {}
    for src, paths in _source_table_map(settings).items():
        for tp in paths:
            reverse[tp] = src
    needed = reverse.get(table)

    picked = ", ".join(f"@@{s}" for s in enabled_sources)
    head = f"{SOURCE_SCOPE_DENIED_PREFIX}({picked})로는 이 질문에 답할 수 없습니다."
    gap = chr(10) * 2
    if needed:
        return (
            head + gap
            + f"이 데이터는 **@@{needed}** 에 있습니다. 입력창에 `@@{needed}` 를 "
            + "추가로 선택하시면 바로 조회해 드리겠습니다."
        )
    return (
        head + gap
        + "필요한 데이터가 선택 범위 밖에 있습니다. 데이터소스 선택을 해제하거나 "
        + "다른 소스를 골라 다시 질문해 주세요."
    )


def _build_schema_context(query: str, allowed_tables: Optional[set],
                          conv_context: str = "") -> str:
    """Assemble the lazy-loaded schema context block for a query.

    conv_context 도 키워드 매칭에 포함해야 한다 — "광고비 얼마야?" 다음에
    "6월과 비교해줘"가 오면 현재 질문엔 '광고' 단어가 없어 광고 테이블 스키마가
    빠지고, LLM 은 보이는 매출 스키마로 SQL 을 써서 주제가 매출로 넘어간다
    (2026-08-10 실사용 제보). 맥락 꼬리(최근 3000자)만 매칭에 쓴다 —
    직전 AI 답변이 1500자로 절단되므로 그보다 넓어야 마지막 답변 전체
    (선두의 '[실행된 쿼리 테이블: ...]' 태그 포함)가 윈도에 들어온다.
    """
    _match_text = (query + " " + (conv_context or "")[-3000:]).lower()
    # 맥락에 등장한 테이블(직전 실행 SQL·태그)은 키워드 매칭과 무관하게 스키마를
    # 결정적으로 포함한다 — 후속 질문에 주제 단어가 없어도 직전 테이블 스키마가
    # 반드시 실리도록 (2026-08-10 아키텍처 변경). allowed_tables 화이트리스트는
    # 그대로 존중한다 (FI 방어선 유지).
    _ctx_tables = set(re.findall(
        r"skin1004-319714\.[A-Za-z_]\w*\.[A-Za-z_]\w*", conv_context or ""))
    global _schema_cache_sales, _schema_cache_tables
    bq = get_bigquery_client()
    settings = get_settings()

    # 1) Determine which schemas to include
    include_sales = (allowed_tables is None) or (settings.sales_table_full_path in allowed_tables)

    # 1b) Determine Product inclusion
    product_path = f"{settings.gcp_project_id}.{settings.bq_dataset_sales}.Product"
    include_product = (allowed_tables is None and (product_path in _ctx_tables or any(kw in _match_text for kw in ["제품", "product", "sku", "카테고리"]))) or \
                      (allowed_tables is not None and product_path in allowed_tables)

    # 2) Lazy-load: only include marketing tables whose keywords match AND are allowed
    query_lower = _match_text
    # @@ 로 소스를 좁힌 경우(허용 테이블 소수)는 키워드 매칭과 무관하게 그 테이블
    # 스키마를 반드시 싣는다 — 안 실으면 LLM 이 없는 컬럼("media")을 지어내거나
    # 다른 테이블로 이탈해 소스 안내로 튕긴다 (2026-08-06 Playwright 전수 테스트:
    # @@메타광고 '플랫폼별 분포' / @@아마존검색 키워드 질문 6건). 일반 라우팅
    # (allowed 가 None 이거나 대형 세트)에서는 기존 키워드 lazy-load 를 유지한다.
    force_all_allowed = allowed_tables is not None and len(allowed_tables) <= 5
    matched_entries = [
        (t[0], t[1], t[2]) for t in MARKETING_TABLES
        if (force_all_allowed and t[0] in allowed_tables)
        or (not force_all_allowed
            and (t[0] in _ctx_tables or any(kw in query_lower for kw in t[2]))
            and (allowed_tables is None or t[0] in allowed_tables))
    ]

    # ── Parallel schema fetch: sales + product + marketing in one pool ──
    # Build list of uncached fetch jobs. Each job is a tuple:
    #   (kind, table_path, label)
    # kind ∈ {"sales", "product", "marketing"} — used to route the result
    # back into the correct cache slot after fetching.
    fetch_jobs = []
    if include_sales and not _schema_cache_sales:
        fetch_jobs.append(("sales", settings.sales_table_full_path, None))
    if include_product and product_path not in _schema_cache_tables:
        fetch_jobs.append(("product", product_path, "제품 마스터"))
    for tp, lb, _ in matched_entries:
        if tp not in _schema_cache_tables:
            fetch_jobs.append(("marketing", tp, lb))

    if fetch_jobs:
        def _fetch_schema(kind, table_path, label):
            try:
                tbl_schema = bq.get_table_schema(table_path)
                tbl_lines = [
                    f"  - {col['name']} ({col['type']}): {col['description']}"
                    for col in tbl_schema
                ]
                table_short = table_path.rsplit(".", 1)[-1]
                if kind == "sales":
                    text = f"\n\n### 실제 테이블 스키마 ({table_short})\n" + "\n".join(tbl_lines)
                elif kind == "product":
                    text = f"\n\n### 제품 마스터 (Product)\n" + "\n".join(tbl_lines)
                else:  # marketing
                    text = f"\n\n### {label} ({table_short})\n" + "\n".join(tbl_lines)
                    # 국가 리터럴 규칙은 SALES_ALL 섹션에만 있어 lazy-load 테이블엔
                    # 적용되지 않았다 — 'INDONESIA' 영문 필터로 0건 (2026-08-10 UI 실측)
                    if any(col["name"] == "Country" for col in tbl_schema):
                        text += ("\n  ⚠️ Country 값은 한국어 국가명('인도네시아', '미국', "
                                 "'독일' 등) — 영어 국가명('Indonesia' 등)으로 필터하면 0건이 난다")
                return kind, table_path, text
            except Exception as e:
                # Preserve original per-table warning labels
                warn_label = (
                    "SALES_ALL_Backup" if kind == "sales"
                    else "Product" if kind == "product"
                    else table_path
                )
                logger.warning("schema_fetch_failed", table=warn_label, error=str(e))
                return kind, table_path, ""

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(fetch_jobs), 5)) as pool:
            futures = [pool.submit(_fetch_schema, k, tp, lb) for k, tp, lb in fetch_jobs]
            for f in concurrent.futures.as_completed(futures):
                kind, tp, schema_text = f.result()
                if kind == "sales":
                    # Only update cache on success; on failure leave empty so next call retries
                    if schema_text:
                        _schema_cache_sales = schema_text
                elif kind == "product":
                    # Original behavior: don't cache failures for Product (retry next call)
                    if schema_text:
                        _schema_cache_tables[tp] = schema_text
                else:  # marketing — original cached empty string on failure
                    _schema_cache_tables[tp] = schema_text

    # ── Assemble schema_context in stable order: sales → product → marketing ──
    schema_context = _schema_cache_sales if include_sales else ""
    if include_product:
        schema_context += _schema_cache_tables.get(product_path, "")
    for table_path, _, _ in matched_entries:
        schema_context += _schema_cache_tables.get(table_path, "")

    logger.info("schema_context_built", total_tables=1 + len(matched_entries), query_matched=len(matched_entries))
    return schema_context


def _build_date_context() -> str:
    """Today/this-month/last-month prompt block for date disambiguation."""
    today = datetime.now().strftime("%Y-%m-%d")
    this_year = datetime.now().year
    this_month = datetime.now().month
    last_month = this_month - 1 if this_month > 1 else 12
    last_month_year = this_year if this_month > 1 else this_year - 1
    return (
        f"\n\n## ⚠️ 오늘 날짜 (최우선 적용)\n"
        f"오늘: {today}\n"
        f"- **이번 달** = {this_year}년 {this_month}월 → `EXTRACT(YEAR FROM Date) = {this_year} AND EXTRACT(MONTH FROM Date) = {this_month}`\n"
        f"- **지난 달** = {last_month_year}년 {last_month}월 → `EXTRACT(YEAR FROM Date) = {last_month_year} AND EXTRACT(MONTH FROM Date) = {last_month}`\n"
        f"- **올해** = {this_year}년 → `EXTRACT(YEAR FROM Date) = {this_year}`\n"
        f"- ⛔ '이번 달'이라고 하면 반드시 {this_month}월! 다른 월로 바꾸지 마세요. 데이터가 적어도 {this_month}월이 맞습니다."
    )


_PERIOD_RANK_TIME_TERMS = ("월별", "주별", "주차별", "일별", "분기별", "연도별")
_PERIOD_RANK_DIM_TERMS = ("업체별", "거래처별", "바이어별", "제품별", "sku별", "채널별")
_PERIOD_RANK_TOP_TERMS = (
    "상위", "주요", "핵심", "비중있는", "비중 있는", "비중이 큰", "top", "랭킹",
)
_HISTORICAL_METRIC_TERMS = (
    "매출", "판매", "수량", "주문", "실적", "비용", "광고비", "roas", "revenue",
)
_EXPLICIT_FUTURE_TERMS = (
    "예정", "계획", "전망", "예측", "예상", "미래", "향후", "프로모션 일정",
)


def _requires_partitioned_period_ranking(query: str, conversation_context: str = "") -> bool:
    """기간별 고카디널리티 TOP 분석이 전역 LIMIT으로 잘릴 위험이 있는가."""
    text = f"{conversation_context}\n{query}".lower()
    return (
        any(term in text for term in _PERIOD_RANK_TIME_TERMS)
        and any(term in text for term in _PERIOD_RANK_DIM_TERMS)
        and any(term in text for term in _PERIOD_RANK_TOP_TERMS)
    )


def _has_partitioned_period_ranking(sql: str) -> bool:
    """기간별 순위 창을 계산하고 TOP N 필터까지 실제 적용했는가."""
    sql = sql or ""
    window_aliases = re.findall(
        r"\b(?:ROW_NUMBER|RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\("
        r"[\s\S]*?\bPARTITION\s+BY\b[\s\S]*?\)\s+AS\s+`?([A-Za-z_]\w*)`?",
        sql,
        re.IGNORECASE,
    )
    rank_limit = r"(?:<=|<|=)\s*\d+|BETWEEN\s+1\s+AND\s+\d+"
    for alias in window_aliases:
        if re.search(
            rf"\b(?:WHERE|AND|QUALIFY)\b[\s\S]*?\b{re.escape(alias)}\b\s*(?:{rank_limit})",
            sql,
            re.IGNORECASE,
        ):
            return True

    # BigQuery QUALIFY는 별칭 없이 창 함수를 직접 필터링할 수도 있다.
    return bool(re.search(
        rf"\bQUALIFY\b[\s\S]*?\b(?:ROW_NUMBER|RANK|DENSE_RANK)\s*\(\s*\)"
        rf"\s*OVER\s*\([\s\S]*?\bPARTITION\s+BY\b[\s\S]*?\)\s*(?:{rank_limit})",
        sql,
        re.IGNORECASE,
    ))


def _requires_current_date_cap(query: str, conversation_context: str = "") -> bool:
    """과거 실적의 시작점만 지정한 질문은 오늘 이후 행을 포함하지 않는다."""
    text = f"{conversation_context}\n{query}".lower()
    has_history_start = bool(
        re.search(r"20\d{2}\s*년?\s*(?:부터|이후)", text)
        or re.search(r"date\s*>=\s*['\"]20\d{2}", text, re.IGNORECASE)
    )
    return (
        has_history_start
        and any(term in text for term in _HISTORICAL_METRIC_TERMS)
        and not any(term in text for term in _EXPLICIT_FUTURE_TERMS)
    )


def _has_current_date_cap(sql: str) -> bool:
    """Date에 오늘 이하 상한 또는 명시적 종료일이 있는가."""
    sql = sql or ""
    explicit_cap = re.search(
        r"\b(?:\w+\.)?Date\s*(?:<=|<)\s*(?:CURRENT_(?:DATE|DATETIME)\s*\(\s*\)|"
        r"DATE\s*\(\s*CURRENT_DATETIME\s*\(\s*\)\s*\)|['\"]20\d{2}-\d{2}-\d{2}['\"])",
        sql,
        re.IGNORECASE,
    )
    between_cap = re.search(
        r"\b(?:\w+\.)?Date\s+BETWEEN\s+[^\n]+\s+AND\s+"
        r"(?:CURRENT_(?:DATE|DATETIME)\s*\(\s*\)|['\"]20\d{2}-\d{2}-\d{2}['\"])",
        sql,
        re.IGNORECASE,
    )
    return bool(explicit_cap or between_cap)


# --- LangGraph Nodes ---


def generate_sql(state: AgentState) -> Dict[str, Any]:
    """Generate SQL from natural language query.

    Args:
        state: Current agent state with user query.

    Returns:
        Updated state with generated_sql.
    """
    query = state["query"]
    brand_filter = state.get("brand_filter")
    enabled_sources = state.get("enabled_sources")
    can_view_fi = bool(state.get("can_view_fi", False))
    logger.info("generating_sql", query=query, enabled_sources=enabled_sources)

    # Use Flash for SQL generation (Pro is too slow due to thinking mode)
    llm = get_flash_client()
    system_prompt = _load_prompt("sql_generator.txt", can_view_fi=can_view_fi)

    # Get table schemas (lazy: only include tables relevant to the query)
    allowed_tables = _allowed_tables_from_sources(enabled_sources, can_view_fi)
    if allowed_tables is not None:
        logger.info("sql_table_filter", allowed_count=len(allowed_tables), sources=enabled_sources)

    # ── SQL Cache: skip LLM if cached SQL uses only allowed tables ──
    conv_context = state.get("conversation_context", "")
    if not conv_context:  # Only cache standalone questions (not follow-ups)
        cache_key = _cache_key(query, brand_filter)
        cached_sql = _cache_lookup(cache_key, allowed_tables)
        if cached_sql:
            logger.info("sql_cache_hit", query=query[:60], cache_key=cache_key)
            return {"generated_sql": cached_sql, "error": None, "_sql_from_cache": True}

    schema_context = _build_schema_context(query, allowed_tables, conv_context)

    this_year = datetime.now().year
    this_month = datetime.now().month
    last_month = this_month - 1 if this_month > 1 else 12
    last_month_year = this_year if this_month > 1 else this_year - 1
    date_context = _build_date_context()

    # Include conversation context if available
    conv_context = state.get("conversation_context", "")
    conv_section = ""
    if conv_context:
        conv_section = f"\n\n## 이전 대화 맥락\n{conv_context}\n\n위 대화 맥락을 참고하여 사용자의 현재 질문에 포함된 '그거', '아까', '다시', '2월은?', '시각화해줘', '차트로 보여줘' 같은 참조를 이해하세요.\n⚠️ 현재 질문이 'B2B', '올해만', '월별로' 같은 짧은 단어/구라면 이것은 새 질문이 아니라 **직전 대화에 대한 답이나 조건 추가**다. 직전 AI 답변이 조건을 되물었다면(예: 'B2B/B2C 구분이 필요하시면 알려주세요'), 직전 사용자 질문에 이 조건을 결합한 하나의 요청으로 해석해 SQL을 생성하라. 예: 직전 질문 '국가별 첫 거래일자 확인' + 현재 답 'B2B' → 해당 국가들의 B2B 기준 MIN(Date) 조회. 직전 질문의 의도를 버리고 현재 단어만으로 일반 현황 조회를 만들면 안 된다.\n⚠️ **주제(지표·테이블) 유지**: 직전 AI 답변에 '[실행된 쿼리 테이블: ...]' 표시가 있으면 '지난달이랑 비교해줘', '국가별로 나눠줘', '작년 같은 기간은?' 같은 후속 질문은 **그 테이블·그 지표 기준**으로 SQL을 생성하라. 직전 주제가 광고비·인플루언서·리뷰·판매수량·쇼피파이·손익이었는데 후속 질문에 주제 단어가 없다고 매출(SALES_ALL_Backup)로 갈아타지 마라 — 사용자가 '매출은?', '그럼 판매액은?'처럼 명시적으로 주제를 바꿀 때만 테이블을 바꾼다.\n⚠️ '시각화해줘', '차트로 그려줘' 같은 후속 요청이 오면, 이전 답변에서 사용된 동일한 데이터 범위/조건/집계 수준으로 SQL을 생성하세요. 이전에 분기별 비교였다면 분기별로, 월별이었다면 월별로 유지하세요.\n⚠️ 이전 답변에서 특정 판매처(Company_Name), 국가(Country), 채널(Mall_Classification)이 나열된 상태에서 사용자가 '판매처별', '국가별', '채널별' 후속 질문을 하면, 이전 답변에 등장한 그 항목들을 WHERE 조건으로 포함하세요. 예: 이전에 예스아시아닷컴코리아·Stylevana가 나왔으면 다음 SQL에도 Company_Name IN ('예스아시아닷컴코리아', 'Stylevana', ...)를 추가."
        conv_section += (
            "\n⚠️ **정정/항의형 후속 질문**: '내가 ~라고 했지', '언급도 안 했어', "
            "'그거 말고', '다시 뽑아줘'는 새 독립 질문이 아니다. 직전 사용자 요청과 "
            "직전 실행 SQL을 기준으로 잘못 추가된 SELECT·CASE·WHERE 조건을 제거하거나 "
            "요청한 기간/축을 복구해 SQL을 다시 생성하라. 사용자가 언급하지 않았다고 "
            "지적한 제품·라인을 새 필터로 해석하지 마라."
        )

    # Brand filter injection: only if user has a group filter assigned
    brand_filter = state.get("brand_filter")
    brand_section = _build_brand_section(brand_filter)
    # No brand_filter (admin/unassigned) → SQL 프롬프트의 기본 규칙 따름

    sql_only_reminder = "\n\n⛔ 최종 지시: SELECT로 시작하는 BigQuery SQL만 출력하라. 설명/안내/되묻기 텍스트 출력 시 시스템 오류 발생. 질문이 모호하면 **먼저 이전 대화 맥락으로 의도를 해소**하고, 맥락으로도 해소되지 않을 때만 합리적 기본값(최근 3개월, TOP 10 등)으로 SQL 생성.\n⚠️ 국가 필터: 모든 테이블의 Country 값은 **한국어 국가명**이다 — WHERE Country='인도네시아' (O), Country='Indonesia'/'INDONESIA' (X, 0건이 난다)."
    _period_rank_required = _requires_partitioned_period_ranking(query, conv_context)
    if _period_rank_required:
        sql_only_reminder += (
            "\n⛔ 이 질문은 기간별 고카디널리티 TOP 분석이다. 반드시 "
            "1단계 CTE에서 기간축·항목축별 SUM을 먼저 끝내고, 2단계 CTE에서 그 집계 결과의 "
            "기간 별칭을 PARTITION BY에 사용해 ROW_NUMBER()/RANK()/DENSE_RANK()를 계산하라. "
            "GROUP BY와 같은 SELECT에서 원본 Date를 윈도 함수에 다시 참조하면 BigQuery 오류가 난다. "
            "그 뒤 바깥 WHERE rank <= N 또는 QUALIFY ... <= N으로 "
            "각 기간의 TOP N을 실제 필터링하라. 순위 컬럼만 만들고 필터하지 않는 것도 금지한다. "
            "기간 오름차순 + 전역 LIMIT만 둔 SQL은 앞쪽 기간만 남기므로 금지한다."
        )
    _current_date_cap_required = _requires_current_date_cap(query, conv_context)
    if _current_date_cap_required:
        sql_only_reminder += (
            "\n⛔ 이 질문은 과거 실적의 시작일만 지정했다. 미래 데이터·예측을 명시적으로 "
            "요청하지 않았으므로 모든 원본 매출/판매 테이블 스캔에 "
            "`Date <= CURRENT_DATETIME()` 상한을 넣어 오늘 이후 행을 제외하라."
        )
    # 직전 실행 테이블 앵커 — conv_section 중간의 일반 지시만으론 LLM이 후속
    # 질문에서 매출로 회귀한다(2026-08-10 판매수량·쇼피파이 시나리오 실측).
    # 프롬프트 맨 끝, 최종 지시 바로 옆에 마지막 실행 테이블을 명시해 고정한다.
    # ⚠️ @@ 로 소스를 좁힌 경우(allowed_tables 존재) 화이트리스트 밖 테이블은
    # 앵커에서 제외한다 — 안 그러면 "직전 테이블을 유지하라"는 앵커와 "허용
    # 테이블만 사용하라"는 스코프 지시가 충돌해 검증 실패 SQL 을 유도한다.
    _prev_tbl_tags = re.findall(r"\[실행된 쿼리 테이블: ([^\]]+)\]", conv_context)
    _prev_tbls = []
    if _prev_tbl_tags:
        _prev_tbls = [t.strip() for t in _prev_tbl_tags[-1].split(",")]
        if allowed_tables is not None:
            _prev_tbls = [t for t in _prev_tbls if t in allowed_tables]
    if _prev_tbls:
        sql_only_reminder += (
            f"\n⚠️ 직전 답변의 실행 테이블: {', '.join(_prev_tbls)} — 현재 질문이 "
            "'~별로 나눠줘', '비교해줘', '작년 같은 기간은?' 같은 후속이면 **이 테이블과 "
            "그 지표(광고비/판매수량/리뷰/손익 등)** 기준으로 SQL을 생성하라. 기간만 바꾸고 "
            "테이블·지표는 유지한다. 직전 질문에 특정 제품·브랜드·국가 필터가 있었으면 "
            "(예: '센텔라 100 앰플 판매수량' → '국가별로 나눠줘') 그 필터도 그대로 유지한다. "
            "사용자가 '매출은?'처럼 명시적으로 지표를 바꿔 물을 때만 다른 테이블로 전환한다."
        )
    # Inject current month into ambiguous date references in the query itself
    _month_keywords = {"이번 달": f"{this_year}년 {this_month}월(이번 달)", "이번달": f"{this_year}년 {this_month}월(이번달)", "지난 달": f"{last_month_year}년 {last_month}월(지난 달)", "지난달": f"{last_month_year}년 {last_month}월(지난달)"}
    _resolved_query = query
    for _mk, _mv in _month_keywords.items():
        if _mk in _resolved_query:
            _resolved_query = _resolved_query.replace(_mk, _mv)

    # 학습된 스킬: 과거 👍 SQL 중 현재 질문과 단어가 겹치는 예시를 few-shot으로 주입.
    # "톤브 앰플 백" 같은 축약/오타 표현이 반복돼도, 한 번 올바르게 풀린 SET LIKE
    # 패턴이 있으면 프롬프트에 정적 매핑표를 매번 손으로 안 늘려도 재사용된다.
    skill_section = ""
    try:
        from app.agents.skill_memory import load_skill_context
        skill_section = load_skill_context("bigquery", query)
        if skill_section:
            skill_section = f"\n\n{skill_section}"
    except Exception:
        pass

    # 사용자가 @@ 로 소스를 지정한 경우: 허용 테이블을 명시적으로 강제한다.
    # 스키마만 실어주면 LLM 이 "광고 플랫폼별 분포" 같은 일반적 표현에서
    # 프롬프트 본문에 나오는 다른 테이블(통합 광고 등)로 이탈해 실행이 거부된다
    # (2026-08-06 Playwright 전수 테스트: @@메타광고 1건·@@아마존검색 4건).
    table_scope_section = ""
    if allowed_tables is not None and 0 < len(allowed_tables) <= 5:
        _tl = "\n".join(f"- `{t}`" for t in sorted(allowed_tables))
        table_scope_section = (
            "\n\n## ⛔ 사용 가능 테이블 (사용자가 데이터소스를 직접 지정함)\n"
            f"{_tl}\n"
            "위 테이블만 사용하라 — 목록 밖 테이블을 참조하면 실행이 거부된다. "
            "질문 표현이 이 테이블의 컬럼과 정확히 일치하지 않으면 가장 가까운 컬럼으로 "
            "재해석해서 SQL 을 생성하라 (예: '키워드 검색 순위' → 이 테이블에 키워드 컬럼이 "
            "없고 ASIN 단위라면 제품(ASIN_Title)별 노출수/클릭수 순위로, '플랫폼별 분포' → "
            "publisher_platform 같은 실제 존재하는 컬럼으로). "
            "단, 개념 자체가 테이블에 없어 대체가 부정확해지는 경우(예: 고객 식별자가 없는데 "
            "'고객 수')는 다른 값을 그 개념인 척 단정하지 말고, 컬럼 별칭에 실제 기준을 "
            "드러내라 — 예: COUNT(DISTINCT Order_name) AS unique_order_count (고객 수 아님, "
            "주문 수). 답변 단계가 이 별칭을 보고 한계를 설명할 수 있어야 한다."
        )

    full_prompt = f"{system_prompt}{schema_context}{table_scope_section}{conv_section}{brand_section}{skill_section}\n\n{date_context}\n\n## 사용자 질문\n{_resolved_query}{sql_only_reminder}"

    try:
        sql = llm.generate(full_prompt, temperature=0.0, max_output_tokens=10000)
        sql = sanitize_sql(sql)

        # Retry once if LLM returned text/truncated SQL instead of valid SQL
        if not sql or len(sql) < 10:
            logger.warning("sql_generation_empty_retry", query=query[:80])
            retry_prompt = (
                full_prompt
                + "\n\n⛔ 이전 시도에서 SQL이 잘리거나 유효하지 않았습니다. "
                "⚠️ 질문에 여러 항목(매출+마케팅비용 등)이 포함되면, **가장 핵심 항목(매출)만 SQL로 생성**하세요. "
                "나머지는 답변에서 '별도 질문 필요'로 안내. "
                "UNION ALL 사용 금지! CASE WHEN 패턴만 사용! "
                "반드시 괄호가 모두 닫힌 완전한 SQL을 출력하세요."
            )
            sql = llm.generate(retry_prompt, temperature=0.1, max_output_tokens=10000)
            sql = sanitize_sql(sql)
            if sql:
                logger.info("sql_generation_retry_success", sql=sql[:200])

        # 월별 업체 TOP처럼 행이 많은 교차분석은 전역 LIMIT이 앞쪽 기간만 남겨도
        # 문법·보안 검증을 모두 통과한다. 프롬프트만으로는 정정 후 재생성에서 다시
        # 퇴행한 실측이 있어, 실행 전에 구조를 검사하고 한 번 더 생성한다.
        if sql and _period_rank_required and not _has_partitioned_period_ranking(sql):
            logger.warning("sql_period_rank_missing_retry", sql=sql[:300])
            rank_retry_prompt = (
                full_prompt
                + f"\n\n⛔ 이전 SQL은 기간별 순위 창 없이 전역 LIMIT을 사용해 요청 기간을 "
                  f"잘라낼 수 있어 거부됐다:\n```sql\n{sql}\n```\n"
                  "반드시 ROW_NUMBER()/RANK()/DENSE_RANK() OVER "
                  "(PARTITION BY 기간축 ORDER BY 지표 DESC)로 순위를 만든 뒤, 바깥 WHERE "
                  "rank <= N 또는 QUALIFY ... <= N으로 각 기간 TOP N을 실제 필터링한 "
                  "완전한 SQL만 다시 출력하라. 순위 컬럼만 만들고 필터하지 마라."
            )
            rank_retry_sql = sanitize_sql(
                llm.generate(rank_retry_prompt, temperature=0.0, max_output_tokens=10000)
            )
            if rank_retry_sql and _has_partitioned_period_ranking(rank_retry_sql):
                sql = rank_retry_sql
                logger.info("sql_period_rank_retry_success", sql=sql[:300])
            else:
                logger.error("sql_period_rank_retry_failed", sql=(rank_retry_sql or "")[:300])
                return {
                    "generated_sql": None,
                    "error": (
                        "요청 기간이 잘릴 수 있는 SQL을 안전하게 교정하지 못했습니다. "
                        "기간별 상위 개수를 명시해 다시 질문해 주세요."
                    ),
                }

        if sql and _current_date_cap_required and not _has_current_date_cap(sql):
            logger.warning("sql_current_date_cap_missing_retry", sql=sql[:300])
            cap_retry_prompt = (
                full_prompt
                + f"\n\n⛔ 이전 SQL은 과거 실적 질문인데 오늘 이후 미래 행을 막는 Date "
                  f"상한이 없어 거부됐다:\n```sql\n{sql}\n```\n"
                  "모든 원본 매출/판매 테이블 스캔에 Date <= CURRENT_DATETIME()을 넣어 "
                  "오늘 이후 행을 제외한 완전한 SQL만 다시 출력하라."
            )
            cap_retry_sql = sanitize_sql(
                llm.generate(cap_retry_prompt, temperature=0.0, max_output_tokens=10000)
            )
            cap_retry_ok = bool(cap_retry_sql and _has_current_date_cap(cap_retry_sql))
            if _period_rank_required:
                cap_retry_ok = cap_retry_ok and _has_partitioned_period_ranking(cap_retry_sql)
            if cap_retry_ok:
                sql = cap_retry_sql
                logger.info("sql_current_date_cap_retry_success", sql=sql[:300])
            else:
                logger.error("sql_current_date_cap_retry_failed", sql=(cap_retry_sql or "")[:300])
                return {
                    "generated_sql": None,
                    "error": (
                        "미래 데이터가 섞일 수 있는 SQL을 안전하게 교정하지 못했습니다. "
                        "종료일을 명시해 다시 질문해 주세요."
                    ),
                }

        # Fix English media names → Korean (influencer table uses Korean values)
        if sql and "influencer_input_ALL_TEAMS" in sql:
            sql = sql.replace("%Instagram%", "%인스타그램%")
            sql = sql.replace("%TikTok%", "%틱톡%")
            sql = sql.replace("%YouTube%", "%유튜브%")
            sql = sql.replace("%Facebook%", "%페이스북%")
            sql = sql.replace('"Instagram"', '"인스타그램"')
            sql = sql.replace('"TikTok"', '"틱톡"')
            sql = sql.replace('"YouTube"', '"유튜브"')
            sql = sql.replace('"Facebook"', '"페이스북"')
            sql = sql.replace("'Instagram'", "'인스타그램'")
            sql = sql.replace("'TikTok'", "'틱톡'")
            sql = sql.replace("'YouTube'", "'유튜브'")
            sql = sql.replace("'Facebook'", "'페이스북'")

        # Country 영어 리터럴 → 한국어 (전 테이블 공통 규칙)
        if sql:
            sql = _localize_country_literals(sql)
            sql = _localize_team_literals(sql)
            sql = _localize_promotion_literals(sql)
            sql = _strip_unrequested_brand_filter(sql, query)

        logger.info("sql_generated", sql=sql[:200])

        # Cache store happens in validate_sql_node, after validate_sql() passes
        # (Fix D) — avoids persisting SQL that turns out to be broken/unparsable.
        return {"generated_sql": sql, "error": None}
    except Exception as e:
        logger.error("sql_generation_failed", error=str(e))
        return {"generated_sql": None, "error": f"SQL 생성 실패: {str(e)}"}


def validate_sql_node(state: AgentState) -> Dict[str, Any]:
    """Validate generated SQL for safety.

    Args:
        state: Current agent state with generated_sql.

    Returns:
        Updated state with sql_valid flag.
    """
    sql = state.get("generated_sql")
    if not sql:
        return {"sql_valid": False, "error": "SQL이 생성되지 않았습니다."}

    allowed_tables = _allowed_tables_from_sources(
        state.get("enabled_sources"),
        bool(state.get("can_view_fi", False)),
    )
    is_valid, error_msg = validate_sql(sql, allowed_tables=allowed_tables)

    if not is_valid:
        logger.warning("sql_validation_failed", error=error_msg, sql=sql[:200])
        if error_msg == FI_ACCESS_DENIED_MESSAGE:
            return {"sql_valid": False, "error": FI_ACCESS_DENIED_MESSAGE}
        _scope = _source_scope_message(error_msg, state.get("enabled_sources"))
        if _scope:
            return {"sql_valid": False, "error": _scope}
        return {"sql_valid": False, "error": f"SQL 검증 실패: {error_msg}"}

    logger.info("sql_validation_passed", sql=sql[:200])

    # Cache only SQL that has passed validation (Fix D — avoid caching broken
    # SQL). Skip when it was already served from cache (already stored) or
    # this is a conversational follow-up (same condition as generate_sql used).
    if not state.get("_sql_from_cache") and not state.get("conversation_context"):
        _query = state.get("query", "")
        _brand_filter = state.get("brand_filter")
        _cache_store(_cache_key(_query, _brand_filter), _query, sql, _brand_filter)

    return {"sql_valid": True, "error": None}


def _retry_with_stronger_model(
    query: str,
    failed_sql: str,
    bq,
    brand_filter: Optional[str] = None,
    can_view_fi: bool = False,
    allowed_tables: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """Escalate a zero-result product-name query from Flash to Claude Opus.

    Flash is used for SQL gen purely for speed; it occasionally misreads an
    abbreviated/colloquial product name (e.g. "톤브 앰플 백") and produces a SET
    LIKE pattern that matches nothing real. Rather than slow down every query
    with a smarter model, only pay that cost when Flash's guess demonstrably
    failed (0 rows back). Returns None if the escalation itself fails or also
    returns nothing new — caller falls back to the original (empty) result.
    """
    try:
        llm = get_llm_client(MODEL_CLAUDE)
        # sql_generator.txt (~70KB, fully static) goes in system_instruction so
        # ClaudeClient._wrap_system can mark it as a cache breakpoint — see
        # app/core/llm.py. Only the retry-specific content is the user turn.
        retry_prompt = (
            f"## 사용자 질문\n{query}"
            + f"\n\n⚠️ 이전 시도에서 아래 SQL을 생성했으나 조회 결과가 0건이었습니다:\n```sql\n{failed_sql}\n```"
            + "\n사용자가 언급한 제품명이 축약어·오타·구어체 표현일 수 있습니다 "
              "(예: '톤브 앰플 백' = '톤 브라이트닝 앰플 100ml'). "
              "위 제품 SKU 목록을 참고해 실제로 존재하는 제품과 다시 매칭한 SQL을 생성하세요. "
              "여전히 확신이 없으면 라인명까지만 필터링한 더 넓은 LIKE 패턴을 사용하세요."
            + _build_brand_section(brand_filter)
        )
        retry_sql = llm.generate(
            retry_prompt,
            system_instruction=_load_prompt("sql_generator.txt", can_view_fi=can_view_fi),
            temperature=0.0, max_output_tokens=10000,
        )
        retry_sql = sanitize_sql(retry_sql)
        retry_sql = _localize_country_literals(retry_sql)
        retry_sql = _localize_team_literals(retry_sql)
        retry_sql = _localize_promotion_literals(retry_sql)
        retry_sql = _strip_unrequested_brand_filter(retry_sql, query)
        if not retry_sql:
            return None

        if allowed_tables is None:
            allowed_tables = _allowed_tables_from_sources(None, can_view_fi)
        is_valid, validation_error = validate_sql(
            retry_sql,
            allowed_tables=allowed_tables,
        )
        if not is_valid:
            logger.warning("sql_product_escalation_invalid", error=validation_error, sql=retry_sql[:200])
            return None

        results = bq.execute_query(retry_sql, timeout=300.0, max_rows=1000)
        logger.info("sql_product_escalation", query=query[:80], row_count=len(results), sql=retry_sql[:200])
        return {"sql_result": results, "error": None, "generated_sql": retry_sql}
    except Exception as e:
        logger.warning("sql_product_escalation_failed", error=str(e)[:200])
        return None


def execute_sql(state: AgentState) -> Dict[str, Any]:
    """Execute validated SQL against BigQuery.

    Args:
        state: Current agent state with validated SQL.

    Returns:
        Updated state with sql_result.
    """
    sql = state.get("generated_sql")
    if not sql or not state.get("sql_valid"):
        return {"sql_result": None, "error": "실행할 수 없는 SQL입니다."}

    # ⛔ **실행 전에 참조 턴과 말이 맞는지 본다.** 후속 질문이 가리킨 턴의 필터가
    #    새 SQL 에서 말없이 사라지면 다른 것을 세게 된다 — 같은 대화에서 매출이
    #    300억 달라진 사고가 그것이다 (붐따 #116).
    # ⚠️ 지금은 **계측만** 한다. 막거나 고치지 않는다 — 오탐이 조회를 끊으면
    #    답변 수치 검증(answer_check)을 계측으로 시작한 이유와 같은 문제가 생긴다.
    try:
        from app.core.turn_state import (extract_states, resolve_reference,
                                         verify_alignment)
        _msgs = state.get("messages") or []
        _q = state.get("query") or ""
        if _msgs:
            _states = extract_states(_msgs)
            _ref = resolve_reference(_q, _states)
            if _ref:
                verify_alignment(sql, _ref, _q)      # 어긋나면 WARNING 을 남긴다
    except Exception as _e:
        logger.warning("turn_alignment_skipped", error=str(_e)[:120])

    can_view_fi = bool(state.get("can_view_fi", False))
    allowed_tables = _allowed_tables_from_sources(
        state.get("enabled_sources"),
        can_view_fi,
    )
    is_valid, validation_error = validate_sql(sql, allowed_tables=allowed_tables)
    if not is_valid:
        logger.warning("sql_pre_execution_validation_failed", error=validation_error, sql=sql[:200])
        if validation_error == FI_ACCESS_DENIED_MESSAGE:
            error = FI_ACCESS_DENIED_MESSAGE
        else:
            error = (_source_scope_message(validation_error, state.get("enabled_sources"))
                     or f"SQL 검증 실패: {validation_error}")
        return {"sql_result": None, "error": error}

    logger.info("executing_sql", sql=sql[:200])

    try:
        bq = get_bigquery_client()
        results = bq.execute_query(sql, timeout=300.0, max_rows=1000)
        logger.info("sql_executed", row_count=len(results))

        # Product-filtered query returned nothing → likely an abbreviated/unfamiliar
        # product name that Flash guessed wrong (e.g. "톤브 앰플 백"). Escalate ONCE
        # to a stronger model instead of always paying that latency cost up front.
        if len(results) == 0 and re.search(r"`?SET`?\s+LIKE|\bProduct\s+LIKE", sql, re.IGNORECASE):
            escalated = _retry_with_stronger_model(
                state.get("query", ""),
                sql,
                bq,
                brand_filter=state.get("brand_filter"),
                can_view_fi=can_view_fi,
                allowed_tables=allowed_tables,
            )
            if escalated is not None:
                return escalated

        return {"sql_result": results, "error": None}
    except Exception as e:
        error_str = str(e)
        # 구문 오류·컬럼명 오류 → 오류 메시지를 실어 1회 재생성.
        # ⚠️ "Syntax error" 만 잡으면 "Unrecognized name"(없는 컬럼)이 재시도 없이
        # 사용자에게 그대로 노출된다 (2026-08-06 @@아마존검색 'Date' 컬럼 사고).
        # "incompatible types"(UNION ALL 컬럼 타입 불일치)도 재생성 대상 —
        # 2026-08-10 실사용자 '마차이' 리뷰 질문이 재시도 없이 오류 노출됨
        if any(k in error_str for k in (
            "Syntax error", "Unrecognized name", "incompatible types",
            "neither grouped nor aggregated",
        )):
            logger.warning("sql_syntax_error_retry", error=error_str[:200], original_sql=sql[:300])
            try:
                llm = get_flash_client()
                query = state.get("query", "")
                # @@ 로 좁힌 소스는 기본 프롬프트에 스키마가 없다 — 재생성에도 스키마와
                # 테이블 스코프를 실어야 같은 컬럼 오류를 반복하지 않는다.
                # conv_context 도 전달 — 재생성 경로에서만 맥락이 빠지면 후속 질문이
                # 오류를 만났을 때 재생성본이 주제 이탈한다 (2026-08-10 검토에서 발견)
                _conv_ctx = state.get("conversation_context", "")
                _schema_ctx = _build_schema_context(query, allowed_tables, _conv_ctx)
                _scope = ""
                if allowed_tables is not None and 0 < len(allowed_tables) <= 5:
                    _scope = ("\n\n## ⛔ 사용 가능 테이블\n"
                              + "\n".join(f"- `{t}`" for t in sorted(allowed_tables))
                              + "\n위 테이블만 사용하고, 컬럼명은 위 스키마의 정확한 이름만 사용하라 "
                              "(모든 테이블에 Date 컬럼이 있는 게 아니다).")
                _syntax_rules = ""
                if "Syntax error" in error_str:
                    _syntax_rules = (
                        "\n1. 질문에 여러 항목(매출+마케팅비 등)이 있으면 **매출 SQL만 생성**"
                        "\n2. UNION ALL 금지! CASE WHEN 패턴만 사용!"
                        "\n3. 모든 괄호를 반드시 닫을 것! CTE 사용 시 WITH ... AS (...) SELECT ... 완전한 형태"
                        "\n4. 짧고 간결한 SQL만! 20줄 이내!"
                    )
                if "neither grouped nor aggregated" in error_str:
                    _syntax_rules += (
                        "\n1. GROUP BY 집계와 ROW_NUMBER/RANK 계산을 같은 SELECT에 두지 말 것"
                        "\n2. 첫 CTE에서 기간·항목별 SUM 집계를 완료하고, 다음 CTE에서 "
                        "집계된 기간 별칭으로 PARTITION BY 할 것"
                        "\n3. 윈도 함수에서 GROUP BY 이전 원본 Date를 다시 참조하지 말 것"
                    )
                _conv_retry = ""
                if _conv_ctx:
                    _conv_retry = (
                        "\n\n## 이전 대화 맥락 (정정 후 재생성에도 반드시 유지)\n"
                        + _conv_ctx
                    )
                _period_retry_required = _requires_partitioned_period_ranking(
                    query, _conv_ctx
                )
                _period_retry_rule = ""
                if _period_retry_required:
                    _period_retry_rule = (
                        "\n⛔ 기간별 주요 항목 분석이므로 첫 CTE에서 기간·항목별 집계를 "
                        "완료하고, 다음 CTE에서 집계된 기간 별칭으로 ROW_NUMBER() OVER "
                        "(PARTITION BY 기간별칭 ORDER BY 지표 DESC)를 계산한 뒤 바깥 WHERE "
                        "rank <= N으로 실제 필터링하라."
                    )
                _cap_retry_required = _requires_current_date_cap(query, _conv_ctx)
                _cap_retry_rule = ""
                if _cap_retry_required:
                    _cap_retry_rule = (
                        "\n⛔ 과거 실적의 시작일만 지정된 질문이므로 모든 원본 매출/판매 "
                        "테이블 스캔에 Date <= CURRENT_DATETIME() 상한을 유지하라."
                    )
                retry_prompt = (
                    _load_prompt("sql_generator.txt", can_view_fi=can_view_fi)
                    + _schema_ctx + _scope + _conv_retry
                    + f"\n\n## 사용자 질문\n{query}"
                    + f"\n\n⛔⛔⛔ 이전 SQL이 다음 오류로 실패했다:\n{error_str[:300]}\n"
                    + "오류 원인을 고쳐 SQL을 다시 작성하라. 컬럼명은 스키마에 있는 정확한 이름만 사용."
                    + _syntax_rules + _period_retry_rule + _cap_retry_rule
                    + _build_brand_section(state.get("brand_filter"))
                )
                retry_sql = llm.generate(retry_prompt, temperature=0.0, max_output_tokens=10000)
                from app.core.security import sanitize_sql
                retry_sql = sanitize_sql(retry_sql)
                retry_sql = _localize_country_literals(retry_sql)
                retry_sql = _localize_team_literals(retry_sql)
                retry_sql = _localize_promotion_literals(retry_sql)
                retry_sql = _strip_unrequested_brand_filter(retry_sql, query)
                if retry_sql:
                    if (_period_retry_required
                            and not _has_partitioned_period_ranking(retry_sql)):
                        logger.error(
                            "sql_retry_period_rank_missing",
                            sql=retry_sql[:300],
                        )
                        retry_sql = ""
                if retry_sql:
                    if _cap_retry_required and not _has_current_date_cap(retry_sql):
                        logger.error(
                            "sql_retry_current_date_cap_missing",
                            sql=retry_sql[:300],
                        )
                        retry_sql = ""
                if retry_sql:
                    is_valid, _verr = validate_sql(
                        retry_sql,
                        allowed_tables=allowed_tables,
                    )
                    if not is_valid:
                        logger.error("sql_retry_validation_failed", error=_verr[:200])
                        # fall through to the existing failure return below (do NOT execute)
                    else:
                        logger.info("sql_retry_executing", sql=retry_sql[:200])
                        results = bq.execute_query(retry_sql, timeout=300.0, max_rows=1000)
                        logger.info("sql_retry_success", row_count=len(results))
                        conv_ctx = state.get("conversation_context", "")
                        retry_brand_filter = state.get("brand_filter")
                        if not conv_ctx:
                            _ck = _cache_key(query, retry_brand_filter)
                            _cache_store(_ck, query, retry_sql, retry_brand_filter)
                        return {"sql_result": results, "error": None, "generated_sql": retry_sql}
            except Exception as retry_e:
                logger.error("sql_retry_also_failed", error=str(retry_e)[:200])
        logger.error("sql_execution_failed", error=error_str[:200])
        return {"sql_result": None, "error": f"SQL 실행 실패: {error_str}"}


# Friendly display names for BigQuery tables
_TABLE_DISPLAY_NAMES = {
    "SALES_ALL_Backup": "통합 매출 (SALES_ALL)",
    "integrated_ad": "통합 광고 데이터",
    "Integrated_marketing_cost": "통합 마케팅 비용",
    "shopify_analysis_sales": "Shopify 판매",
    "raw_data": "플랫폼 메트릭스",
    "influencer_input_ALL_TEAMS": "인플루언서 마케팅",
    "amazon_search_analytics_catalog_performance": "아마존 검색 분석",
    "Amazon_Review": "아마존 리뷰",
    "Qoo10_Review": "큐텐 리뷰",
    "Shopee_Review": "쇼피 리뷰",
    "Smartstore_Review": "스마트스토어 리뷰",
    "meta data_test": "메타 광고 라이브러리",
    "Product": "제품 마스터",
}


def _extract_table_sources(sql: str) -> str:
    """Extract table names from SQL and return a friendly source string."""
    if not sql:
        return "BigQuery"
    # Match backtick-quoted full paths: `project.dataset.table`
    matches = re.findall(r'`([^`]+)`', sql)
    table_names = set()
    for m in matches:
        parts = m.split(".")
        if len(parts) >= 2:
            table_short = parts[-1]
            display = _TABLE_DISPLAY_NAMES.get(table_short, table_short)
            table_names.add(display)
    if not table_names:
        # Fallback: try unquoted FROM/JOIN table references
        from_matches = re.findall(r'(?:FROM|JOIN)\s+([\w.-]+)', sql, re.IGNORECASE)
        for fm in from_matches:
            parts = fm.split(".")
            table_short = parts[-1]
            display = _TABLE_DISPLAY_NAMES.get(table_short, table_short)
            table_names.add(display)
    if not table_names:
        return "BigQuery"
    return " + ".join(sorted(table_names))



# 프롬프트의 DISTINCT 국가 목록을 단일 소스로 재사용한다 (목록을 코드에 또 두지 않는다).
_COUNTRY_VALUES: list = []


def _all_countries() -> list:
    """국가 목록 — **값 목록 캐시**에서 온다 (프롬프트 파싱 아님).

    ⛔ 예전엔 `prompts/sql_generator.txt` 의 DISTINCT 줄을 정규식으로 파싱했다.
       그 줄이 `{{VALUES:Country}}` 자리표시자로 바뀌면서 파싱이 빈 목록을
       돌려줬고, "에콰도르는 실재한다" 는 판정이 통째로 죽었다 (2026-08-18).
       사본을 없애는 작업이 **사본을 읽던 코드**를 깨뜨린 것이다 — 같은 캐시를 본다.
    """
    global _COUNTRY_VALUES
    if _COUNTRY_VALUES:
        return _COUNTRY_VALUES
    try:
        from app.core.value_lists import _cached
        _COUNTRY_VALUES = _cached("Country") or []
    except Exception:
        _COUNTRY_VALUES = []
    return _COUNTRY_VALUES

def _country_hint(sql: str) -> str:
    """0건일 때 국가 값을 **실제 목록과 대조해** 알려준다 (추측하게 두지 않는다)."""
    import re as _re
    known = _all_countries()
    used = _re.findall(r"Country\s*(?:=|LIKE|IN)\s*\(?\s*'([^']+)'", sql or "", _re.I)
    used += _re.findall(r"'([^']+)'", " ".join(
        _re.findall(r"Country\s+IN\s*\(([^)]*)\)", sql or "", _re.I)))
    seen, checked = set(), []
    for v in used:
        v = v.strip().strip("%")
        if v and v not in seen:
            seen.add(v)
            checked.append(v)
    if not known or not checked:
        return "Country는 한국어 국가명이다 (실제 목록은 위 DISTINCT 절 참조)."
    ok = [v for v in checked if v in known]
    bad = [v for v in checked if v not in known]
    parts = []
    if ok:
        parts.append(
            f"⚠️ {', '.join(ok)} 는 **실재하는 국가다** (DISTINCT 목록에 있음). "
            f"0건인 이유를 국가 값 탓으로 돌리지 마라 — 기간·거래처·제품 등 **다른 조건**이 원인이다")
    for v in bad:
        near = [k for k in known if v[:2] and v[:2] in k][:5]
        parts.append(f"'{v}' 는 Country 목록에 없다"
                     + (f" (비슷한 값: {', '.join(near)})" if near else ""))
    return " / ".join(parts)



# ⛔ **금액 컬럼을 "수량" 으로 부르는 오답이 실제로 나갔다** (2026-08-14).
#    "에콰도르 Valkirias FOC" 답변이 `SUM(FOC)` 376,968.4 를 **"377,000개"** 라고 썼다.
#    `FOC` 는 `Production_Cost2` 와 같은 값, 즉 **원가 금액(원)** 이다 — 프롬프트 스키마엔
#    "FOC 금액" 이라고 맞게 적혀 있었는데도 서술 단계에서 뒤집혔다. 수량 컬럼은
#    `Total_Qty`·`FOC_Qty` 뿐이고 그건 `Product` 테이블에만 있다.
#    금액을 개수로 읽으면 **자릿수가 그대로라 틀린 티가 안 난다** — 가장 위험한 종류다.
_MONEY_COLS = ("Sales1_R", "Sales2_R", "Sales1_R_FOC", "Sales2_R_FOC", "FOC",
               "Production_Cost2", "Production_Cost", "Discount_Coupon",
               "Service_Fee", "ad_spend_krw", "conversion_value_krw")
_QTY_COLS = ("Total_Qty", "FOC_Qty")


def _unit_note(sql: str) -> str:
    """SQL 이 고른 컬럼에서 **단위를 결정적으로** 뽑아 서술 단계에 못 박는다."""
    up = (sql or "").upper()
    money = [c for c in _MONEY_COLS if c.upper() in up]
    qty = [c for c in _QTY_COLS if c.upper() in up]
    if not money and not qty:
        return ""
    lines = ["", "⚠️ **단위 (SQL 이 고른 컬럼에서 확정된 사실 — 추측 금지)**:"]
    if money:
        lines.append(
            "- " + ", ".join(money) + " 는 **금액(원)** 이다. 값에 '개'·'수량'·'개수'·"
            "'EA' 를 붙이지 마라. 표 헤더도 '금액' 또는 '원' 으로 쓸 것"
            + ("" if qty else " — 이 결과에는 **수량 컬럼이 없다.** 수량을 답하지 마라"))
    if qty:
        lines.append("- " + ", ".join(qty) + " 만 **수량(개)** 이다")
    return "\n".join(lines)


def format_answer(state: AgentState) -> Dict[str, Any]:
    """Format SQL results into a natural language answer with optional chart.

    Args:
        state: Current agent state with sql_result.

    Returns:
        Updated state with answer (and chart if applicable).
    """
    query = state["query"]
    sql = state.get("generated_sql", "")
    results = state.get("sql_result")
    error = state.get("error")

    # Handle error cases
    if error == FI_ACCESS_DENIED_MESSAGE:
        return {"answer": FI_ACCESS_DENIED_MESSAGE}
    # 데이터소스 범위 안내는 오류가 아니라 사용자 안내다 — 그대로 보여준다
    if error and str(error).startswith(SOURCE_SCOPE_DENIED_PREFIX):
        return {"answer": error}
    if error:
        return {
            "answer": f"죄송합니다. 질문을 처리하는 중 오류가 발생했습니다.\n\n오류: {error}"
        }

    # GROUP BY 없는 집계 쿼리(SUM/COUNT 단독)는 매칭 데이터가 0건이어도
    # 값이 전부 NULL인 행 1개를 돌려준다. 그대로 두면 "0건" 경로를 못 타고
    # LLM이 빈 결과를 놓친 채 원인을 지어낸다 (2028년 미래 날짜 질문에
    # "신규 진출 여부 확인 필요"로 답한 사례, 2026-08). 빈 결과로 정규화한다.
    if results and len(results) == 1 and all(v is None for v in results[0].values()):
        logger.info("sql_result_all_null_treated_as_empty", sql=(sql or "")[:200])
        results = None

    results = _relabel_team_values(results)

    if not results:
        # 0건 질문에서 미인식 용어를 후보로 수집 (백그라운드 — 응답을 늦추지 않는다).
        # 스트리밍 경로도 0건이면 format_answer 를 타므로 이 한 곳이면 된다.
        try:
            import threading as _th

            from app.core.term_aliases import collect_candidates as _cc
            _th.Thread(target=_cc, args=(query,), daemon=True).start()
        except Exception:
            pass
        # Build context hints for valid column values referenced in SQL
        _value_hints = []
        sql_upper = (sql or "").upper()
        if "TEAM_NEW" in sql_upper:
            # ⚠️ 예전에는 GM_EAST1·DD_DT1 같은 구버전 코드를 힌트로 넣고 있었다.
            # 프롬프트 본문에는 "구버전 — 없음!"이라 적혀 있는 값들이라, 0건일 때
            # 존재하지도 않는 코드를 대안으로 안내하게 된다 (2026-08-11 발견).
            _value_hints.append(
                "Team_NEW 유효 값(코드 = 공식 팀명): "
                + ", ".join(f"{c} = {kr}" for c, kr in TEAM_CODE2KR.items())
                + " (⚠️ GM_EAST1·GM_Ecomm·GM_MKT·DD_DT1·DD_DT2·GM_WEST 는 구버전이라 존재하지 않는다)"
            )
        if "COUNTRY" in sql_upper:
            # ⛔ **부분 목록을 "유효 값" 으로 주지 마라.** 예전엔 191개 중 12개만 나열하고
            #    "등" 을 붙였는데, LLM 이 그걸 전체 목록으로 읽고 **실재하는 국가를 없다고
            #    단정했다** — "에콰도르 Valkirias FOC" 질문에 "에콰도르는 유효 국가 목록에
            #    존재하지 않는다" 고 답했다 (2026-08-14 사용자 제보). 실제로는 2,448건·33.8억
            #    (2022-08~2026-08) 이 있고 Valkirias 거래처도 있었다.
            #    Team_NEW·Continent 힌트가 **전체 목록**을 주는 것과 같은 이유다.
            _value_hints.append(_country_hint(sql))
        # 광역 대륙명을 Continent2에서 찾는 오류가 잦다 (유럽 → 0건, 2026-08 실제 발생).
        # 유효 값을 그대로 넘겨 LLM이 원인을 추측하지 않고 대조로 짚게 한다.
        if "CONTINENT1" in sql_upper:
            _value_hints.append(
                "Continent1 유효 값: CIS, 글로벌, 기타, 남미, 북미, 아시아, 아프리카, "
                "오세아니아, 유럽, 중동, 중미"
            )
        if "CONTINENT2" in sql_upper:
            _value_hints.append(
                "Continent2 유효 값: CIS, 글로벌_B2B, 글로벌_플랫폼, 기타, 남아메리카, 동남아시아, "
                "동남유럽, 동아시아, 북미, 북아프리카, 북유럽, 서남아시아, 서유럽, 아프리카, "
                "오세아니아, 중동, 중앙아메리카 "
                "(⚠️ '유럽'·'아시아'·'동유럽'은 Continent2에 없다. 광역 대륙은 Continent1을 써야 한다)"
            )
        # ⛔ **유효 값 목록만으로는 추측을 못 막는다.** 국가 힌트를 전체 목록으로 고쳤더니
        #    같은 질문에서 이번엔 거래처명을 의심했다 (2026-08-14). 목록을 붙일 수 없는
        #    컬럼(거래처·제품)이 늘 남기 때문이다. **필터를 하나씩 빼고 실제로 세어**
        #    범인을 짚는다 — 0행일 때만 도는 조회 1회다.
        try:
            from app.core.zero_row import diagnose as _diagnose
            _dx = _diagnose(sql, get_bigquery_client())
            if _dx:
                _value_hints.append(_dx)
        except Exception as _e:
            logger.warning("zero_row_diagnose_skipped", error=str(_e)[:150])

        _hints_text = "\n".join(_value_hints)

        # Try Flash LLM for helpful empty-result message (with timeout), else template
        try:
            empty_llm = get_flash_client()
            # 오늘 날짜를 반드시 넣는다. 없으면 LLM이 현재 연도를 추측해
            # "작년은 2024년" 같은 틀린 원인 분석을 내놓는다 (2026-08 실제 발생).
            _today = datetime.now()
            _range_note = ""
            # 미래 기간을 물으면 그게 0건의 확정적 원인이다. 짚어주지 않으면
            # LLM 이 "신규 진출 여부" 같은 엉뚱한 원인을 지어낸다.
            _years = [int(y) for y in re.findall(r"\b(20\d{2})\b", sql or "")]
            if _years and min(_years) > _today.year:
                _range_note += (
                    f"\n⚠️ 이 SQL 은 {min(_years)}년을 조회하는데 오늘은 {_today.year}년이다. "
                    "**미래 기간이라 데이터가 있을 수 없다**는 것이 0행의 확정적 원인이다. "
                    "다른 원인을 추측하지 말고 이 점을 명확히 안내하라."
                )
            if "FI_LLM_Flat" in (sql or ""):
                _range_note = (
                    "\n⚠️ 이 질문이 쓰는 재무 손익 테이블(FI_LLM_Flat)은 **2026-01 ~ 2026-06** 만 "
                    "보유하고 있다. 요청 기간이 이 범위 밖이면 그것이 0행의 확정적 원인이므로, "
                    "다른 원인을 추측하지 말고 보유 기간을 명확히 안내한 뒤 그 범위로 다시 제안할 것."
                )
            empty_prompt = f"""사용자가 "{query}"라고 질문했고, SQL 결과가 0행입니다:
```sql
{sql}
```
오늘 날짜: {_today.strftime('%Y-%m-%d')} (올해={_today.year}, 작년={_today.year - 1})
{f"유효 값: {_hints_text}" if _hints_text else ""}{_range_note}
⚠️ 원인 분석 방법: 위에 "유효 값" 목록이 있으면 **SQL의 필터 리터럴을 그 목록과 먼저 대조하라.**
목록에 없는 값으로 필터했다면 **그것이 0행의 확정적 원인**이다 — 다른 원인을 나열하지 말고
어느 값이 왜 잘못됐는지, 올바른 컬럼·값이 무엇인지 한 가지로 단정해 답하라.
대조해도 원인이 없을 때만 추정 원인을 제시하되, 추정임을 밝혀라.

간결하게: 1) 해당 조건의 데이터가 없다는 안내 2) 위 방법으로 짚은 원인 3) 구체적인 대안 질문 2개. 한국어. ⚠️ "조회하지 못했습니다" 표현 사용 금지! "해당 조건의 데이터가 존재하지 않습니다" 사용."""
            # ⚠️ with 블록으로 감싸지 마라. 블록을 빠져나갈 때 shutdown(wait=True)가
            # 걸려 아래 8초 타임아웃이 무의미해진다 (LLM 이 멈추면 그만큼 그대로 대기).
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                f = pool.submit(empty_llm.generate, empty_prompt, None, 0.3)
                answer = f.result(timeout=8.0)
            finally:
                pool.shutdown(wait=False)
            if answer and len(answer) > 30:
                return {"answer": answer}
        except (concurrent.futures.TimeoutError, Exception):
            pass
        # Template fallback — more helpful than a single line
        return {
            "answer": (
                "### 📊 데이터 조회 결과\n\n"
                "해당 조건의 데이터가 조회되지 않았습니다.\n\n"
                "#### 확인 사항\n"
                "- 국가명이나 채널명(쇼피/아마존/틱톡/라자다 등)이 정확한지 확인해 주세요\n"
                "- 해당 국가에 해당 채널이 존재하는지 확인이 필요합니다 (예: 일본 아마존은 데이터 없음)\n"
                "- 조회 기간을 넓혀서 다시 질문해 보세요\n\n"
                "---\n\n"
                "> 💡 **이런 식으로 질문해 보세요**\n"
                "> - \"2024년 미국 아마존 월별 매출 알려줘\"\n"
                "> - \"베트남 쇼피 2025년 매출 알려줘\"\n"
                "> - \"태국 라자다 분기별 매출 추이\""
            )
        }

    # Use Flash for answer formatting (faster, 3-5s vs 15-25s with Pro)
    llm = get_flash_client()

    # Limit result preview for prompt — smart strategy based on result size
    _ts_keywords = ("월별", "주차별", "주별", "일별", "분기별", "추이", "트렌드", "변동")
    _is_timeseries = any(kw in query for kw in _ts_keywords)

    # Product name columns — convert underscores to spaces for readability
    def _is_product_col(col_name: str) -> bool:
        cl = col_name.lower()
        return any(kw in cl for kw in ("product", "set", "제품", "item_name", "sku_name"))

    def _humanize_row(row, max_text_len=80):
        humanized = {}
        for k, v in row.items():
            if isinstance(v, str) and _is_product_col(k):
                v = v.replace("_", " ")
            if isinstance(v, str) and len(v) > max_text_len:
                humanized[k] = v[:max_text_len] + "..."
            else:
                humanized[k] = v
        return humanized

    if len(results) > 100:
        try:
            result_preview = _build_smart_preview(results, query)
        except Exception as e:
            logger.warning("smart_preview_failed_fallback", error=str(e))
            preview_rows = [_humanize_row(r) for r in results[:15]]
            result_preview = json.dumps(preview_rows, ensure_ascii=False, indent=2, default=str)
    elif _is_timeseries and len(results) <= 60:
        # Time-series: send ALL rows so LLM can show full table & chart (cap at 60)
        preview_rows = [_humanize_row(r) for r in results]
        result_preview = json.dumps(preview_rows, ensure_ascii=False, indent=2, default=str)
    elif _is_timeseries and len(results) <= 100:
        # Grouped time-series (e.g. 월별 몰별): pivot to compact table
        result_preview = _try_pivot_timeseries(results, query)
        if not result_preview:
            preview_rows = [_humanize_row(r) for r in results[:60]]
            result_preview = json.dumps(preview_rows, ensure_ascii=False, indent=2, default=str)
    else:
        preview_rows = [_humanize_row(r) for r in results[:15]]
        result_preview = json.dumps(preview_rows, ensure_ascii=False, indent=2, default=str)

    # Hard cap on preview size to keep LLM prompt manageable (max ~5KB)
    if len(result_preview) > 5000:
        preview_rows = [_humanize_row(r) for r in results[:8]]
        result_preview = json.dumps(preview_rows, ensure_ascii=False, indent=2, default=str)

    today = datetime.now().strftime("%Y-%m-%d")
    today_kr = datetime.now().strftime("%Y년 %m월 %d일")

    # Extract actual table names for source attribution
    table_source = _extract_table_sources(sql)

    # Detect data date range from results for scope verification
    _date_cols = [k for k in (results[0].keys() if results else [])
                  if any(d in k.lower() for d in ("date", "month", "year", "날짜", "연도", "월"))]
    _date_vals = set()
    for row in results[:100]:
        for dc in _date_cols:
            v = row.get(dc)
            if v is not None:
                _date_vals.add(str(v))
    data_range_hint = f"데이터에 포함된 날짜/기간 값: {sorted(_date_vals)[:20]}" if _date_vals else ""

    # Pre-build conditional warnings (avoid backslash in f-string)
    _preview_warning = ""
    if len(results) > 15:
        _preview_warning = f"⚠️ 위 JSON은 전체 {len(results)}행 중 상위 프리뷰입니다. 나머지 데이터도 존재하므로 프리뷰 기반으로 데이터 범위를 단정하지 마세요."

    _limit_warning = ""
    if len(results) >= 1000:
        _limit_warning = (
            f"⚠️ 결과가 {len(results)}행으로 LIMIT 1,000에 도달했습니다. "
            "전체 데이터 중 일부만 포함되어 있습니다. "
            '답변 마지막에 반드시 다음 경고를 추가하세요: '
            '\'> ⚠️ 조회 결과가 1,000행 제한에 도달하여 일부 데이터만 표시되었습니다. '
            '더 구체적인 조건으로 검색해주세요.\''
        )

    # 브랜드 표기 규칙 (2026-08-05 재정의)
    #   브랜드는 스킨천사 · 우마(UM) · 좀비뷰티 · 커먼랩스 넷뿐이다.
    #   Brand='CBT' 는 팀 값이 잘못 들어간 행이고 실제로는 스킨천사 매출이므로 SK 에 합산한다.
    #   이전 규칙은 "CBT(중국사업 부문)로 병기"였는데, 그러면 브랜드별 표에 팀이 별도 행으로
    #   남는다 — 노션 AI Tester 에 "cbt가 브랜드로 되어있습니다" 제보가 올라온 원인.
    _brand_warning = ""
    if re.search(r"Brand\s+(IN\s*\(|=\s*')[^)]*\b(CBT|UM|DD)\b", sql, re.IGNORECASE):
        _brand_warning = (
            "⚠️ **브랜드 표기 (매우 중요!)**: 브랜드는 **스킨천사(SKIN1004) · 우마(umma) · "
            "좀비뷰티 · 커먼랩스** 넷뿐입니다.\n"
            "- **`CBT` 는 브랜드가 아니라 스킨천사 매출입니다.** 조회 결과에 CBT 가 별도로 나오면 "
            "**SK(스킨천사)와 합산해서** 하나의 항목으로 제시하세요. "
            "\"중국사업부문\" 같은 별도 행으로 남기지 마세요.\n"
            "- `UM` 은 **우마(umma)** 라는 별도 브랜드입니다.\n"
            "- CBT·JBT·KBT 등은 **팀**입니다. 브랜드로 나열하지 마세요."
        )

    # 팀 표기 — 데이터는 코드, 사내 공식 명칭은 한글이다 (2026-08-11 확정).
    # 코드를 그대로 내면 사용자가 자기 팀을 못 알아본다. 한글명을 앞세우고 코드를 병기한다.
    _team_warning = ""
    if re.search(r"Team_NEW", sql, re.IGNORECASE):
        _team_warning = (
            "⚠️ **팀 표기 (중요!)**: 조회 결과의 팀 코드는 **공식 한글 팀명으로 바꿔서** 제시하세요. "
            "표·차트 라벨·본문 모두 `한글팀명(코드)` 형식으로 씁니다 (예: `영업1팀(B2B1)`).\n"
            + "\n".join(f"- `{c}` → **{kr}**" for c, kr in TEAM_CODE2KR.items())
            + "\n- 위 목록에 없는 값(`기타`·`OP` 등)은 정식 팀이 아니므로 "
            "'기타'로 묶거나 표에서 제외하고, 그 사실을 한 줄로 밝히세요."
        )

    _ingredient_keywords = ("성분", "나이아신아마이드", "레티놀", "히알루론산", "판테놀", "세라마이드")
    _ingredient_query = any(kw in query for kw in _ingredient_keywords) and any(
        kw in query for kw in ("포함", "미포함", "함유", "안 들어", "안들어", "없는")
    )
    if _ingredient_query and re.search(r"(NOT\s+)?LIKE\s+'%", sql, re.IGNORECASE):
        _ingredient_warning = (
            "⚠️ **성분 데이터 한계 경고 (매우 중요!)**: 이 조회는 제품명(product_name) 텍스트 매칭으로 "
            "성분 포함/미포함을 판단한 것이며, 실제 배합 성분(포뮬레이션) 테이블을 조회한 것이 아닙니다. "
            "제품명에 성분명이 없다고 해서 그 제품에 실제로 해당 성분이 들어있지 않은 것은 아닙니다. "
            "답변 요약 바로 아래에 반드시 다음 경고를 그대로 포함하세요: "
            "\"⚠️ 본 결과는 제품명 기반 조회이며, 전체 제품의 실제 배합 성분 데이터는 보유하고 있지 않아 "
            "정확한 성분 포함/미포함 여부와 다를 수 있습니다.\" "
            "이 결과를 확정적인 '미포함 TOP' 순위처럼 단정적으로 제시하지 마세요."
        )
    else:
        _ingredient_warning = ""

    # B2B 할인 0원을 "할인을 안 했다"로 서술하는 것을 막는다.
    # Discount_Coupon 에는 B2B 값이 구조적으로 들어오지 않는다 — 0 은 '없음'이 아니라 '해당 없음'이다.
    # ⚠️ 프롬프트(sql_generator.txt)에만 적어두면 SQL 생성에는 반영돼도 **서술 프롬프트에는
    #    닿지 않아** 답변이 그대로 오도한다 (2026-08-12 실측). 서술 단계에서 다시 막는다.
    _b2b_discount_warning = ""
    if re.search(r"Discount_Coupon", sql, re.IGNORECASE) and re.search(
        r"Sales_Type\s*=\s*'B2B'", sql, re.IGNORECASE
    ):
        _b2b_discount_warning = (
            "⚠️ **B2B 할인 서술 규칙 (필수)**: `Discount_Coupon` 은 B2C 전용 항목이라 "
            "B2B 는 전 구간 0원으로 적재됩니다. 합계 0원을 **'할인을 하지 않았다'로 서술하지 마세요.** "
            "요약에 \"이 항목은 B2C 전용이라 B2B 는 집계되지 않습니다\"를 반드시 포함하고, "
            "'프로모션이 없었는지 확인이 필요하다', '정가 거래 중심으로 보인다' 같은 "
            "추측성 해석은 붙이지 마세요. B2B 의 무상 지원은 FOC(무상 출고)로 확인한다고 안내하세요."
        )

    _result_header = f"총 {len(results)}행"
    if len(results) > 15:
        _result_header += f", 아래는 상위 {min(15, len(results))}건 프리뷰"

    prompt = f"""다음은 사용자의 질문과 BigQuery 실행 결과입니다.
결과를 바탕으로 사용자에게 **구조화된 분석 보고서** 형태로 답변을 작성하세요.

{LANGUAGE_DETECTION_RULE}

## 오늘 날짜
{today_kr} (오늘 기준)

## 사용자 질문
{query}

## 실행된 SQL
```sql
{sql}
```

## 실행 결과 ({_result_header})
```json
{result_preview}
```
{_preview_warning}
{_limit_warning}
{data_range_hint}

## 답변 형식 (반드시 아래 섹션 구조를 따르세요)

### 📊 [질문에 맞는 간결한 제목]
#### 요약
[1-2문장으로 핵심 결론만. 가장 중요한 수치는 **굵게** 표시. 장황한 설명 금지!]
#### 상세 데이터 (표)
[마크다운 표로 정리. 숫자는 오른쪽 정렬(---:). 시계열은 전체 행 표시]
#### 분석 및 인사이트
[2-3개 bullet만. 각 1줄 이내. 비중/변화율/추세 중심. 문단형 서술 금지!]
---
*조회 기준: {today} | 내부 데이터베이스*
> 💡 **이런 것도 물어보세요**
> - [구체적 후속 질문 1 — 다른 기간/국가/제품 등 범위 확장]
> - [구체적 후속 질문 2 — 관련 데이터 심화 분석]
> - [구체적 후속 질문 3 — 다른 관점의 분석]

⚠️ 반드시 구체적인 후속 질문 3개를 생성하세요. "[후속 질문 3개]" 같은 플레이스홀더 텍스트를 절대 출력하지 마세요. 실제 사용자가 클릭해서 바로 질문할 수 있는 구체적 문장이어야 합니다.

{_unit_note(sql)}
⚠️ **데이터 출처 보안**: 답변 본문에서 테이블명(`SALES_ALL_Backup`, `Product`, `SALES_ALL` 등), 프로젝트 ID(`skin1004-319714`), 데이터셋명, 컬럼명(`Sales1_R`, `Total_Qty` 등)을 절대 노출하지 마세요. 출처를 언급해야 하면 '내부 데이터베이스'라고만 표현하세요.

⚠️ **분량 제한 (최우선)**:
- 전체 답변 8000자 이내 (표 포함). 8000자 초과 시 **요약 모드**로 전환:
  - 상위 10건만 표시하고 나머지는 "외 N건" 으로 생략
  - 상세 데이터 대신 집계/요약 통계만 제공
  - "전체 데이터가 필요하시면 말씀해주세요" 안내
- 요약은 2문장 이내, 인사이트는 bullet 3개 이내 (각 1줄)
- 장황한 해석/배경설명/가정 금지. 숫자와 팩트만!
- SQL FORMAT 이슈 설명 금지 — 데이터 그대로 보여주기만 하면 됨

## 작성 규칙
- SQL 결과 데이터만 사용 (외부 정보 절대 금지)
- 금액 표기: 1억 이상 → "약 12.3억원" 형태(실제 숫자 대입!), 1억 미만 → 천 단위 쉼표(예: 7,700만원). 퍼센트 소수점 1자리. ⚠️ "OO.O억원" 같은 플레이스홀더 출력 절대 금지! 반드시 실제 계산된 숫자를 넣으세요
- 3행 이상 비교 → 마크다운 표 필수. 시계열은 전체 행 표시 (생략 금지)
- 제품명(SET) 영어 원본 그대로 공백 포함 (한국어 번역 금지, 언더스코어 사용 금지)
- 단순 수치 1개만 → "상세 데이터" 생략, 요약만
- 기간 부족 시 첫 줄에 ⚠️ 표시. 질문 범위와 데이터 범위 불일치 시 명시
- 비즈니스 인사이트 필수: 비중, 변화율, 추세, 집중도, 비교 관점
- 조건 설명(브랜드, 기간)은 답변 끝에 짧게 괄호로
- ⚠️ 불완전 월 데이터 경고 (매우 중요!): 오늘은 {today_kr}입니다. 월별 추이/비교 데이터에 현재 월({today[:7]})이 포함되어 있다면, 해당 월은 아직 진행 중이므로 데이터가 불완전합니다. 반드시 "⚠️ {today[:7]}월 데이터는 {today}까지의 부분 집계입니다"라고 명시하고, 추세 분석에서 현재 월 수치가 낮은 것은 미완료 때문임을 언급하세요. 절대 불완전한 현재 월 데이터를 완성된 과거 월과 동일 선상에서 비교하지 마세요.
{_brand_warning}
{_team_warning}
{_ingredient_warning}
{_b2b_discount_warning}
"""

    try:
        # Answer generation: foreground. Chart: parallel with short timeout.
        # User sees answer immediately; chart appended only if ready fast enough.
        # ⚠️ with 블록으로 감싸지 마라. 블록 종료 시 shutdown(wait=True)가 걸려
        # 아래 "차트 8초 넘으면 건너뛴다"는 로직이 무력화된다 — 느린 차트가
        # 답변 전체를 그만큼 붙잡는다. 스트리밍 경로처럼 shutdown(wait=False)로 푼다.
        chart_markdown = None
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            answer_future = executor.submit(llm.generate, prompt, None, 0.05, 65536)
            chart_llm = get_flash_client()
            chart_future = executor.submit(
                _try_generate_chart, chart_llm, query, sql, result_preview, results
            )

            answer = answer_future.result(timeout=120.0)
            # Give chart up to 8s after answer is ready; skip if slow
            try:
                chart_markdown = chart_future.result(timeout=8.0)
            except concurrent.futures.TimeoutError:
                chart_markdown = None
                logger.info("chart_generation_skipped_timeout")
        finally:
            executor.shutdown(wait=False)

        if chart_markdown:
            insight_markers = ["#### 분석 및 인사이트", "#### 분석", "### 분석 및 인사이트", "### 분석"]
            inserted = False
            for marker in insight_markers:
                if marker in answer:
                    answer = answer.replace(marker, f"#### 시각화\n{chart_markdown}\n\n{marker}", 1)
                    inserted = True
                    break
            if not inserted:
                answer = answer + f"\n\n#### 시각화\n{chart_markdown}"

        # 답변 속 수치가 조회 결과에서 나온 것인지 확인한다. 보고서는 이 방어선을
        # 갖고 있었지만 **채팅에는 없었다** — 가장 위험한 실패(그럴듯한데 틀린 숫자)가
        # 가장 넓은 경로에서 무방비였다 (2026-08-13).
        # ⛔ 지금은 답변을 손대지 않는다 — 채팅의 수치는 상당수가 파생값이라 발생률을
        #    먼저 재고 다음 단계를 정한다. 기록은 WARNING 으로 남는다.
        try:
            from app.core.answer_check import log_verification
            log_verification(answer, results, query, route="bigquery")
        except Exception:
            pass

        answer += f"\n\n<details><summary>실행된 쿼리</summary>\n\n```sql\n{sql}\n```\n</details>"

        return {"answer": answer}
    except Exception as e:
        logger.error("answer_formatting_failed", error=str(e))
        # Fallback: return raw results
        return {
            "answer": f"SQL 실행 결과 ({len(results)}행):\n```json\n{result_preview}\n```"
        }


def _try_pivot_timeseries(results: list, query: str) -> str:
    """Pivot grouped time-series data into a compact table for LLM.

    Converts long-format (month, mall, revenue) → pivot (mall rows × month columns).
    Returns markdown table string, or empty string if pivot fails.
    """
    if not results or len(results) < 3:
        return ""
    try:
        keys = list(results[0].keys())
        if len(keys) < 3:
            return ""

        # Detect time column (first string column with time-like values)
        time_col = None
        group_col = None
        value_col = None

        for k in keys:
            vals = [str(r.get(k, "")) for r in results[:10]]
            is_time = any(
                any(h in v.lower() for h in ("2024", "2025", "2026", "월", "분기", "q1", "q2", "q3", "q4"))
                for v in vals
            )
            if is_time and not time_col:
                time_col = k
                continue
            # Numeric column
            try:
                float(results[0].get(k, 0) or 0)
                if not value_col:
                    value_col = k
            except (ValueError, TypeError):
                if not group_col:
                    group_col = k

        if not (time_col and group_col and value_col):
            return ""

        # Build pivot
        from collections import OrderedDict
        time_order = list(OrderedDict.fromkeys(str(r.get(time_col, "")) for r in results))
        groups = list(OrderedDict.fromkeys(str(r.get(group_col, "")) for r in results))

        pivot = {}
        group_totals = {}
        for r in results:
            t = str(r.get(time_col, ""))
            g = str(r.get(group_col, ""))
            v = float(r.get(value_col, 0) or 0)
            if g not in pivot:
                pivot[g] = {}
                group_totals[g] = 0
            pivot[g][t] = v
            group_totals[g] += v

        # Sort groups by total descending, limit to top 20
        groups = sorted(groups, key=lambda g: group_totals.get(g, 0), reverse=True)[:20]

        # Build markdown table
        header = f"| {group_col} | " + " | ".join(time_order) + " | 합계 |"
        separator = "|---:" + "|---:" * len(time_order) + "|---:|"
        rows = []
        for g in groups:
            vals = [pivot.get(g, {}).get(t, 0) for t in time_order]
            total = sum(vals)
            formatted = [f"{int(v):,}" for v in vals]
            rows.append(f"| {g} | " + " | ".join(formatted) + f" | {int(total):,} |")

        table = f"## 피벗 테이블 ({group_col} × {time_col}, 값: {value_col})\n\n{header}\n{separator}\n" + "\n".join(rows)
        table += f"\n\n*총 {len(results)}행 → 피벗: {len(groups)}그룹 × {len(time_order)}기간*"
        logger.info("pivot_timeseries_built", groups=len(groups), periods=len(time_order))
        return table
    except Exception as e:
        logger.warning("pivot_timeseries_failed", error=str(e))
        return ""


def _build_smart_preview(results: list, query: str) -> str:
    """Build a smart preview for large result sets (>100 rows).

    Instead of blindly sending the first 20 rows (which may be alphabetically
    biased), this produces an aggregate summary + top-20-by-revenue sample
    so the LLM can write a meaningful answer.
    """
    if not results:
        return "[]"

    keys = list(results[0].keys())

    # Auto-detect revenue/quantity columns by name AND by checking actual data types
    _rev_keywords = ("revenue", "sales", "매출", "amount", "금액")
    _qty_keywords = ("qty", "quantity", "수량")
    # Use exact word boundaries to avoid "Country" matching "count"
    rev_cols = [k for k in keys if any(w == k.lower() or k.lower().startswith(w) or k.lower().endswith(w) or f"_{w}" in k.lower() or f"{w}_" in k.lower() for w in _rev_keywords)]
    qty_cols = [k for k in keys if any(w == k.lower() or k.lower().startswith(w) or k.lower().endswith(w) or f"_{w}" in k.lower() or f"{w}_" in k.lower() for w in _qty_keywords)]

    # Validate detected columns are actually numeric by sampling first row
    def _is_numeric_col(col_name: str) -> bool:
        for row in results[:5]:
            v = row.get(col_name)
            if v is not None:
                try:
                    float(v)
                    return True
                except (ValueError, TypeError):
                    return False
        return False

    rev_cols = [c for c in rev_cols if _is_numeric_col(c)]
    qty_cols = [c for c in qty_cols if _is_numeric_col(c)]

    # Detect dimension columns (everything that's not a metric)
    metric_cols = set(rev_cols + qty_cols)
    dim_cols = [k for k in keys if k not in metric_cols]

    # Aggregate summary
    summary_parts = [f"총 행수: {len(results)}"]
    for dc in dim_cols:
        unique_vals = set(str(row.get(dc, "")) for row in results if row.get(dc) is not None)
        summary_parts.append(f"고유 {dc} 수: {len(unique_vals)}")
        if len(unique_vals) <= 15:
            summary_parts.append(f"  값: {sorted(unique_vals)}")

    for rc in rev_cols:
        total = sum(float(row.get(rc) or 0) for row in results)
        summary_parts.append(f"총 {rc}: {total:,.0f}")
    for qc in qty_cols:
        total = sum(float(row.get(qc) or 0) for row in results)
        summary_parts.append(f"총 {qc}: {total:,.0f}")

    # Sort by first revenue column DESC and take top 20
    sort_col = rev_cols[0] if rev_cols else (qty_cols[0] if qty_cols else None)
    if sort_col:
        sorted_rows = sorted(results, key=lambda r: float(r.get(sort_col) or 0), reverse=True)
    else:
        sorted_rows = results
    top_rows = sorted_rows[:15]

    # Truncate long text fields in preview rows
    truncated_rows = []
    for row in top_rows:
        tr = {}
        for k, v in row.items():
            if isinstance(v, str) and len(v) > 80:
                tr[k] = v[:80] + "..."
            else:
                tr[k] = v
        truncated_rows.append(tr)

    preview = {
        "summary": "\n".join(summary_parts),
        "top_15_sample": truncated_rows,
    }
    return json.dumps(preview, ensure_ascii=False, indent=2, default=str)


def _try_generate_chart(llm, query: str, sql: str, result_preview: str, results: list) -> str:
    """Attempt to generate an interactive chart for the SQL results.

    Returns a ```chart-config``` markdown block with Chart.js JSON, or empty string.
    The frontend renders this interactively with animations and tooltips.
    """
    from app.core.chart import build_chartjs_config, get_chart_config_prompt

    # ⚠️ 원문을 INFO 로만 남기면 프로덕션에서 사라진다 — stdlib 기본 레벨이 WARNING 이라
    # 앱 INFO 로그는 한 줄도 저널에 남지 않는다 (2026-08-11 확인: info 0 / warn 91 / err 46).
    # 실패를 진단하려면 실패 경로에서 원문을 함께 남겨야 한다.
    config_json = ""
    try:
        # Truncate SQL and results to prevent prompt overflow → chart config JSON truncation
        sql_short = sql[:300] + "..." if len(sql) > 300 else sql
        preview_short = result_preview[:800] + "..." if len(result_preview) > 800 else result_preview
        config_prompt = get_chart_config_prompt(query, sql_short, preview_short, len(results))
        config_json = llm.generate_json(config_prompt)
        logger.info("chart_config_raw", config_json=config_json[:500])
        config = json.loads(config_json)

        # Force chart when user explicitly requested visualization
        _CHART_REQUEST = ("차트", "그래프", "시각화", "그려", "그려줘", "chart", "graph", "시각화해", "도표", "플롯", "분기별", "월별", "추이", "비중")
        user_requested_chart = any(kw in query.lower() for kw in _CHART_REQUEST)
        if not config.get("needs_chart"):
            if user_requested_chart:
                logger.info("chart_forced_by_user_request", config=config)
                config["needs_chart"] = True
            else:
                logger.info("chart_not_needed", config=config)
                return ""

        # Force line chart for monthly/time-series queries
        _TREND_HINTS = ("월별", "월간", "추이", "트렌드", "trend", "monthly")
        if any(h in query.lower() for h in _TREND_HINTS):
            if config.get("chart_type") in ("bar", "grouped_bar", "stacked_bar"):
                logger.info("chart_type_overridden_to_line", original=config["chart_type"], reason="trend query")
                config["chart_type"] = "line"

        logger.info("chart_requested", chart_type=config.get("chart_type"), group_column=config.get("group_column"))

        # Build Chart.js config JSON (rendered interactively by frontend)
        chartjs_json = build_chartjs_config(config, results)
        if chartjs_json:
            return f"\n\n```chart-config\n{chartjs_json}\n```"
        logger.warning("chartjs_config_returned_none")
        return ""
    except Exception as e:
        logger.error("chart_generation_skipped", error=str(e), error_type=type(e).__name__,
                     raw=config_json[:600], raw_len=len(config_json), query=query[:80])
        return ""


# --- Routing Functions ---


def should_execute(state: AgentState) -> str:
    """Decide whether to execute SQL or return error.

    Args:
        state: Current agent state.

    Returns:
        Next node name.
    """
    if state.get("sql_valid"):
        return "execute_sql"
    return "format_answer"


def should_retry(state: AgentState) -> str:
    """Decide whether to retry SQL generation.

    Args:
        state: Current agent state.

    Returns:
        Next node name.
    """
    retry_count = state.get("retry_count", 0)
    if state.get("error") and retry_count < 2:
        return "generate_sql"
    return "format_answer"


# --- Build Graph ---


def build_sql_agent_graph() -> StateGraph:
    """Build the Text-to-SQL LangGraph workflow.

    Returns:
        Compiled LangGraph StateGraph.
    """
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("generate_sql", generate_sql)
    workflow.add_node("validate_sql", validate_sql_node)
    workflow.add_node("execute_sql", execute_sql)
    workflow.add_node("format_answer", format_answer)

    # Define edges
    workflow.set_entry_point("generate_sql")
    workflow.add_edge("generate_sql", "validate_sql")
    workflow.add_conditional_edges(
        "validate_sql",
        should_execute,
        {
            "execute_sql": "execute_sql",
            "format_answer": "format_answer",
        },
    )
    workflow.add_edge("execute_sql", "format_answer")
    workflow.add_edge("format_answer", END)

    return workflow.compile()


# Module-level compiled graph
sql_agent = build_sql_agent_graph()


def _extract_previous_sql(conversation_context: str) -> str:
    """Extract the last executed SQL from conversation context.

    Looks for SQL blocks in previous assistant messages.
    """
    import re as _re
    # Match SQL in code blocks
    matches = _re.findall(r'```sql\s*\n(.*?)\n```', conversation_context, _re.DOTALL)
    if matches:
        return matches[-1].strip()
    # Match SELECT statements directly
    select_matches = _re.findall(
        r'(SELECT\s+[\s\S]*?LIMIT\s+\d+)',
        conversation_context,
        _re.IGNORECASE,
    )
    if select_matches:
        return select_matches[-1].strip()
    return ""


async def run_sql_agent_unlimited(
    previous_sql: str,
    query: str,
    model_type: str = MODEL_GEMINI,
    can_view_fi: bool = False,
) -> str:
    """Re-run a previous SQL query without the LIMIT restriction.

    Used when user confirms they want full data after a 10000-row truncation warning.

    Args:
        previous_sql: The SQL from the previous query to re-run.
        query: Original user query for context.
        model_type: LLM model type.

    Returns:
        Formatted answer with full data.
    """
    import re as _re

    if not previous_sql:
        return "이전 쿼리를 찾을 수 없습니다. 원래 질문을 다시 해주세요."

    # Remove LIMIT clause from SQL
    unlimited_sql = _re.sub(r'\s*LIMIT\s+\d+\s*$', '', previous_sql, flags=_re.IGNORECASE).strip()

    logger.info("sql_agent_unlimited_rerun", sql=unlimited_sql[:200])

    # Validate
    allowed_tables = _allowed_tables_from_sources(None, can_view_fi)
    is_valid, error_msg = validate_sql(
        unlimited_sql,
        allowed_tables=allowed_tables,
    )
    if not is_valid:
        if error_msg == FI_ACCESS_DENIED_MESSAGE:
            return FI_ACCESS_DENIED_MESSAGE
        return f"SQL 검증 실패: {error_msg}"

    try:
        bq = get_bigquery_client()
        results = bq.execute_query(unlimited_sql, timeout=300.0, max_rows=100000)
        total_rows = len(results)
        logger.info("sql_unlimited_executed", row_count=total_rows)

        if not results:
            return "조회 결과가 없습니다."

        # Format with Flash
        llm = get_flash_client()
        # For very large results, provide summary only
        if total_rows > 500:
            preview = _build_smart_preview(results, query)
        else:
            preview = json.dumps(results[:50], ensure_ascii=False, indent=2, default=str)

        prompt = f"""사용자가 전체 데이터를 요청했습니다. LIMIT 없이 재실행한 결과입니다.

## 사용자 질문
{query}

## 실행 결과 (총 {total_rows}행)
```json
{preview}
```

## 답변 규칙
1. 총 {total_rows}행의 전체 데이터를 조회했다고 안내하세요.
2. 핵심 요약 (상위 항목, 합계 등)을 마크다운 표로 보여주세요.
3. 데이터가 너무 많아 전부 표시할 수 없는 경우 상위 항목 요약 + 전체 통계를 제공하세요.
4. 한국어로 답변하세요.
5. 금액: 1억 이상은 "약 OO.O억원", 1억 미만은 천 단위 쉼표."""

        answer = llm.generate(prompt, temperature=0.05)
        return answer

    except Exception as e:
        logger.error("sql_unlimited_failed", error=str(e))
        return f"전체 데이터 조회 중 오류가 발생했습니다: {str(e)}"


async def run_sql_agent(
    query: str,
    conversation_context: str = "",
    model_type: str = MODEL_GEMINI,
    brand_filter: Optional[str] = None,
    enabled_sources: Optional[list] = None,
    can_view_fi: bool = False,
) -> str:
    """Run the Text-to-SQL agent on a query.

    Args:
        query: Natural language question about data.
        conversation_context: Previous conversation context for reference resolution.
        model_type: "gemini" or "claude" — which LLM to use.
        brand_filter: Comma-separated brand codes (e.g. "SK,CL,CBT" or "UM").
        enabled_sources: List of enabled source keys (e.g. ["BigQuery 제품"]) for table filtering.

    Returns:
        Natural language answer based on SQL results.
    """
    initial_state: AgentState = {
        "query": query,
        "route_type": "text_to_sql",
        "generated_sql": None,
        "sql_valid": None,
        "sql_result": None,
        "retrieved_docs": None,
        "doc_relevance": None,
        "answer": "",
        "needs_retry": False,
        "retry_count": 0,
        "error": None,
        "messages": None,
        "conversation_context": conversation_context,
        "model_type": model_type,
        "brand_filter": brand_filter,
        "can_view_fi": can_view_fi,
        "enabled_sources": enabled_sources,
    }

    import asyncio
    logger.info("sql_agent_started", query=query)

    def _run_sync() -> str:
        state = dict(initial_state)
        state.update(generate_sql(state))
        state.update(validate_sql_node(state))
        if state.get("sql_valid"):
            conv_ctx = state.get("conversation_context", "")
            ck = _cache_key(query, brand_filter) if not conv_ctx else None
            state["generated_sql"] = _enforce_partition_filter(
                state.get("generated_sql", ""), query,
                cache_key=ck, brand_filter=brand_filter,
                can_view_fi=can_view_fi,
                allowed_tables=_allowed_tables_from_sources(enabled_sources, can_view_fi),
            )
            state.update(execute_sql(state))
        state.update(format_answer(state))
        return state.get("answer", "")

    answer = await asyncio.to_thread(_run_sync)
    logger.info("sql_agent_completed", answer_length=len(answer))
    return answer


# --- Fast-answer experiment (BQ_FAST_ANSWER=1): template table first, short LLM insights after ---

_COL_LABELS = {
    "total_revenue": "매출액 (원)", "revenue": "매출액 (원)", "sales": "매출액 (원)",
    "total_quantity": "판매수량 (개)", "total_qty": "판매수량 (개)", "quantity": "판매수량 (개)",
    "total_orders": "주문 건수", "product_name": "제품", "product": "제품",
    "country": "국가", "month": "월", "quarter": "분기", "year": "연도",
    "mall_classification": "채널", "company_name": "판매처", "team_new": "팀",
    "brand": "브랜드", "line": "라인", "date": "날짜", "continent1": "대륙", "continent2": "권역",
}


def _fast_fmt_cell(v) -> str:
    from decimal import Decimal as _D
    if v is None:
        return "-"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float, _D)):
        f = float(v)
        # Monetary/large values: whole numbers read better than stray decimals
        if f == int(f) or abs(f) >= 10000:
            return f"{round(f):,}"
        return f"{f:,.2f}"
    return str(v)


def _fast_table_markdown(results: list, max_rows: int = 15) -> str:
    cols = list(results[0].keys())
    heads = [_COL_LABELS.get(c.lower(), c) for c in cols]
    lines = ["| " + " | ".join(heads) + " |", "|" + "|".join([" :--- "] * len(cols)) + "|"]
    for row in results[:max_rows]:
        lines.append("| " + " | ".join(_fast_fmt_cell(row.get(c)) for c in cols) + " |")
    table = "\n".join(lines)
    if len(results) > max_rows:
        table += f"\n\n*(전체 {len(results)}행 중 상위 {max_rows}행 표시)*"
    return table


def _fast_summary_line(results: list) -> str:
    """One-line computed summary: totals of revenue/qty-like numeric columns."""
    from decimal import Decimal as _D
    parts = []
    cols = list(results[0].keys())
    for c in cols:
        cl = c.lower()
        vals = [r.get(c) for r in results if isinstance(r.get(c), (int, float, _D))]
        if not vals:
            continue
        total = float(sum(float(v) for v in vals))
        if "revenue" in cl or "sales" in cl or "매출" in cl:
            uk = total / 100_000_000
            parts.append(f"총 매출액 **약 {uk:,.1f}억원** ({int(total):,}원)")
        elif "qty" in cl or "quantity" in cl or "수량" in cl:
            parts.append(f"총 판매수량 **{int(total):,}개**")
        elif "order" in cl:
            parts.append(f"총 주문 **{int(total):,}건**")
    return " · ".join(parts)


def _fast_answer_stream(query, sql, results, wiki_context, _t0, _t_gen, _t_exec_start, _t_exec_end):
    """Yield template-rendered table instantly, then short LLM insights.

    Removes the 3-4s full-answer LLM generation from the critical path: the
    user sees title+summary+table the moment BigQuery returns, and only the
    insights/follow-up section is model-generated (short prompt, short output).
    """
    import concurrent.futures
    import time as _time

    today = datetime.now().strftime("%Y-%m-%d")
    _t_tpl = _time.perf_counter()

    title = re.sub(r"\s*(알려줘|보여줘|알려주세요|보여주세요|줄래\??|해줘)\s*$", "", query.strip())
    out_head = f"### 📊 {title}\n\n"
    summary = _fast_summary_line(results)
    if summary:
        out_head += f"#### 요약\n{summary}\n\n"
    out_head += f"#### 상세 데이터\n{_fast_table_markdown(results)}\n"
    yield out_head

    # Chart in background while insights stream
    result_preview = _build_smart_preview(results, query) if len(results) > 100 else json.dumps(
        results[:50], ensure_ascii=False, indent=2, default=str
    )
    _chart_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    chart_future = _chart_executor.submit(
        _try_generate_chart, get_flash_client(), query, sql, result_preview, results
    )

    wiki_block = f"\n## 참고 팩트(지식 위키 — 결과 보완용으로만)\n{wiki_context}\n" if wiki_context else ""
    insight_prompt = f"""사용자 질문: {query}
SQL 결과 ({len(results)}행):
{result_preview}
{wiki_block}
결과 표는 이미 사용자에게 표시되었다. 표/수치 나열을 반복하지 말고 아래 형식만 출력하라:

#### 분석 및 인사이트
- [결과에서 도출한 구체적 인사이트 2~3개, 비율·비교 등 계산 활용]

---
*조회 기준: {today} | 내부 데이터베이스*
> 💡 **이런 것도 물어보세요**
> - [구체적 후속 질문 1]
> - [구체적 후속 질문 2]
> - [구체적 후속 질문 3]

규칙: SQL 결과만 근거로. 금액 1억+ → "약 OO.O억원". 플레이스홀더 출력 금지.
⚠️ 후속 질문은 대괄호([]) 없이 완성된 실제 질문 문장으로 출력하라.
⚠️ 테이블명·프로젝트 ID·컬럼명 노출 금지. 출처는 '내부 데이터베이스'로만.
{TEAM_DISPLAY_RULE}"""

    llm = get_flash_client()
    _t_ins = _time.perf_counter()
    yield "\n"
    for chunk in llm.generate_stream(insight_prompt, temperature=0.1, max_output_tokens=1500):
        yield chunk
    _t_end = _time.perf_counter()

    try:
        chart_markdown = chart_future.result(timeout=8.0)
        if chart_markdown:
            yield f"\n\n#### 시각화\n{chart_markdown}"
    except (concurrent.futures.TimeoutError, Exception):
        pass
    _chart_executor.shutdown(wait=False)

    yield f"\n\n<details><summary>실행된 쿼리</summary>\n\n```sql\n{sql}\n```\n</details>"

    logger.info(
        "bq_fast_timing",
        sql_gen_ms=round((_t_gen - _t0) * 1000),
        bq_exec_ms=round((_t_exec_end - _t_exec_start) * 1000),
        template_ms=round((_t_ins - _t_tpl) * 1000),
        insights_ms=round((_t_end - _t_ins) * 1000),
        total_ms=round((_t_end - _t0) * 1000),
        rows=len(results),
    )


def run_sql_agent_stream(
    query: str,
    conversation_context: str = "",
    model_type: str = MODEL_GEMINI,
    brand_filter: Optional[str] = None,
    enabled_sources: Optional[list] = None,
    wiki_context: str = "",
    can_view_fi: bool = False,
):
    """Streaming version of run_sql_agent. Yields text chunks during format_answer.

    Runs SQL generation + validation + execution synchronously, then streams
    the answer formatting via generate_stream.

    Yields:
        str: text chunks as the answer is generated.
    """
    # Experimental single-session tool-loop path (dev A/B: BQ_TOOL_LOOP=1 on
    # skin1004-dev only — see ecosystem.windows.config.js). Prod keeps the
    # legacy generate→execute→format pipeline below.
    import os
    if os.getenv("BQ_TOOL_LOOP") == "1":
        from app.agents.sql_tool_agent import run_sql_tool_loop_stream
        yield from run_sql_tool_loop_stream(
            query,
            conversation_context=conversation_context,
            brand_filter=brand_filter,
            enabled_sources=enabled_sources,
            wiki_context=wiki_context,
            can_view_fi=can_view_fi,
        )
        return

    initial_state: AgentState = {
        "query": query,
        "route_type": "text_to_sql",
        "generated_sql": None, "sql_valid": None, "sql_result": None,
        "retrieved_docs": None, "doc_relevance": None,
        "answer": "", "needs_retry": False, "retry_count": 0,
        "error": None, "messages": None,
        "conversation_context": conversation_context,
        "model_type": model_type,
        "brand_filter": brand_filter,
        "can_view_fi": can_view_fi,
        "enabled_sources": enabled_sources,
    }

    # Run SQL generation + validation + execution (non-streaming)
    import time as _time
    _t0 = _time.perf_counter()
    state = dict(initial_state)
    state.update(generate_sql(state))
    _t_gen = _time.perf_counter()
    state.update(validate_sql_node(state))
    _t_exec_start = _t_exec_end = _time.perf_counter()
    if state.get("sql_valid"):
        conv_ctx = state.get("conversation_context", "")
        ck = _cache_key(query, brand_filter) if not conv_ctx else None
        state["generated_sql"] = _enforce_partition_filter(
            state.get("generated_sql", ""), query,
            cache_key=ck, brand_filter=brand_filter,
            can_view_fi=can_view_fi,
            allowed_tables=_allowed_tables_from_sources(enabled_sources, can_view_fi),
        )
        _t_exec_start = _time.perf_counter()
        state.update(execute_sql(state))
        _t_exec_end = _time.perf_counter()

    sql = state.get("generated_sql", "")
    results = state.get("sql_result")
    error = state.get("error")

    # Error / empty → yield full message (no streaming needed)
    if error or not results:
        state.update(format_answer(state))
        yield state.get("answer", "")
        return

    # 팀 코드 → 한글 팀명 (fast-answer 의 결정적 표까지 함께 적용된다)
    results = _relabel_team_values(results)

    # Fast-answer experiment (dev A/B: BQ_FAST_ANSWER=1): template table
    # instantly from rows, LLM only for short insights.
    if os.getenv("BQ_FAST_ANSWER") == "1":
        yield from _fast_answer_stream(
            query, sql, results, wiki_context, _t0, _t_gen, _t_exec_start, _t_exec_end
        )
        return

    # Build format prompt (same as format_answer but stream the LLM call)
    from app.core.llm import get_flash_client
    llm = get_flash_client()

    result_preview = _build_smart_preview(results, query) if len(results) > 100 else json.dumps(
        results[:50], ensure_ascii=False, indent=2, default=str
    )
    today = datetime.now().strftime("%Y-%m-%d")
    today_kr = datetime.now().strftime("%Y년 %m월 %d일")
    table_source = _extract_table_sources(sql)

    wiki_block = ""
    if wiki_context:
        wiki_block = (
            "\n## 참고: 이전 대화에서 추출된 관련 팩트 (지식 위키)\n"
            f"{wiki_context}\n"
            "⚠️ 위 팩트는 참고용입니다. SQL 실행 결과가 최신 원본이므로 결과를 우선하되, "
            "팩트가 결과를 보완하거나 맥락을 제공할 때만 인용하세요.\n"
        )

    prompt = f"""## SQL 실행 결과
사용자 질문: {query}
실행된 SQL:
```sql
{sql}
```
결과 ({len(results)}행):
{result_preview}
{wiki_block}
## 답변 형식
### 📊 [제목] → #### 요약 → #### 상세 데이터 (표) → #### 분석 및 인사이트
---
*조회 기준: {today} | 내부 데이터베이스*
> 💡 **이런 것도 물어보세요**
> - [구체적 후속 질문 1 — 다른 기간/국가/제품 등 범위 확장]
> - [구체적 후속 질문 2 — 관련 데이터 심화 분석]
> - [구체적 후속 질문 3 — 다른 관점의 분석]

규칙: SQL 결과만 사용. 금액 1억+→"약 OO.O억원". 표 필수. 인사이트 필수. 조건은 끝에 괄호로.
⚠️ 반드시 구체적인 후속 질문 3개를 생성하세요. "[후속 질문]" 같은 플레이스홀더를 절대 출력하지 마세요.
{_unit_note(sql)}
⚠️ 데이터 출처 보안: 테이블명, 프로젝트 ID, 컬럼명을 답변 본문에 노출하지 마세요. 출처 언급 시 '내부 데이터베이스'라고만 표현하세요.
{TEAM_DISPLAY_RULE}"""

    # Start chart generation in background BEFORE streaming answer
    import concurrent.futures
    _chart_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    chart_llm = get_flash_client()
    chart_future = _chart_executor.submit(_try_generate_chart, chart_llm, query, sql, result_preview, results)

    # Stream answer (chart generates in parallel)
    _t_stream_start = _time.perf_counter()
    _t_first_token = None
    for chunk in llm.generate_stream(prompt, temperature=0.05, max_output_tokens=10000):
        if _t_first_token is None:
            _t_first_token = _time.perf_counter()
        yield chunk
    _t_stream_end = _time.perf_counter()
    logger.info(
        "bq_stage_timing",
        sql_gen_ms=round((_t_gen - _t0) * 1000),
        bq_exec_ms=round((_t_exec_end - _t_exec_start) * 1000),
        answer_first_token_ms=round(((_t_first_token or _t_stream_end) - _t_stream_start) * 1000),
        answer_stream_ms=round((_t_stream_end - _t_stream_start) * 1000),
        total_ms=round((_t_stream_end - _t0) * 1000),
        rows=len(results),
    )

    # Chart should be done by now (ran in parallel with answer streaming)
    try:
        chart_markdown = chart_future.result(timeout=8.0)
        if chart_markdown:
            yield f"\n\n#### 시각화\n{chart_markdown}"
    except (concurrent.futures.TimeoutError, Exception):
        pass
    _chart_executor.shutdown(wait=False)

    yield f"\n\n<details><summary>실행된 쿼리</summary>\n\n```sql\n{sql}\n```\n</details>"
