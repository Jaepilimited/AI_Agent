# -*- coding: utf-8 -*-
"""조직이 검증한 질문↔SQL 을 예시로 주는 규칙.

⛔ **답을 재생하는 것이 아니다.** `sql_cache` 는 같은 질문에 같은 SQL 을 그대로 내주고,
   그래서 **0건을 내는 SQL 도 재생한다**는 알려진 문제가 있다. 여기서 뽑은 것은
   프롬프트에 *참고*로 들어갈 뿐이고 검증·실행·후처리는 그대로 지난다.

⛔ **틀린 것을 학습하면 더 자신 있게 틀린다.** 자산이 되는 조건이 좁은 이유다.
"""
import inspect
from pathlib import Path

from app.core import sql_examples as ex

ROOT = Path(__file__).resolve().parent.parent


def test_zero_row_queries_never_become_examples():
    """⛔ 0건을 낸 SQL 을 예시로 굳히면 다음 사람도 같은 방향으로 0건을 낸다."""
    src = inspect.getsource(ex.record)
    assert "row_count <= 0" in src


def test_one_off_questions_are_not_assets():
    """한 번은 우연이다 — 두 번 이상 나온 것만 예시가 된다."""
    assert ex.MIN_SEEN >= 2
    assert "n_seen >= %s" in inspect.getsource(ex.pick)


def test_unrelated_questions_get_no_example():
    """⚠️ 겹침 문턱을 낮추면 아무 예시나 붙는다 — 엉뚱한 예시는 없느니만 못하다."""
    assert ex.MIN_OVERLAP >= 0.3
    assert ex.MAX_EXAMPLES <= 3


def test_examples_are_chosen_without_an_llm():
    """⛔ 왜 이 예시가 프롬프트에 들어갔는지 설명할 수 있어야 한다."""
    src = inspect.getsource(ex.pick)
    # ⚠️ 낱말 "llm" 을 찾으면 **주석에 걸린다** ("LLM 을 쓰지 않는다" 라고 적어 뒀다).
    #    실제 호출을 본다.
    for call in ("get_flash_client", "get_gemini", "generate(", "embed"):
        assert call not in src, call
    assert "_keywords" in src
    assert "sql_examples_picked" in src, "고른 근거를 로그에 남겨야 한다"


def test_forbidden_tables_never_leak_through_an_example():
    """⚠️ 손익(FI)처럼 사람마다 볼 수 있는 범위가 다른 테이블이 **예시로** 새면
       프롬프트에서 스키마를 지운 의미가 없다."""
    allowed = {"proj.Sales_Integration.SALES_ALL_Backup"}
    assert not ex._tables_ok("SELECT * FROM `proj.Sales_Integration.FI_LLM_Flat`", allowed)
    assert ex._tables_ok("SELECT * FROM `proj.Sales_Integration.SALES_ALL_Backup`", allowed)


def test_a_thumbs_down_blocks_the_example_immediately():
    """⛔ 사람이 틀렸다고 하면 **즉시** 빼야 한다 — 배치로 미루면 그 사이 계속 쓰인다.
       ⚠️ 지우지 않고 막는다: 왜 막혔는지 남아야 되돌릴 수 있고, 같은 질문이 다시
          쌓여 조용히 되살아나는 것도 막힌다."""
    src = inspect.getsource(ex.block)
    assert "blocked = 1" in src and "blocked_why" in src
    assert "DELETE" not in src.upper()

    memory = (ROOT / "app" / "agents" / "skill_memory.py").read_text(encoding="utf-8")
    neg = memory.split("def save_negative_skill", 1)[1].split("\ndef ", 1)[0]
    assert "sql_examples import block" in neg


def test_follow_ups_are_neither_stored_nor_matched():
    """⛔ "시각화해줘" · "여기서 인도네시아만" 은 앞 대화가 진짜 의도다 — 예시로 쓰면
       **왜 그 SQL 인지 설명되지 않는다.** 실측(2026-08-27): 부트스트랩에서 후속 질문이
       1,142건이었다 (자산 후보의 절반 이상)."""
    agent = (ROOT / "app" / "agents" / "sql_agent.py").read_text(encoding="utf-8")
    record_block = agent.split("from app.core.sql_examples import record", 1)[0][-400:]
    assert 'state.get("conversation_context")' in record_block
    inject = agent.split("example_section = \"\"", 1)[1].split("\n\n", 1)[0]
    assert "if not conv_context:" in inject

    boot = (ROOT / "scripts" / "bootstrap_sql_examples.py").read_text(encoding="utf-8")
    assert "first_user" in boot and "skipped_followup" in boot


def test_the_example_block_says_it_is_only_a_reference():
    """⚠️ 예시를 그대로 베끼면 기간·필터가 지금 질문과 어긋난다 — 프롬프트가 그렇게 말해야 한다."""
    text = ex.render([{"question": "q", "sql": "SELECT 1", "overlap": 1.0, "n_seen": 2}])
    assert "참고" in text and "그대로 쓰지 말고" in text
    assert ex.render([]) == "", "예시가 없으면 빈 제목만 남기지 않는다"


def test_the_prompt_still_carries_the_examples():
    """붙였는데 프롬프트에 안 들어가면 아무 일도 일어나지 않는다 (에러도 없다)."""
    agent = (ROOT / "app" / "agents" / "sql_agent.py").read_text(encoding="utf-8")
    line = next(l for l in agent.splitlines() if l.strip().startswith("full_prompt = f\""))
    assert "{example_section}" in line
