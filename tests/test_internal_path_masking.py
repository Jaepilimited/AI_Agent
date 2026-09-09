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
    """⛔ 한 경로만 막으면 다른 경로로 그대로 나간다 (스트리밍/비스트리밍).

    ⚠️ **이 개수 검사는 2026-09-09 에 실제 유출을 놓쳤다.** 호출이 몇 번 나오는지는
       "무엇이 가려지는가" 를 말해 주지 않는다 — 아래 동작 검사가 진짜 방어다.
    """
    import inspect

    from app.agents import sql_agent

    src = inspect.getsource(sql_agent)
    assert src.count("_mask_internal_paths(") >= 3


# ── 붐따 #147 재발 (2026-09-09 실측) ─────────────────────────────────────────
#
# `_mask_internal_paths` 는 **LLM 이 쓰는 서술**에만 걸려 있었고, 코드가 결과에서
# 만들어 흘리는 표는 마스킹 밖이었다. "프로모션 캘린더 경로 알려줘" 실측:
#
#     [비스트리밍] 노출 없음      ← format_answer 가 답변 전체를 가린다
#     [스트리밍]   **노출됨**     ← 실사용 경로가 이쪽이다
#
# 이 저장소가 반복해 겪은 "두 경로 중 한쪽만" 이고, 하필 보안 건에서 났다.


def test_the_code_built_table_is_masked_too():
    """⛔ 표를 만드는 것도 코드다 — LLM 서술만 가리면 절반만 막는 것이다."""
    from app.agents.sql_agent import _fast_answer_head

    head = _fast_answer_head(
        "프로모션 캘린더 경로 알려줘",
        [{"table_path": "skin1004-319714.promotion_calendar.promotion"}])

    assert "skin1004-319714" not in head
    assert "promotion_calendar" not in head
    assert "내부 경로 비공개" in head


def test_the_code_built_table_keeps_ordinary_values():
    """⚠️ 경로처럼 생겼을 뿐인 값을 뭉개면 멀쩡한 답이 망가진다."""
    from app.agents.sql_agent import _fast_answer_head

    head = _fast_answer_head("버전 알려줘", [{"버전": "3.14.15"}, {"버전": "v1.2.3"}])
    assert "3.14.15" in head and "v1.2.3" in head


def test_the_fast_stream_yields_the_head_through_the_masking_helper():
    """⛔ 머리를 만드는 곳과 가리는 곳을 갈라 놓지 마라 — 갈라지면 또 샌다.

    표를 손으로 다시 조립하는 코드가 생기면 이 검사가 걸린다.
    """
    import inspect

    from app.agents import sql_agent

    src = inspect.getsource(sql_agent)
    assert "out_head = _fast_answer_head(" in src, \
        "빠른 응답 머리는 _fast_answer_head() 로만 만든다"
    assert "_mask_internal_paths(head)" in inspect.getsource(sql_agent._fast_answer_head), \
        "머리 조립 함수가 스스로 가려야 한다"


def test_the_verification_notice_is_masked():
    """⛔ 대조용 원본 표는 **조회 결과 그대로**다 — 여기로도 샌다."""
    import inspect

    from app.agents import sql_agent

    src = inspect.getsource(sql_agent)
    assert src.count("_mask_internal_paths(\n        _number_check_notice(") == 2, \
        "두 스트리밍 경로 모두에서 원본 표를 가려야 한다"


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
