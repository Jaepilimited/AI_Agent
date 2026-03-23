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

from app.core.llm import MODEL_GEMINI, get_flash_client, get_llm_client
from app.core.prompt_fragments import LANGUAGE_DETECTION_RULE
from app.agents.query_verifier import QueryVerifierAgent
from app.core.security import sanitize_sql, validate_sql
from app.models.state import AgentState

logger = structlog.get_logger(__name__)


# ── SQL Cache ──────────────────────────────────────────────
# Caches (query → SQL) to skip LLM generation for repeated questions.
# In-memory LRU + MariaDB persistence.

_sql_cache: OrderedDict = OrderedDict()  # query_hash → {sql, tables} (LRU order)
_SQL_CACHE_MAX = 500


def _cache_key(query: str, brand_filter: Optional[str] = None) -> str:
    """Normalize query and build cache key hash."""
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    raw = f"{normalized}|{brand_filter or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _extract_tables_from_sql(sql: str) -> set:
    """Extract BigQuery table paths from SQL for cache validation."""
    return set(re.findall(r'`(skin1004-319714\.[^`]+)`', sql))


def _cache_lookup(query_hash: str, allowed_tables: Optional[set] = None) -> Optional[str]:
    """Check cache, then validate cached SQL only uses allowed tables."""
    sql = None

    # 1. In-memory (move to end for LRU tracking)
    if query_hash in _sql_cache:
        _sql_cache.move_to_end(query_hash)
        sql = _sql_cache[query_hash]

    # 2. MariaDB persistent cache
    if sql is None:
        try:
            from app.db.mariadb import fetch_one
            row = fetch_one(
                "SELECT generated_sql FROM sql_cache WHERE query_hash = %s",
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
            "hit_count = hit_count + 1, last_used_at = NOW()",
            (query_hash, query[:500], sql, brand_filter),
        )
    except Exception as e:
        logger.debug("sql_cache_store_failed", error=str(e))

# Load prompts
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# Schema cache: per-table individual caches for lazy loading
_schema_cache_sales: str = ""
_schema_cache_tables: Dict[str, str] = {}  # table_path -> schema text

# Marketing / review / ad tables with keyword triggers for lazy loading
MARKETING_TABLES = [
    ("skin1004-319714.marketing_analysis.integrated_advertising_data", "통합 광고 데이터",
     ["광고", "ad", "advertising", "클릭", "노출", "roas", "cpc", "cpm", "틱톡광고", "페이스북광고", "구글광고", "카카오", "네이버광고"]),
    ("skin1004-319714.marketing_analysis.Integrated_marketing_cost", "통합 마케팅 비용",
     ["마케팅", "marketing", "비용", "roi", "매체", "팀별"]),
    ("skin1004-319714.marketing_analysis.shopify_analysis_sales", "Shopify 판매 데이터",
     ["shopify", "쇼피파이", "반품", "return", "환불"]),
    ("skin1004-319714.Platform_Data.raw_data", "플랫폼 메트릭스",
     ["플랫폼", "platform", "순위", "rank", "가격", "할인", "채널별 제품", "채널별 가격"]),
    ("skin1004-319714.marketing_analysis.influencer_input_ALL_TEAMS", "인플루언서 마케팅",
     ["인플루언서", "influencer", "팔로워", "캠페인", "kol"]),
    ("skin1004-319714.marketing_analysis.amazon_search_analytics_catalog_performance", "아마존 검색 분석",
     ["아마존 검색", "amazon search", "장바구니", "cart", "ctr", "전환율", "asin"]),
    ("skin1004-319714.Review_Data.Amazon_Review", "아마존 리뷰",
     ["아마존 리뷰", "amazon review"]),
    ("skin1004-319714.Review_Data.Qoo10_Review", "큐텐 리뷰",
     ["큐텐 리뷰", "qoo10 review", "큐텐리뷰"]),
    ("skin1004-319714.Review_Data.Shopee_Review", "쇼피 리뷰",
     ["쇼피 리뷰", "shopee review", "쇼피리뷰"]),
    ("skin1004-319714.Review_Data.Smartstore_Review", "스마트스토어 리뷰",
     ["스마트스토어 리뷰", "smartstore review", "스마트스토어리뷰", "네이버 리뷰"]),
    ("skin1004-319714.ad_data.meta data_test", "메타 광고 라이브러리",
     ["메타 광고", "meta ad", "페이스북 광고 라이브러리", "인스타 광고"]),
]

