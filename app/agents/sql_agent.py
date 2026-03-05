"""Text-to-SQL Agent using LangGraph.

Workflow: generate_sql → validate_sql → execute_sql → format_answer
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import structlog
from langgraph.graph import END, StateGraph

from app.config import get_settings
from app.core.bigquery import get_bigquery_client
import concurrent.futures

from app.core.llm import MODEL_GEMINI, get_flash_client, get_llm_client
from app.agents.query_verifier import QueryVerifierAgent
from app.core.security import sanitize_sql, validate_sql
from app.models.state import AgentState

logger = structlog.get_logger(__name__)

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
    logger.info("generating_sql", query=query)

    # Use Flash for SQL generation (Pro is too slow due to thinking mode)
    llm = get_flash_client()
    system_prompt = _load_prompt("sql_generator.txt")

    # Get table schemas (lazy: only include tables relevant to the query)
    global _schema_cache_sales, _schema_cache_tables, _schema_cache
    bq = get_bigquery_client()
    settings = get_settings()

    # 1) Always include primary sales table
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

    # 2) Lazy-load: only include marketing tables whose keywords match the query
    query_lower = query.lower()
    matched_tables = 0
    for table_entry in MARKETING_TABLES:
        table_path, label, keywords = table_entry[0], table_entry[1], table_entry[2]
        # Check if any keyword matches the query
        if not any(kw in query_lower for kw in keywords):
            continue
        matched_tables += 1
        # Fetch and cache individual table schema
        if table_path not in _schema_cache_tables:
            try:
                tbl_schema = bq.get_table_schema(table_path)
                tbl_lines = [
                    f"  - {col['name']} ({col['type']}): {col['description']}"
                    for col in tbl_schema
                ]
                table_short = table_path.rsplit(".", 1)[-1]
                _schema_cache_tables[table_path] = f"\n\n### {label} ({table_short})\n" + "\n".join(tbl_lines)
            except Exception as e:
                logger.warning("schema_fetch_failed", table=table_path, error=str(e))
                _schema_cache_tables[table_path] = ""
        schema_context += _schema_cache_tables.get(table_path, "")

    logger.info("schema_context_built", total_tables=1 + matched_tables, query_matched=matched_tables)

    today = datetime.now().strftime("%Y-%m-%d")
    date_context = f"\n\n## 오늘 날짜\n{today} (사용자가 '이번 달', '지난 달', '올해' 등 상대적 날짜를 사용하면 이 날짜를 기준으로 계산하세요)"

    # Include conversation context if available
    conv_context = state.get("conversation_context", "")
    conv_section = ""
    if conv_context:
        conv_section = f"\n\n## 이전 대화 맥락\n{conv_context}\n\n위 대화 맥락을 참고하여 사용자의 현재 질문에 포함된 '그거', '아까', '다시', '2월은?' 같은 참조를 이해하세요."

    full_prompt = f"{system_prompt}{schema_context}{date_context}{conv_section}\n\n## 사용자 질문\n{query}"

    try:
        sql = llm.generate(full_prompt, temperature=0.0)
        sql = sanitize_sql(sql)
        logger.info("sql_generated", sql=sql[:200])
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
        results = bq.execute_query(sql, timeout=45.0, max_rows=10000)
        logger.info("sql_executed", row_count=len(results))
        return {"sql_result": results, "error": None}
    except Exception as e:
        logger.error("sql_execution_failed", error=str(e))
        return {"sql_result": None, "error": f"SQL 실행 실패: {str(e)}"}


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

        # Try Flash LLM for helpful empty-result message, else template fallback
        try:
            empty_llm = get_flash_client()
            empty_prompt = f"""사용자가 "{query}"라고 질문했고, 다음 SQL을 실행했지만 결과가 0행이었습니다:

```sql
{sql}
```

{f"## 참고: 유효한 컬럼 값{chr(10)}{_hints_text}" if _hints_text else ""}

