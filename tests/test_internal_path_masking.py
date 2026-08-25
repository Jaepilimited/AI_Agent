# -*- coding: utf-8 -*-
"""내부 테이블 경로를 답변 본문에 노출하지 않는다 — 붐따 #147 (임재필, 2026-08-25).

    질문: "프로모션 캘린더 경로 알려줘"
    답변: | BigQuery Table Path | `skin1004-319714.promotion_calendar.promotion` |
    제보: "경로는 빅쿼리 경로(테이블)를 알려주면안됨. 보안이슈. 내가 말한건 url말하는거임."

⛔ 프롬프트에는 이미 "테이블명·프로젝트 ID·컬럼명 노출 금지" 가 적혀 있었다. 그런데도
   샜다 — 프롬프트는 확률을 높일 뿐이고 보증은 코드가 한다 (FI 방어선과 같은 사상).

⚠️ `<details>실행된 쿼리</details>` 안의 SQL 은 **건드리지 않는다.** 그건 "이 숫자가
   어디서 나왔나" 를 확인하는 경로이고, 코드 펜스 안에 접힌 채로 들어간다.
   본문(산문·표)에 경로를 **답으로 제시하는 것**이 문제였다.
"""
import pytest

from app.agents.sql_agent import _mask_internal_paths as mask

PROJ = "skin1004-319714"


def test_table_path_in_prose_is_masked():
    """#147 그 자체."""
    out = mask(f"| BigQuery Table Path | `{PROJ}.promotion_calendar.promotion` |")
    assert PROJ not in out
    assert "promotion_calendar" not in out


def test_bare_dataset_table_is_masked_too():
    """프로젝트 ID 없이 `dataset.table` 만 적어도 내부 구조는 그대로 새어 나간다."""
    out = mask("데이터는 `promotion_calendar.promotion` 에 있습니다.")
    assert "promotion_calendar.promotion" not in out


def test_executed_sql_block_is_left_intact():
    """⛔ 실행된 쿼리는 근거다 — 여기까지 지우면 숫자를 확인할 길이 사라진다."""
    body = ("본문입니다.\n\n<details><summary>실행된 쿼리</summary>\n\n"
            f"```sql\nSELECT 1 FROM `{PROJ}.promotion_calendar.promotion`\n```\n</details>")
    out = mask(body)
    assert f"`{PROJ}.promotion_calendar.promotion`" in out


def test_code_fence_is_left_intact():
    body = f"설명\n\n```sql\nSELECT * FROM `{PROJ}.Sales_Integration.Product`\n```\n"
    assert out_has(body)


def out_has(body):
    out = mask(body)
    return "Sales_Integration.Product" in out


def test_ordinary_text_with_dots_is_not_touched():
    """⚠️ 버전·소수점을 경로로 오인하면 멀쩡한 문장이 뭉개진다."""
    for s in ("증가율은 3.14.15 입니다", "약 12.3억원", "2026.08.25 기준", "v1.2.3"):
        assert mask(s) == s


def test_masking_runs_on_every_answer_path():
    """⛔ 한 경로만 막으면 다른 경로로 그대로 나간다 (스트리밍/비스트리밍)."""
    import inspect

    from app.agents import sql_agent

    src = inspect.getsource(sql_agent)
    assert src.count("_mask_internal_paths(") >= 3


def test_stream_masks_across_chunk_boundaries():
    """⛔ 경로가 청크 경계에서 쪼개져도 막아야 한다 — 절반만 듣는 방어는 방어가 아니다."""
    from app.agents.sql_agent import _mask_stream

    chunks = ["경로는 `skin1004-3", "19714.promotion_ca", "lendar.promotion` 입니다.\n끝"]
    out = "".join(_mask_stream(iter(chunks)))
    assert "promotion_calendar" not in out
    assert "skin1004-319714" not in out
    assert "끝" in out                      # 마지막 조각도 흘려보낸다


def test_stream_passes_ordinary_text_through():
    from app.agents.sql_agent import _mask_stream

    chunks = ["일본 매출은 ", "약 55.1억원입니다.\n", "감사합니다."]
    assert "".join(_mask_stream(iter(chunks))) == "".join(chunks)