# Backward-compatible flat schema cache (filled on first full load)
_schema_cache: str = ""


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_path = PROMPTS_DIR / filename
    return prompt_path.read_text(encoding="utf-8")


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
    logger.info("generating_sql", query=query, enabled_sources=enabled_sources)

    # Use Flash for SQL generation (Pro is too slow due to thinking mode)
    llm = get_flash_client()
    system_prompt = _load_prompt("sql_generator.txt")

    # Get table schemas (lazy: only include tables relevant to the query)
    global _schema_cache_sales, _schema_cache_tables, _schema_cache
    bq = get_bigquery_client()
    settings = get_settings()

    # enabled_sources → allowed table paths mapping
    _SOURCE_TABLE_MAP = {
        "BigQuery 매출": [settings.sales_table_full_path],
        "BigQuery 제품": [f"{settings.gcp_project_id}.{settings.bq_dataset_sales}.Product"],
        "BQ 광고데이터": ["skin1004-319714.marketing_analysis.integrated_advertising_data"],
        "BQ 마케팅비용": ["skin1004-319714.marketing_analysis.Integrated_marketing_cost"],
        "BQ Shopify": ["skin1004-319714.marketing_analysis.shopify_analysis_sales"],
        "BQ 플랫폼": ["skin1004-319714.Platform_Data.raw_data"],
        "BQ 인플루언서": ["skin1004-319714.marketing_analysis.influencer_input_ALL_TEAMS"],
        "BQ 아마존검색": ["skin1004-319714.marketing_analysis.amazon_search_analytics_catalog_performance"],
        "BQ 아마존리뷰": ["skin1004-319714.Review_Data.Amazon_Review"],
        "BQ 큐텐리뷰": ["skin1004-319714.Review_Data.Qoo10_Review"],
        "BQ 쇼피리뷰": ["skin1004-319714.Review_Data.Shopee_Review"],
        "BQ 스마트스토어": ["skin1004-319714.Review_Data.Smartstore_Review"],
        "BQ 메타광고": ["skin1004-319714.ad_data.meta data_test"],
    }

    # Build allowed_tables set from enabled_sources
    allowed_tables = None  # None = no filtering (all allowed)
    if enabled_sources is not None:
        allowed_tables = set()
        for src in enabled_sources:
            for tp in _SOURCE_TABLE_MAP.get(src, []):
                allowed_tables.add(tp)
        logger.info("sql_table_filter", allowed_count=len(allowed_tables), sources=enabled_sources)

    # ── SQL Cache: skip LLM if cached SQL uses only allowed tables ──
    conv_context = state.get("conversation_context", "")
    if not conv_context:  # Only cache standalone questions (not follow-ups)
        cache_key = _cache_key(query, brand_filter)
        cached_sql = _cache_lookup(cache_key, allowed_tables)
        if cached_sql:
            logger.info("sql_cache_hit", query=query[:60], cache_key=cache_key)
            return {"generated_sql": cached_sql, "error": None}

    # 1) Include primary sales table only if allowed
    include_sales = (allowed_tables is None) or (settings.sales_table_full_path in allowed_tables)
    if include_sales:
        if not _schema_cache_sales:
            try:
                schema = bq.get_table_schema(settings.sales_table_full_path)
                schema_lines = [
                    f"  - {col['name']} ({col['type']}): {col['description']}"
                    for col in schema
                ]
                table_short = settings.sales_table_full_path.rsplit(".", 1)[-1]
                _schema_cache_sales = f"\n\n### 실제 테이블 스키마 ({table_short})\n" + "\n".join(schema_lines)
            except Exception as e:
                logger.warning("schema_fetch_failed", table="SALES_ALL_Backup", error=str(e))
        schema_context = _schema_cache_sales
    else:
        schema_context = ""

    # 1b) Include Product table if allowed
    product_path = f"{settings.gcp_project_id}.{settings.bq_dataset_sales}.Product"
    include_product = (allowed_tables is None and any(kw in query.lower() for kw in ["제품", "product", "sku", "카테고리"])) or \
                      (allowed_tables is not None and product_path in allowed_tables)
    if include_product and product_path not in _schema_cache_tables:
        try:
            tbl_schema = bq.get_table_schema(product_path)
            tbl_lines = [f"  - {col['name']} ({col['type']}): {col['description']}" for col in tbl_schema]
            _schema_cache_tables[product_path] = f"\n\n### 제품 마스터 (Product)\n" + "\n".join(tbl_lines)
        except Exception as e:
            logger.warning("schema_fetch_failed", table="Product", error=str(e))
    if include_product:
        schema_context += _schema_cache_tables.get(product_path, "")

    # 2) Lazy-load: only include marketing tables whose keywords match AND are allowed
    query_lower = query.lower()
    matched_entries = [
        (t[0], t[1], t[2]) for t in MARKETING_TABLES
        if any(kw in query_lower for kw in t[2])
        and (allowed_tables is None or t[0] in allowed_tables)
    ]

    # Parallel-fetch uncached schemas (avoid serial BQ roundtrips)
    uncached = [(tp, lb) for tp, lb, _ in matched_entries if tp not in _schema_cache_tables]
    if uncached:
        def _fetch_schema(table_path, label):
            try:
                tbl_schema = bq.get_table_schema(table_path)
                tbl_lines = [
                    f"  - {col['name']} ({col['type']}): {col['description']}"
                    for col in tbl_schema
                ]
                table_short = table_path.rsplit(".", 1)[-1]
                return table_path, f"\n\n### {label} ({table_short})\n" + "\n".join(tbl_lines)
            except Exception as e:
                logger.warning("schema_fetch_failed", table=table_path, error=str(e))
                return table_path, ""

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(uncached), 5)) as pool:
            futures = [pool.submit(_fetch_schema, tp, lb) for tp, lb in uncached]
            for f in concurrent.futures.as_completed(futures):
                tp, schema_text = f.result()
                _schema_cache_tables[tp] = schema_text

    for table_path, _, _ in matched_entries:
        schema_context += _schema_cache_tables.get(table_path, "")

    logger.info("schema_context_built", total_tables=1 + len(matched_entries), query_matched=len(matched_entries))

    today = datetime.now().strftime("%Y-%m-%d")
    date_context = f"\n\n## 오늘 날짜\n{today} (사용자가 '이번 달', '지난 달', '올해' 등 상대적 날짜를 사용하면 이 날짜를 기준으로 계산하세요)"

    # Include conversation context if available
    conv_context = state.get("conversation_context", "")
    conv_section = ""
    if conv_context:
        conv_section = f"\n\n## 이전 대화 맥락\n{conv_context}\n\n위 대화 맥락을 참고하여 사용자의 현재 질문에 포함된 '그거', '아까', '다시', '2월은?' 같은 참조를 이해하세요."

    # Brand filter injection: only if user has a group filter assigned
    brand_filter = state.get("brand_filter")
    brand_section = ""
    if brand_filter:
        brands = [b.strip() for b in brand_filter.split(",") if b.strip()]
        brand_in = ", ".join(f"'{b}'" for b in brands)
        brand_section = (
            f"\n\n## ⚠️ 브랜드 필터 (최우선 적용)\n"
            f"매출/제품 관련 SQL에 반드시 `WHERE Brand IN ({brand_in})` 조건을 추가하세요.\n"
        )
    # No brand_filter (admin/unassigned) → SQL 프롬프트의 기본 규칙 따름 (ETC 제외 등)

    sql_only_reminder = "\n\n⛔ 최종 지시: SELECT로 시작하는 BigQuery SQL만 출력하라. 설명/안내/되묻기 텍스트 출력 시 시스템 오류 발생. 질문이 모호하면 합리적 기본값(최근 3개월, TOP 10 등)으로 SQL 생성."
    full_prompt = f"{system_prompt}{schema_context}{date_context}{conv_section}{brand_section}\n\n## 사용자 질문\n{query}{sql_only_reminder}"

    try:
        sql = llm.generate(full_prompt, temperature=0.0, max_output_tokens=8192)
        sql = sanitize_sql(sql)

        # Retry once if LLM returned text instead of SQL
        if not sql or len(sql) < 10:
            logger.warning("sql_generation_empty_retry", query=query[:80])
            retry_prompt = (
                full_prompt
                + "\n\n⛔ 이전 시도에서 SQL이 아닌 텍스트를 출력했습니다. "
                "반드시 SELECT로 시작하는 BigQuery SQL만 출력하세요. "
                "설명, 안내, 되묻기 텍스트 절대 금지! SQL만!"
            )
            sql = llm.generate(retry_prompt, temperature=0.1, max_output_tokens=8192)
            sql = sanitize_sql(sql)
            if sql:
                logger.info("sql_generation_retry_success", sql=sql[:200])

        logger.info("sql_generated", sql=sql[:200])

        # Store in cache for future use (only standalone queries)
        if not conv_context:
            cache_key = _cache_key(query, brand_filter)
            _cache_store(cache_key, query, sql, brand_filter)

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

    is_valid, error_msg = validate_sql(sql)

    if not is_valid:
        logger.warning("sql_validation_failed", error=error_msg, sql=sql[:200])
        return {"sql_valid": False, "error": f"SQL 검증 실패: {error_msg}"}

    logger.info("sql_validation_passed", sql=sql[:200])

    # QueryVerifier: fire-and-forget (non-blocking, log-only)
    # Previously blocked up to 15s — now runs in background thread
    try:
        import asyncio
        import threading

        verifier = QueryVerifierAgent()
        schema_info = _schema_cache or ""
        _sql_ref = sql  # capture for closure

        def _verify_bg():
            try:
                vr = asyncio.run(verifier.verify(_sql_ref, schema_info))
                if isinstance(vr, dict) and not vr.get("valid", True):
                    logger.info("query_verifier_bg_issue", errors=vr.get("errors", []))
                else:
                    logger.debug("query_verifier_bg_passed")
            except Exception as ex:
                logger.debug("query_verifier_bg_skipped", error=str(ex))

        threading.Thread(target=_verify_bg, daemon=True).start()
    except Exception as e:
        logger.debug("query_verifier_launch_failed", error=str(e))

    return {"sql_valid": True, "error": None}


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

    logger.info("executing_sql", sql=sql[:200])

    try:
        bq = get_bigquery_client()
        results = bq.execute_query(sql, timeout=45.0, max_rows=1000)
        logger.info("sql_executed", row_count=len(results))
        return {"sql_result": results, "error": None}
    except Exception as e:
        logger.error("sql_execution_failed", error=str(e))
        return {"sql_result": None, "error": f"SQL 실행 실패: {str(e)}"}