다음 규칙으로 답변하세요:
1. 해당 조건의 데이터가 없다고 간결하게 안내 (1문장). SQL에 사용된 값이 유효 값 목록에 없으면 **"해당 값은 존재하지 않습니다"**라고 명확히 안내
2. SQL에서 사용된 필터 조건(국가, 채널, 날짜, 제품명, 팀 등)을 확인하고, 어떤 조건이 문제인지 설명. 유효 값 목록이 있으면 가장 유사한 값을 추천
3. 비슷한 데이터를 조회할 수 있는 대안 질문 2-3개 제시
4. 한국어로 답변하세요."""
            answer = empty_llm.generate(empty_prompt, temperature=0.3)
            if answer and len(answer) > 30:
                return {"answer": answer}
        except Exception:
            pass
        # Template fallback — more helpful than a single line
        return {
            "answer": (
                "해당 조건의 데이터가 조회되지 않았습니다.\n\n"
                "**확인 사항:**\n"
                "- 국가명이나 채널명(쇼피/아마존/틱톡/라자다 등)이 정확한지 확인\n"
                "- 해당 국가에 해당 채널이 존재하는지 확인 (예: 일본 아마존은 데이터 없음)\n"
                "- 조회 기간을 넓혀서 다시 질문\n\n"
                "**예시 질문:**\n"
                "- \"2024년 미국 아마존 월별 매출 알려줘\"\n"
                "- \"베트남 쇼피 2025년 매출 알려줘\"\n"
                "- \"태국 라자다 분기별 매출 추이\""
            )
        }

    # Use Flash for answer formatting (faster, 3-5s vs 15-25s with Pro)
    llm = get_flash_client()

    # Limit result preview for prompt — smart strategy based on result size
    _ts_keywords = ("월별", "주차별", "주별", "일별", "분기별", "추이", "트렌드", "변동")
    _is_timeseries = any(kw in query for kw in _ts_keywords)

    if len(results) > 100:
        try:
            result_preview = _build_smart_preview(results, query)
        except Exception as e:
            logger.warning("smart_preview_failed_fallback", error=str(e))
            result_preview = json.dumps(results[:20], ensure_ascii=False, indent=2, default=str)
    elif _is_timeseries and len(results) <= 100:
        # Time-series: send ALL rows so LLM can show full table & chart
        result_preview = json.dumps(results, ensure_ascii=False, indent=2, default=str)
    else:
        result_preview = json.dumps(results[:20], ensure_ascii=False, indent=2, default=str)

    today = datetime.now().strftime("%Y-%m-%d")
    today_kr = datetime.now().strftime("%Y년 %m월 %d일")

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
    if len(results) > 20:
        _preview_warning = f"⚠️ 위 JSON은 전체 {len(results)}행 중 상위 프리뷰입니다. 나머지 데이터도 존재하므로 '20일까지만 존재' 등 프리뷰 기반으로 데이터 범위를 단정하지 마세요."

    _limit_warning = ""
    if len(results) >= 10000:
        _limit_warning = (
            f"⚠️ 결과가 {len(results)}행으로 LIMIT 10,000에 도달했습니다. "
            "전체 데이터 중 일부만 포함되어 있습니다. "
            '답변 마지막에 반드시 다음 경고를 추가하세요: '
            '\'> ⚠️ 조회 결과가 10,000행 제한에 도달하여 일부 데이터만 표시되었습니다. '
            '전체 데이터가 필요하시면 **"전체 데이터 줘"**라고 말씀해주세요.\''
        )

    _result_header = f"총 {len(results)}행"
    if len(results) > 20:
        _result_header += f", 아래는 상위 {min(20, len(results))}건 프리뷰"

    prompt = f"""다음은 사용자의 질문과 BigQuery 실행 결과입니다.
결과를 바탕으로 사용자에게 **구조화된 분석 보고서** 형태로 한국어 답변을 작성하세요.

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
[2-3개 핵심 포인트를 bullet으로. 주목할 만한 인사이트는 `> ` 인용 형식으로 강조]

---
*조회 기준: {today} | SKIN1004 내부 매출 데이터 (BigQuery)*

## 작성 규칙
1. **반드시** 위 섹션 구조(요약 → 상세 데이터 → 분석 및 인사이트)를 따르세요.
2. 핵심 수치는 **굵게** 표시하세요.
3. 금액 표기: 1억 이상은 "약 OO.O억원", 1억 미만은 천 단위 쉼표 (예: 5,312만원).
4. 3행 이상 비교 데이터는 반드시 마크다운 표(`| 컬럼 | ... |`)로 정리하세요.
5. 시계열 데이터(월별, 주차별, 일별, 분기별 추이)는 **전체 행을 마크다운 표로** 보여주세요 (절대 생략 금지). 시계열이 아닌 랭킹/비교 데이터가 20행 초과 시에만 상위 항목 표 + 나머지 요약.
6. **제품명(SET/product 컬럼)은 영어 원본 그대로** 표시 (한국어 번역 금지).
7. 단순 수치 1개만 반환되는 경우(합계 등)에는 "상세 데이터" 섹션을 생략하고 요약만 작성하세요.

