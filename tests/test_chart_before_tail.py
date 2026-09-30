# -*- coding: utf-8 -*-
"""`#### 시각화` 는 답변 꼬리(조회 기준·후속 질문) **앞**에 온다 — 2026-09-30.

같은 제보에서 함께 드러났다 (프로덕션 msg 15074). 차트는 그려졌는데 자리가 이랬다:

    #### 분석 및 인사이트
    ---
    *조회 기준: 2026-09-30 | 내부 데이터베이스*
    > 💡 **이런 것도 물어보세요**
    #### 시각화            <- 후속 질문 칩 **아래**
    ```chart-config

답변이 끝난 줄 알고 스크롤을 멈춘 사람에게는 차트가 없는 것과 같다.

⛔ 스트리밍은 이미 흘려보낸 글자 사이에 끼워 넣을 수 없어 맨 끝에 붙이고 있었고,
   비스트리밍은 `#### 분석 및 인사이트` **앞**에 넣어 **경로마다 자리가 달랐다.**
   이제 둘 다 꼬리만 붙잡아 두고 그 앞에 차트를 넣는다 (`_AnswerTailHolder`).
"""

from pathlib import Path

from app.agents.sql_agent import (
    _AnswerTailHolder,
    _split_answer_tail,
    _with_chart_before_tail,
)

_SRC = Path(__file__).resolve().parents[1] / "app" / "agents" / "sql_agent.py"

# 프롬프트가 지시하는 그 꼬리 (비스트리밍·fast-answer 프롬프트가 같은 모양이다)
BODY = "#### 분석 및 인사이트\n- 3월이 가장 높았습니다.\n"
TAIL = (
    "\n---\n"
    "*조회 기준: 2026-09-30 | 내부 데이터베이스*\n"
    "> 💡 **이런 것도 물어보세요**\n"
    "> - 분기별로 비교해줘\n"
)
ANSWER = BODY + TAIL
CHART = '```chart-config\n{"type": "line", "data": {}}\n```'


def _order(text):
    """화면에 나타나는 순서를 낱말로 돌려준다."""
    marks = {
        "인사이트": text.find("#### 분석 및 인사이트"),
        "시각화": text.find("#### 시각화"),
        "조회기준": text.find("*조회 기준:"),
        "후속질문": text.find("이런 것도 물어보세요"),
    }
    return [k for k, v in sorted(marks.items(), key=lambda kv: kv[1]) if v >= 0]


# ── 꼬리를 가르는 규칙 ────────────────────────────────────────────────────

def test_the_tail_is_the_footer_and_the_followup_block():
    body, tail = _split_answer_tail(ANSWER)

    assert "#### 분석 및 인사이트" in body
    assert "3월이 가장 높았습니다" in body
    assert "*조회 기준:" in tail and "이런 것도 물어보세요" in tail
    assert body + tail == ANSWER, "가르기만 한다 — 한 글자도 잃지 않는다"


def test_a_mid_body_horizontal_rule_is_not_swallowed():
    """⛔ 본문 중간의 `---` 를 꼬리로 읽으면 답변 뒷부분이 통째로 뒤로 밀린다."""
    text = "앞 문단\n\n---\n\n뒤 문단도 본문이다\n" + TAIL
    body, tail = _split_answer_tail(text)

    assert "뒤 문단도 본문이다" in body
    assert "---" in body, "본문의 수평선은 본문에 남는다"
    assert "*조회 기준:" in tail
    assert body + tail == text


def test_an_answer_without_a_tail_holds_nothing():
    body, tail = _split_answer_tail("요약만 있는 답변\n")
    assert tail == ""
    assert body == "요약만 있는 답변\n"


def test_the_footer_line_alone_still_starts_the_tail():
    """구분선 없이 `*조회 기준:` 만 와도 꼬리다 (LLM 이 `---` 를 빼먹을 수 있다)."""
    text = "본문\n*조회 기준: 2026-09-30 | 내부 데이터베이스*\n"
    body, tail = _split_answer_tail(text)
    assert body == "본문\n"
    assert "*조회 기준:" in tail


