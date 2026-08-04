"""Experimental single-session tool-use BigQuery agent (dev-gated).

Enabled via BQ_TOOL_LOOP=1 (checked in run_sql_agent_stream). Replaces the
generate→execute→format pipeline (two serial LLM calls with separate prompts)
with one streaming Gemini Flash session holding a run_bigquery_sql tool:
the model streams intro text immediately, calls the tool, and continues
streaming the final answer in the same session. On SQL errors the model
sees the error message as the tool result and can self-correct.

Safety parity with the legacy path: every tool call goes through
sanitize_sql → validate_sql → _enforce_partition_filter before execution.
"""

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.core.bigquery import get_bigquery_client
from app.core.llm import _GEMINI_SEM, get_flash_client
from app.core.security import FI_ACCESS_DENIED_MESSAGE, sanitize_sql, validate_sql

logger = structlog.get_logger(__name__)

_TOOL_NAME = "run_bigquery_sql"
_MAX_TOOL_TURNS = 3  # tool-call rounds; +1 final answer turn


def _json_safe_rows(rows: List[Dict[str, Any]], limit: int = 50) -> list:
    """Rows → JSON-serializable (Decimal/date → str) for the function response."""
    return json.loads(json.dumps(rows[:limit], ensure_ascii=False, default=str))


def _execute_tool_call(
    sql_arg: str,
    query: str,
    brand_filter: Optional[str],
    allowed_tables: Optional[set],
    can_view_fi: bool = False,
) -> Tuple[Dict[str, Any], str, list]:
    """Validate + partition-guard + execute one tool call.

    Returns (payload_for_model, executed_sql, rows). Errors are returned in
    the payload (not raised) so the model can read them and retry.
    """
    from app.agents.sql_agent import _build_smart_preview, _enforce_partition_filter

    sql = sanitize_sql(sql_arg or "")
    is_valid, err = validate_sql(sql, allowed_tables=allowed_tables)
    if not is_valid:
        logger.warning("tool_sql_validation_failed", error=str(err)[:200], sql=sql[:200])
        if err == FI_ACCESS_DENIED_MESSAGE:
            return {"error": FI_ACCESS_DENIED_MESSAGE, "access_denied": True}, "", []
        return {"error": f"SQL 검증 실패: {err}. 규칙에 맞게 SQL을 수정해 다시 호출하세요."}, "", []

    sql = _enforce_partition_filter(
        sql,
        query,
        cache_key=None,
        brand_filter=brand_filter,
        can_view_fi=can_view_fi,
        allowed_tables=allowed_tables,
    )
    is_valid, err = validate_sql(sql, allowed_tables=allowed_tables)
    if not is_valid:
        logger.warning("tool_sql_pre_execution_validation_failed", error=str(err)[:200], sql=sql[:200])
        if err == FI_ACCESS_DENIED_MESSAGE:
            return {"error": FI_ACCESS_DENIED_MESSAGE, "access_denied": True}, "", []
        return {"error": f"SQL 검증 실패: {err}. 규칙에 맞게 SQL을 수정해 다시 호출하세요."}, "", []

    try:
        bq = get_bigquery_client()
        rows = bq.execute_query(sql, timeout=120.0, max_rows=1000)
    except Exception as e:
        logger.warning("tool_sql_execution_failed", error=str(e)[:200], sql=sql[:200])
        return {
            "error": f"SQL 실행 실패: {str(e)[:500]}. 오류 메시지를 참고해 SQL을 수정해 다시 호출하세요."
        }, sql, []

    logger.info("tool_sql_executed", row_count=len(rows), sql=sql[:200])
    if len(rows) > 100:
        payload: Dict[str, Any] = {
            "row_count": len(rows),
            "result_preview": _build_smart_preview(rows, query),
            "note": "행이 많아 집계 요약 + 상위 샘플만 제공됨",
        }
    else:
        payload = {"row_count": len(rows), "rows": _json_safe_rows(rows)}
    payload["executed_sql"] = sql
    return payload, sql, rows


def _build_answer_rules() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""