## ⚠️ 질문-데이터 정합성 검증 (최우선 규칙)
**답변 전에 반드시 아래를 확인하세요:**
8. 사용자가 묻는 **기간 범위**와 실제 데이터의 기간을 비교하세요.
   - "2026년 매출"을 물었는데 데이터가 1~2월뿐이면: 요약 첫 줄에 "⚠️ 2026년은 현재 1~2월 데이터만 존재합니다 (오늘: {today_kr})" 반드시 명시
   - "연간 매출"을 물었는데 일부 월만 있으면: 어떤 월까지 데이터가 있는지 명시
   - 미래 날짜 질문이면: "해당 기간은 아직 데이터가 없습니다" 안내
9. 사용자가 묻는 **범위**(국가, 채널, 제품 등)와 실제 데이터 범위를 비교하세요.
   - "전체 국가"를 물었는데 3개국만 있으면: 포함된 국가를 명시
   - "아마존 매출"을 물었는데 아마존 데이터가 없으면: 데이터 없음을 명시
10. **절대 질문과 다른 범위의 데이터를 질문에 대한 답인 것처럼 제시하지 마세요.**
    - 사용자가 "2026년 매출"을 물었으면 "2월 매출"이라고 제목 바꿔 답하지 마세요.
    - 반드시 원래 질문을 존중하고, 데이터가 부족하면 부족하다고 명시하세요.
"""

    try:
        # Run answer generation and chart generation in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            answer_future = executor.submit(llm.generate, prompt, None, 0.3)
            # Use Flash for chart config generation (faster, Pro too slow)
            chart_llm = get_flash_client()
            chart_future = executor.submit(
                _try_generate_chart, chart_llm, query, sql, result_preview, results
            )

            answer = answer_future.result()
            chart_markdown = chart_future.result()

        if chart_markdown:
            # Insert chart before "분석 및 인사이트" section if found
            insight_markers = ["#### 분석 및 인사이트", "#### 분석", "### 분석 및 인사이트", "### 분석"]
            inserted = False
            for marker in insight_markers:
                if marker in answer:
                    answer = answer.replace(marker, f"#### 시각화\n{chart_markdown}\n\n{marker}", 1)
                    inserted = True
                    break
            if not inserted:
                answer = answer + f"\n\n#### 시각화\n{chart_markdown}"

        # Embed SQL reference for full-data re-execution (hidden in details tag)
        if len(results) >= 10000:
            answer += f"\n\n<details><summary>실행된 쿼리</summary>\n\n```sql\n{sql}\n```\n</details>"

        return {"answer": answer}
    except Exception as e:
        logger.error("answer_formatting_failed", error=str(e))
        # Fallback: return raw results
        return {
            "answer": f"SQL 실행 결과 ({len(results)}행):\n```json\n{result_preview}\n```"
        }


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
    top_rows = sorted_rows[:20]

    preview = {
        "summary": "\n".join(summary_parts),
        "top_20_by_revenue": top_rows,
    }
    return json.dumps(preview, ensure_ascii=False, indent=2, default=str)


def _try_generate_chart(llm, query: str, sql: str, result_preview: str, results: list) -> str:
    """Attempt to generate a chart for the SQL results.

    Returns markdown/HTML string with interactive chart, or empty string.
    """
    from app.core.chart import generate_chart, get_chart_config_prompt

    try:
        config_prompt = get_chart_config_prompt(query, sql, result_preview, len(results))
        config_json = llm.generate_json(config_prompt)
        logger.info("chart_config_raw", config_json=config_json[:500])
        config = json.loads(config_json)

        if not config.get("needs_chart"):
            logger.info("chart_not_needed", config=config)
            return ""

        logger.info("chart_requested", chart_type=config.get("chart_type"), group_column=config.get("group_column"))

        # Generate PNG chart
        filename = generate_chart(config, results)
        if filename:
            # Use relative path so charts work from any host (localhost, IP, domain)
            chart_url = f"/static/charts/{filename}"
            logger.info("chart_url_generated", url=chart_url)
            return f"\n\n![chart]({chart_url})"
        logger.warning("chart_generate_returned_none")
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
) -> str:
    """Run the Text-to-SQL agent on a query.

    Args:
        query: Natural language question about data.
        conversation_context: Previous conversation context for reference resolution.
        model_type: "gemini" or "claude" — which LLM to use.

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
    }

    logger.info("sql_agent_started", query=query)
    result = sql_agent.invoke(initial_state)
    logger.info("sql_agent_completed", answer_length=len(result.get("answer", "")))
    return result["answer"]
