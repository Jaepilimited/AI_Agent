# -*- coding: utf-8 -*-
"""판정이 보는 본문 · 빈 답변 방어 — 2026-09-15.

둘 다 **조용한 실패**다. 하나는 판정이 본문 일부를 못 봐서 멀쩡한 답을 실패로
적었고(그리고 같은 이유로 금지 문구를 놓칠 수 있었고), 다른 하나는 빈 답변이
HTTP 200 으로 나가는데 예외도 경고도 없었다.
"""

import re
from pathlib import Path

import pytest

from app.core.golden_runner import _body_only, _evaluate, load_golden_set

_ROOT = Path(__file__).resolve().parents[1]

_FOLLOWUP = (
    "> 💡 **이런 것도 물어보세요**\n"
    "> - 브라질 외 다른 남미 국가는?\n"
    "> - 현지 행사 참가 계획은?\n"
)
_PR_SOURCE = (
    "[PR 이슈 원본 시트 · 리스트 탭]"
    "(https://docs.google.com/spreadsheets/d/1MLuwW74Nvv33YW6cVG5qW4uJLGuj3miUqcky9r20A8c/edit?gid=0)"
)


# ── 1. 후속 제안 뒤 본문을 삼키지 않는다 ────────────────────────────────────

def test_body_keeps_what_comes_after_the_followup_block() -> None:
    """제안 뒤에 붙는 출처·표·공시는 본문이다 — 판정이 봐야 한다.

    실패 사례: `pr_issue_reaches_pr` 이 답변에 시트 링크가 **있는데도** 9런 내리
    "필수 누락" 이었다. 링크가 제안 뒤에 붙는데 판정이 거기부터 끝까지 버렸다.
    """
    answer = "브라질 Sephora 입점 내용입니다.\n\n" + _FOLLOWUP + "\n" + _PR_SOURCE

    body = _body_only(answer)

    assert "1MLuwW74Nvv33YW6cVG5qW4uJLGuj3miUqcky9r20A8c" in body
    assert "브라질 Sephora 입점 내용입니다." in body


def test_body_still_drops_the_suggestions_themselves() -> None:
    """제안 문구는 계속 빠져야 한다 — run#23 의 거짓 통과가 그 자리였다."""
    answer = "본문은 B2B 이야기입니다.\n\n> 💡 **이런 것도 물어보세요**\n> - B2C 채널의 할인 금액은?\n"

    body = _body_only(answer)

    assert "B2C" not in body
    assert "본문은 B2B 이야기입니다." in body


def test_banned_phrase_after_the_followup_block_is_not_missed() -> None:
    """음성 단언이 조용히 죽지 않아야 한다 — 실패보다 나쁜 것은 무력한 단언이다."""
    answer = "정상 답변입니다.\n\n" + _FOLLOWUP + "\n죄송합니다. 데이터가 없습니다.\n"
    item = {"expect": {"not_contains": ["데이터가 없습니다"], "min_len": 1}}

    reasons = _evaluate(item, answer, 1.0)

    assert any("데이터가 없습니다" in r for r in reasons)


def test_details_block_is_still_removed() -> None:
    answer = "본문.\n<details>실행된 쿼리\nSELECT 비밀 FROM 어딘가\n</details>"

    assert "비밀" not in _body_only(answer)


def test_followup_strip_matches_the_frontend_rule() -> None:
    """⛔ 화면과 판정이 같은 규칙을 써야 한다.

    갈리면 사용자가 보는 본문과 판정이 보는 본문이 달라져 어느 쪽도 못 믿는다.
    프론트(`stripFollowupBlock`)는 **줄 단위**로 제안 항목까지만 건너뛴다 —
    끝까지 지우는 정규식(DOTALL)으로 되돌아가면 이 테스트가 막는다.
    """
    src = (_ROOT / "app" / "core" / "golden_runner.py").read_text(encoding="utf-8")
    assert "re.DOTALL" not in src.split("def _strip_followup_block")[1].split("def _body_only")[0]

    front = (_ROOT / "app" / "frontend" / "chat.js").read_text(encoding="utf-8")
    assert "function stripFollowupBlock" in front, "프론트 구현이 사라졌다 — 규칙이 갈렸는지 확인할 것"


# ── 2. 필터 확인은 SQL 에서 한다 (본문 기대어는 확률적이다) ─────────────────

def test_sql_contains_all_is_enforced() -> None:
    item = {"expect": {"sql_contains_all": ["integrated_ad", "KBT"], "min_len": 1}}
    answer = "표입니다.\n<details>\n\n```sql\nSELECT media FROM `x.marketing_analysis.integrated_ad` WHERE team = 'KBT'\n```\n</details>"

    assert _evaluate(item, answer, 1.0) == []