# Friendly display names for BigQuery tables
_TABLE_DISPLAY_NAMES = {
    "SALES_ALL_Backup": "통합 매출 (SALES_ALL)",
    "integrated_advertising_data": "통합 광고 데이터",
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
    if error:
        return {
            "answer": f"죄송합니다. 질문을 처리하는 중 오류가 발생했습니다.\n\n오류: {error}"
        }

    if not results:
        # Build context hints for valid column values referenced in SQL
        _value_hints = []
        sql_upper = (sql or "").upper()
        if "TEAM_NEW" in sql_upper:
            _value_hints.append(
                "Team_NEW 유효 값: GM_EAST1, GM_EAST2, GM_Ecomm, GM_MKT, CBT, JBT, KBT, BCM, B2B1, B2B2, DD_DT1, DD_DT2, OP, 기타 (⚠️ GM_WEST는 존재하지 않음!)"
            )
        if "COUNTRY" in sql_upper:
            _value_hints.append(
                "Country는 한국어 값: 미국, 인도네시아, 말레이시아, 필리핀, 일본, 중국, 한국, 태국, 베트남, 싱가포르, 호주, 독일 등"
            )
        _hints_text = "\n".join(_value_hints)

        # Try Flash LLM for helpful empty-result message (with timeout), else template
        try:
            empty_llm = get_flash_client()
            empty_prompt = f"""사용자가 "{query}"라고 질문했고, SQL 결과가 0행입니다:
```sql
{sql}
```
{f"유효 값: {_hints_text}" if _hints_text else ""}
간결하게: 1) 해당 조건의 데이터가 없다는 안내 2) 왜 0건인지 가능한 원인 (조건 불일치, 해당 기간 데이터 없음 등) 3) 구체적인 대안 질문 2개. 한국어. ⚠️ "조회하지 못했습니다" 표현 사용 금지! "해당 조건의 데이터가 존재하지 않습니다" 사용."""
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                f = pool.submit(empty_llm.generate, empty_prompt, None, 0.3)
                answer = f.result(timeout=2.0)
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

    # Truncate long text fields (review content etc.) to reduce prompt size
    def _truncate_row(row, max_text_len=80):
        truncated = {}
        for k, v in row.items():
            if isinstance(v, str) and len(v) > max_text_len:
                truncated[k] = v[:max_text_len] + "..."
            else:
                truncated[k] = v
        return truncated

    if len(results) > 100:
        try:
            result_preview = _build_smart_preview(results, query)
        except Exception as e:
            logger.warning("smart_preview_failed_fallback", error=str(e))
            preview_rows = [_truncate_row(r) for r in results[:15]]
            result_preview = json.dumps(preview_rows, ensure_ascii=False, indent=2, default=str)
    elif _is_timeseries and len(results) <= 60:
        # Time-series: send ALL rows so LLM can show full table & chart (cap at 60)
        preview_rows = [_truncate_row(r) for r in results]
        result_preview = json.dumps(preview_rows, ensure_ascii=False, indent=2, default=str)
    elif _is_timeseries and len(results) <= 100:
        # Grouped time-series (e.g. 월별 몰별): pivot to compact table
        result_preview = _try_pivot_timeseries(results, query)
        if not result_preview:
            preview_rows = [_truncate_row(r) for r in results[:60]]
            result_preview = json.dumps(preview_rows, ensure_ascii=False, indent=2, default=str)
    else:
        preview_rows = [_truncate_row(r) for r in results[:15]]
        result_preview = json.dumps(preview_rows, ensure_ascii=False, indent=2, default=str)

    # Hard cap on preview size to keep LLM prompt manageable (max ~5KB)
    if len(result_preview) > 5000:
        preview_rows = [_truncate_row(r) for r in results[:8]]
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

### 📊 [질문에 맞는 제목]
#### 요약
[1-3문장으로 핵심 결론. 가장 중요한 수치는 **굵게** 표시]
#### 상세 데이터
[3행 이상의 비교 데이터는 반드시 마크다운 표로 정리. 숫자는 오른쪽 정렬(---:)]
#### 분석 및 인사이트
[2-3개 핵심 포인트를 bullet으로 작성. 비중/변화율/추세/비교 포함]
---
*조회 기준: {today} | 데이터소스: {table_source}*
> 💡 **이런 것도 물어보세요**
> - [후속 질문 3개]

## 작성 규칙
- SQL 결과 데이터만 사용 (외부 정보 절대 금지)
- 금액: 1억 이상 → "약 OO.O억원", 1억 미만 → 천 단위 쉼표. 퍼센트 소수점 1자리
- 3행 이상 비교 → 마크다운 표 필수. 시계열은 전체 행 표시 (생략 금지)
- 제품명(SET) 영어 원본 그대로 (한국어 번역 금지)
- 단순 수치 1개만 → "상세 데이터" 생략, 요약만
- 기간 부족 시 첫 줄에 ⚠️ 표시. 질문 범위와 데이터 범위 불일치 시 명시
- 비즈니스 인사이트 필수: 비중, 변화율, 추세, 집중도, 비교 관점
- 조건 설명(브랜드, 기간)은 답변 끝에 짧게 괄호로
"""

    try:
        # Answer generation: foreground. Chart: parallel with short timeout.
        # User sees answer immediately; chart appended only if ready fast enough.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            answer_future = executor.submit(llm.generate, prompt, None, 0.3, 8192)
            chart_llm = get_flash_client()
            chart_future = executor.submit(
                _try_generate_chart, chart_llm, query, sql, result_preview, results
            )

            answer = answer_future.result()
            # Give chart up to 3s after answer is ready; skip if slow
            try:
                chart_markdown = chart_future.result(timeout=3.0)
            except concurrent.futures.TimeoutError:
                chart_markdown = None
                logger.info("chart_generation_skipped_timeout")

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

    try:
        config_prompt = get_chart_config_prompt(query, sql, result_preview, len(results))
        config_json = llm.generate_json(config_prompt)
        logger.info("chart_config_raw", config_json=config_json[:500])
        config = json.loads(config_json)

        if not config.get("needs_chart"):
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
        logger.error("chart_generation_skipped", error=str(e), error_type=type(e).__name__)
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
    is_valid, error_msg = validate_sql(unlimited_sql)
    if not is_valid:
        return f"SQL 검증 실패: {error_msg}"

    try:
        bq = get_bigquery_client()
        results = bq.execute_query(unlimited_sql, timeout=60.0, max_rows=100000)
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

        answer = llm.generate(prompt, temperature=0.3)
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
        "web_search_results": None,
        "answer": "",
        "needs_retry": False,
        "retry_count": 0,
        "error": None,
        "messages": None,
        "conversation_context": conversation_context,
        "model_type": model_type,
        "brand_filter": brand_filter,
        "enabled_sources": enabled_sources,
    }

    logger.info("sql_agent_started", query=query)
    result = sql_agent.invoke(initial_state)
    logger.info("sql_agent_completed", answer_length=len(result.get("answer", "")))
    return result["answer"]


def run_sql_agent_stream(
    query: str,
    conversation_context: str = "",
    model_type: str = MODEL_GEMINI,
    brand_filter: Optional[str] = None,
    enabled_sources: Optional[list] = None,
):
    """Streaming version of run_sql_agent. Yields text chunks during format_answer.

    Runs SQL generation + validation + execution synchronously, then streams
    the answer formatting via generate_stream.

    Yields:
        str: text chunks as the answer is generated.
    """
    initial_state: AgentState = {
        "query": query,
        "route_type": "text_to_sql",
        "generated_sql": None, "sql_valid": None, "sql_result": None,
        "retrieved_docs": None, "doc_relevance": None, "web_search_results": None,
        "answer": "", "needs_retry": False, "retry_count": 0,
        "error": None, "messages": None,
        "conversation_context": conversation_context,
        "model_type": model_type,
        "brand_filter": brand_filter,
        "enabled_sources": enabled_sources,
    }

    # Run SQL generation + validation + execution (non-streaming)
    state = dict(initial_state)
    state.update(generate_sql(state))
    state.update(validate_sql_node(state))
    if state.get("sql_valid"):
        state.update(execute_sql(state))

    sql = state.get("generated_sql", "")
    results = state.get("sql_result")
    error = state.get("error")

    # Error / empty → yield full message (no streaming needed)
    if error or not results:
        state.update(format_answer(state))
        yield state.get("answer", "")
        return

    # Build format prompt (same as format_answer but stream the LLM call)
    from app.core.llm import get_flash_client
    llm = get_flash_client()

    result_preview = _build_smart_preview(results) if len(results) > 100 else json.dumps(
        results[:50], ensure_ascii=False, indent=2, default=str
    )
    today = datetime.now().strftime("%Y-%m-%d")
    today_kr = datetime.now().strftime("%Y년 %m월 %d일")
    table_source = _extract_table_sources(sql)

    prompt = f"""## SQL 실행 결과
사용자 질문: {query}
실행된 SQL:
```sql
{sql}
```
결과 ({len(results)}행):
{result_preview}

## 답변 형식
### 📊 [제목] → #### 요약 → #### 상세 데이터 (표) → #### 분석 및 인사이트
---
*조회 기준: {today} | {table_source}*
> 💡 후속 질문 3개

규칙: SQL 결과만 사용. 금액 1억+→"약 OO.O억원". 표 필수. 인사이트 필수. 조건은 끝에 괄호로."""

    # Stream answer
    for chunk in llm.generate_stream(prompt, temperature=0.3, max_output_tokens=8192):
        yield chunk

    # Append chart + SQL details after streaming
    try:
        chart_llm = get_flash_client()
        chart_markdown = _try_generate_chart(chart_llm, query, sql, result_preview, results)
        if chart_markdown:
            yield f"\n\n#### 시각화\n{chart_markdown}"
    except Exception:
        pass

    yield f"\n\n<details><summary>실행된 쿼리</summary>\n\n```sql\n{sql}\n```\n</details>"