## 답변 형식 (도구 결과 수신 후 반드시 준수)
### 📊 [제목] → #### 요약 → #### 상세 데이터 (표) → #### 분석 및 인사이트
답변 마지막에 아래 블록을 포함:
---
*조회 기준: {today} | 내부 데이터베이스*
> 💡 **이런 것도 물어보세요**
> - [구체적 후속 질문 1 — 다른 기간/국가/제품 등 범위 확장]
> - [구체적 후속 질문 2 — 관련 데이터 심화 분석]
> - [구체적 후속 질문 3 — 다른 관점의 분석]

규칙: SQL 결과만 사용. 금액 1억+→"약 OO.O억원". 표 필수. 인사이트 필수. 조건은 끝에 괄호로.
⚠️ 반드시 구체적인 후속 질문 3개를 생성하세요. "[후속 질문]" 같은 플레이스홀더를 절대 출력하지 마세요.
⚠️ 데이터 출처 보안: 테이블명, 프로젝트 ID, 컬럼명을 답변 본문에 노출하지 마세요. 출처 언급 시 '내부 데이터베이스'라고만 표현하세요."""


def run_sql_tool_loop_stream(
    query: str,
    conversation_context: str = "",
    brand_filter: Optional[str] = None,
    enabled_sources: Optional[list] = None,
    wiki_context: str = "",
    can_view_fi: bool = False,
):
    """Single-session tool-use replacement for run_sql_agent_stream.

    Yields:
        str: text chunks (intro + final answer + chart + SQL details).
    """
    from google.genai import types

    from app.agents.sql_agent import (
        _allowed_tables_from_sources,
        _build_date_context,
        _build_brand_section,
        _build_schema_context,
        _build_smart_preview,
        _load_prompt,
        _try_generate_chart,
    )

    t0 = time.perf_counter()
    client = get_flash_client()

    allowed_tables = _allowed_tables_from_sources(enabled_sources, can_view_fi)
    schema_context = _build_schema_context(query, allowed_tables)

    system_instruction = (
        _load_prompt("sql_generator.txt", can_view_fi=can_view_fi)
        + schema_context
        + "\n\n## 도구 사용 지침 (최우선)\n"
        "위 규칙에 따라 SQL을 작성하되, SQL을 텍스트로 출력하지 말고 반드시 "
        f"{_TOOL_NAME} 도구로 실행하라.\n"
        f"1. ⛔ 절대 규칙: {_TOOL_NAME} 호출 전에 반드시 짧은 안내 문장 한 줄을 먼저 텍스트로 출력하라. "
        "예: \"🔍 2025년 6월 일본 매출 데이터를 조회하겠습니다...\" "
        "(한국어, 한 문장, SQL/테이블명 언급 금지). 안내 문장 없이 곧바로 도구를 호출하는 것은 금지.\n"
        f"2. 안내 문장 직후 {_TOOL_NAME}을 호출하라.\n"
        "3. 결과를 받으면 아래 답변 형식으로 최종 답변을 작성하라.\n"
        "4. error가 반환되면 오류 메시지를 참고해 SQL을 수정해 다시 호출하라 (이때 추가 안내 문장은 불필요).\n"
        + _build_answer_rules()
    )

    user_blocks = [_build_date_context().strip()]
    brand_section = _build_brand_section(brand_filter)
    if brand_section:
        user_blocks.append(brand_section.strip())
    if conversation_context:
        user_blocks.append(
            "## 이전 대화 맥락\n" + conversation_context
            + "\n위 맥락을 참고해 '그거', '아까', '다시' 같은 참조를 해석하세요."
        )
    if wiki_context:
        user_blocks.append(
            "## 참고: 지식 위키 팩트\n" + wiki_context
            + "\nSQL 실행 결과가 최신 원본이므로 결과를 우선하세요."
        )
    user_blocks.append("## 사용자 질문\n" + query)

    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text="\n\n".join(user_blocks))])
    ]

    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=_TOOL_NAME,
                description="BigQuery에서 SELECT SQL을 실행하고 결과 행(JSON)을 반환한다.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "sql": types.Schema(
                            type=types.Type.STRING,
                            description="실행할 BigQuery SELECT SQL (완전한 단일 쿼리)",
                        )
                    },
                    required=["sql"],
                ),
            )
        ]
    )

    config = types.GenerateContentConfig(
        temperature=0.05,
        max_output_tokens=10000,
        tools=[tool],
        system_instruction=system_instruction,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    last_sql = ""
    last_rows: list = []
    chart_future = None
    chart_executor = None
    first_token_at: Optional[float] = None
    bq_ms_total = 0.0
    tool_turns = 0
    yielded_text = False

    try:
        for turn in range(_MAX_TOOL_TURNS + 1):
            fn_calls: list = []
            model_parts: list = []  # original Part objects — preserves thought_signature
            turn_first_text = True

            with _GEMINI_SEM:
                stream = client.client.models.generate_content_stream(
                    model=client.model, contents=contents, config=config
                )
                for chunk in stream:
                    for cand in chunk.candidates or []:
                        content = cand.content
                        if not content or not content.parts:
                            continue
                        for part in content.parts:
                            model_parts.append(part)
                            if getattr(part, "text", None):
                                if first_token_at is None:
                                    first_token_at = time.perf_counter()
                                if turn_first_text and yielded_text:
                                    yield "\n\n"
                                turn_first_text = False
                                yielded_text = True
                                yield part.text
                            fc = getattr(part, "function_call", None)
                            if fc and fc.name:
                                fn_calls.append(fc)

            if not fn_calls:
                break
            if turn >= _MAX_TOOL_TURNS:
                logger.warning("tool_loop_turn_limit", query=query[:80])
                yield "\n\n⚠️ 조회 재시도 한도를 초과했습니다. 질문을 더 구체적으로 바꿔 다시 시도해 주세요."
                break

            tool_turns += 1
            # Gemini 3.x requires functionCall parts to be echoed back verbatim
            # (including thought_signature) — never rebuild these Parts.
            contents.append(types.Content(role="model", parts=model_parts))

            response_parts: list = []
            for fc in fn_calls:
                args = dict(fc.args) if fc.args else {}
                t_bq = time.perf_counter()
                payload, executed_sql, rows = _execute_tool_call(
                    args.get("sql", ""),
                    query,
                    brand_filter,
                    allowed_tables,
                    can_view_fi=can_view_fi,
                )
                bq_ms_total += (time.perf_counter() - t_bq) * 1000
                if payload.get("access_denied"):
                    yield FI_ACCESS_DENIED_MESSAGE
                    return
                if rows:
                    last_sql, last_rows = executed_sql, rows
                elif executed_sql and not last_sql:
                    last_sql = executed_sql
                response_parts.append(
                    types.Part.from_function_response(name=fc.name, response=payload)
                )
            contents.append(types.Content(role="user", parts=response_parts))

            # Chart generation runs in parallel with the final answer turn,
            # mirroring the legacy pipeline's background chart future.
            if last_rows and chart_future is None:
                import concurrent.futures

                preview = (
                    _build_smart_preview(last_rows, query)
                    if len(last_rows) > 100
                    else json.dumps(last_rows[:50], ensure_ascii=False, default=str)
                )
                chart_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                chart_future = chart_executor.submit(
                    _try_generate_chart, get_flash_client(), query, last_sql, preview, last_rows
                )

        if chart_future is not None:
            try:
                chart_markdown = chart_future.result(timeout=8.0)
                if chart_markdown:
                    yield f"\n\n#### 시각화\n{chart_markdown}"
            except Exception:
                pass
            chart_executor.shutdown(wait=False)

        if last_sql:
            yield f"\n\n<details><summary>실행된 쿼리</summary>\n\n```sql\n{last_sql}\n```\n</details>"

        t_end = time.perf_counter()
        logger.info(
            "bq_tool_timing",
            first_token_ms=round(((first_token_at or t_end) - t0) * 1000),
            bq_exec_ms=round(bq_ms_total),
            total_ms=round((t_end - t0) * 1000),
            tool_turns=tool_turns,
            rows=len(last_rows),
        )
    except Exception as e:
        logger.error("bq_tool_loop_failed", error=str(e)[:300])
        yield f"\n\n오류가 발생했습니다: {str(e)[:200]}"