def test_sql_contains_all_fails_when_the_team_filter_is_gone() -> None:
    """팀 필터가 빠지면 전사 집계가 되는데 답변은 그럴듯하다 — SQL 이 유일한 증거다."""
    item = {"expect": {"sql_contains_all": ["integrated_ad", "KBT"], "min_len": 1}}
    answer = "표입니다.\n<details>\n\n```sql\nSELECT media FROM `x.marketing_analysis.integrated_ad`\n```\n</details>"

    reasons = _evaluate(item, answer, 1.0)

    assert any("KBT" in r for r in reasons)


# ── 3. 고친 두 문항이 되돌아가지 않게 못 박는다 ─────────────────────────────

def _item(item_id: str) -> dict:
    return next(i for i in load_golden_set() if i["id"] == item_id)


def test_currency_item_checks_the_split_not_a_frozen_count() -> None:
    """살아 있는 집계값을 문자열로 얼리면 반드시 낡는다 (1,142 → 1,141, 13런 연속 실패)."""
    expected = _item("log_amount_currency_mixed")["expect"]

    assert {"USD", "EUR", "JPY", "CNY"}.issubset(expected["contains_all"])
    frozen = {"1,142", "1142", "1,141", "1141"}
    assert not frozen & set(expected.get("contains_any", []))
    assert not frozen & set(expected["contains_all"])


def test_date_cap_item_checks_the_team_in_sql_not_in_prose() -> None:
    """본문 기대어로 두면 LLM 문장에 우연히 들어갈 때만 통과한다 (20런 중 9회 실패)."""
    expected = _item("inc_date_cap_gate_never_kills_request")["expect"]

    assert {"integrated_ad", "KBT"}.issubset(expected["sql_contains_all"])
    assert "KBT" not in expected.get("contains_any", [])
    assert "한국사업팀" not in expected.get("contains_any", [])
    # 관문이 되살아나면 잡아야 하는 단언은 그대로 있어야 한다
    assert "SQL이 생성되지 않았습니다" in expected["not_contains"]


# ── 4. 빈 답변은 조용히 나가지 않는다 ───────────────────────────────────────

class _Block:
    def __init__(self, type_: str, text: str = "") -> None:
        self.type = type_
        self.text = text


class _Resp:
    def __init__(self, blocks: list, stop_reason: str = "end_turn") -> None:
        self.content = blocks
        self.stop_reason = stop_reason


def test_first_text_warns_when_there_is_no_text_block(monkeypatch) -> None:
    """모델이 출력 토큰을 다 쓰고도 text 블록 없이 끝나는 일이 실제로 있다.

    2026-09-15 골든 run#88: 출력 1,191 토큰 · `answer_len=0` · 로그 0줄.
    """
    from app.core import llm as llm_mod

    seen: list[tuple] = []
    monkeypatch.setattr(
        llm_mod.logger, "warning",
        lambda event, **kw: seen.append((event, kw)))

    out = llm_mod.ClaudeClient._first_text(_Resp([_Block("thinking")]))

    assert out == ""
    assert seen and seen[0][0] == "claude_no_text_block"
    assert seen[0][1]["block_types"] == ["thinking"]


def test_first_text_stays_quiet_and_correct_when_text_exists(monkeypatch) -> None:
    from app.core import llm as llm_mod

    seen: list = []
    monkeypatch.setattr(llm_mod.logger, "warning", lambda e, **kw: seen.append(e))

    out = llm_mod.ClaudeClient._first_text(
        _Resp([_Block("thinking"), _Block("text", "답변입니다")]))

    assert out == "답변입니다"
    assert seen == []


def test_direct_handler_replaces_an_empty_answer() -> None:
    """⛔ 예외가 아니라서 `except` 에 안 걸린다 — 따로 막지 않으면 빈 화면이 나간다."""
    src = (_ROOT / "app" / "agents" / "orchestrator.py").read_text(encoding="utf-8")
    body = src.split("async def _handle_direct")[1].split("async def _verify_coherence")[0]

    assert "direct_empty_answer" in body, "빈 답변 경고가 사라졌다"
    guard = re.search(
        r"if not \(answer or \"\"\)\.strip\(\):(.{0,600}?)return \{\"source\": \"direct\"",
        body, re.DOTALL)
    assert guard, "빈 답변 가드가 반환 직전에 없다"
    assert "_DIRECT_TEMPORARY_FAILURE" in guard.group(1)
    # 스트리밍은 이미 흘려보낸 뒤라 되돌릴 수 없다 — 안내를 한 조각 더 보낸다
    assert "stream_callback(answer)" in guard.group(1)


@pytest.mark.parametrize("item_id", ["log_amount_currency_mixed",
                                     "inc_date_cap_gate_never_kills_request"])
def test_touched_items_still_load(item_id: str) -> None:
    assert _item(item_id)["expect"]["max_seconds"] > 0