# ── 차트가 꼬리 앞에 들어간다 (비스트리밍) ────────────────────────────────

def test_the_chart_lands_between_the_insights_and_the_footer():
    out = _with_chart_before_tail(ANSWER, CHART)

    assert _order(out) == ["인사이트", "시각화", "조회기준", "후속질문"]
    assert CHART in out


def test_nothing_from_the_answer_is_lost():
    out = _with_chart_before_tail(ANSWER, CHART)
    for line in ANSWER.splitlines():
        if line.strip():
            assert line in out, line


def test_without_a_tail_the_chart_goes_last():
    out = _with_chart_before_tail("#### 요약\n한 줄\n", CHART)
    assert out.index("#### 시각화") > out.index("한 줄")


# ── 스트리밍도 같은 자리에 온다 ───────────────────────────────────────────

def _streamed_with_chart(text, size=9):
    """스트리밍 경로가 하는 일을 그대로 재현한다: 꼬리를 붙잡고, 차트를 먼저."""
    holder = _AnswerTailHolder()
    out = []
    for i in range(0, len(text), size):          # 청크 경계를 꼬리 가운데로 쪼갠다
        piece = holder.feed(text[i:i + size])
        if piece:
            out.append(piece)
    out.append("\n\n#### 시각화\n" + CHART)
    out.append(holder.flush())
    return "".join(out)


def test_streaming_puts_the_chart_before_the_tail_too():
    out = _streamed_with_chart(ANSWER)
    assert _order(out) == ["인사이트", "시각화", "조회기준", "후속질문"]


def test_streaming_and_non_streaming_agree_on_the_order():
    """⛔ 경로마다 자리가 다르면 같은 질문이 다르게 보인다 (그게 이 결함이었다)."""
    assert _order(_streamed_with_chart(ANSWER)) == _order(
        _with_chart_before_tail(ANSWER, CHART))


def test_streaming_loses_nothing_when_there_is_no_chart():
    holder = _AnswerTailHolder()
    body = holder.feed(ANSWER)
    assert body + holder.flush() == ANSWER


# ── 배선: 세 경로 전부 ────────────────────────────────────────────────────

def test_all_three_paths_are_wired():
    src = _SRC.read_text(encoding="utf-8")

    # 비스트리밍은 헬퍼를 쓴다 (분석 절 앞 삽입은 사라졌다)
    assert "_with_chart_before_tail(answer, chart_markdown)" in src
    assert "insight_markers" not in src, "옛 삽입 자리가 남아 있다 — 경로가 갈린다"
    # 스트리밍 두 경로가 각자 꼬리를 붙잡는다
    assert src.count("_tail = _AnswerTailHolder()") == 2
    assert src.count("_answer_tail = _tail.flush()") == 2


def test_the_chart_is_yielded_before_the_tail_in_both_streams():
    """소스 순서를 본다 — 꼬리를 먼저 흘리면 고친 것이 원래대로 돌아간다."""
    src = _SRC.read_text(encoding="utf-8")
    for fn in ("_fast_answer_stream", "def run_sql_agent_stream"):
        block = src.split(fn, 1)[1]
        chart_at = block.find('#### 시각화\\n{chart_markdown}')
        tail_at = block.find("_answer_tail = _tail.flush()")
        assert 0 <= chart_at < tail_at, fn


def test_the_number_check_runs_after_the_tail_is_collected():
    """⚠️ 꼬리를 `_streamed` 에 담기 전에 검증하면 꼬리의 수치가 빠진다."""
    src = _SRC.read_text(encoding="utf-8")
    for fn in ("_fast_answer_stream", "def run_sql_agent_stream"):
        block = src.split(fn, 1)[1]
        tail_at = block.find("_streamed.append(_answer_tail)")
        check_at = block.find("_number_check_notice(")
        assert 0 <= tail_at < check_at, fn
