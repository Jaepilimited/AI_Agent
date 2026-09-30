# -*- coding: utf-8 -*-
"""LLM 이 스스로 쓴 차트 펜스는 답변에 남지 않는다 — 2026-09-30 사용자 제보.

제보는 답변 한가운데 박힌 JSON 원문이었다 (사용자가 그 블록만 그대로 붙여 왔다):

    질문: "월별 매출 알려주고, 시각화해줘"   (프로덕션 msg 15074, 12:29)
    답변: ... 상세 데이터 표 ...
          ```chart:line
          {"title": "2026년 월별 매출 추이", "labels": [...], "series": [...]}
          ```
          ... 분석 및 인사이트 ...
          #### 시각화
          ```chart-config
          {"type": "line", "data": {...}}      <- 이쪽만 그림이 된다
          ```

같은 차트가 **두 번** 실렸다. 프론트(`chat.js`)가 그리는 태그는 `chart-config`
하나뿐이라 모델이 고른 태그는 코드 블록 글자 더미로 남는다.

실측(프로덕션, 8/1~9/30): 차트가 실린 답변 314건 중 2건
(9/15 `chart:doughnut` · 9/30 `chart:line`). 둘 다 **Gemini Flash** 가 쓴
답변이고 9/15 건은 사내 LLM 게이트웨이 개통(9/28) **전**이다 — 게이트웨이와 무관하다.

프롬프트에도 금지를 적었지만 보증은 코드다.
"""

import re
from pathlib import Path

from app.agents.sql_agent import (
    _ModelChartStripper,
    _mask_stream,
    _strip_model_chart_fences,
)

_SRC = (Path(__file__).resolve().parents[1] / "app" / "agents" / "sql_agent.py")

# 제보된 그 블록 (msg 15074 원문)
REPORTED = """#### 상세 데이터
| 월 | 매출액 (원) |
| :--- | :--- |
| 2026-01 | 2,778,097,051 |

```chart:line
{
  "title": "2026년 월별 매출 추이",
  "labels": ["2026-01", "2026-02"],
  "series": [
    {
      "name": "매출액",
      "data": [
        2778097051,
        3361671835
      ]
    }
  ]
}
```

#### 분석 및 인사이트
- 3월 매출이 가장 높았습니다.
"""


def test_the_reported_block_is_gone_and_the_rest_survives():
    out = _strip_model_chart_fences(REPORTED)

    assert "```chart:line" not in out
    assert '"series"' not in out
    assert "2778097051" not in out, "JSON 안의 원시 숫자가 남았다"
    # 표와 인사이트는 그대로다 — 지운 것은 그릴 수 없는 블록 하나뿐이다
    assert "| 2026-01 | 2,778,097,051 |" in out
    assert "#### 분석 및 인사이트" in out
    assert "3월 매출이 가장 높았습니다" in out


def test_the_real_chart_block_is_never_touched():
    """⛔ `chart-config` 를 걷으면 차트가 통째로 사라진다 — 이 방어의 반대 사고다."""
    real = (
        "앞 문장\n\n"
        '```chart-config\n'
        '{"type": "line", "data": {"labels": ["2026-01"], '
        '"datasets": [{"label": "매출액", "data": [1]}]}}\n'
        "```\n\n"
        "뒤 문장\n"
    )
    assert _strip_model_chart_fences(real) == real


def test_sql_evidence_block_is_never_touched():
    sql = "문장\n\n```sql\nSELECT SUM(Sales1_R)\n```\n"
    assert _strip_model_chart_fences(sql) == sql


def test_other_fences_survive():
    for tag in ("json", "python", "", "text"):
        block = "앞\n\n```%s\n{}\n```\n뒤\n" % tag
        assert _strip_model_chart_fences(block) == block, tag


def test_every_model_chart_tag_is_stripped():
    """실측된 두 가지(`chart:line`·`chart:doughnut`)와 맨 `chart` 를 함께 막는다."""
    for tag in ("chart:line", "chart:doughnut", "chart", "chartjs", "chart-configX"):
        block = "앞\n\n```%s\n{}\n```\n뒤\n" % tag
        out = _strip_model_chart_fences(block)
        assert "```" + tag not in out, tag
        assert "앞" in out and "뒤" in out, tag


# ── 스트리밍도 같은 스트리퍼를 쓴다 (한쪽만 고치면 경로에 따라 답이 갈린다) ──

def _stream(text, size=7):
    """청크 경계를 일부러 펜스 가운데로 쪼갠다."""
    return (text[i:i + size] for i in range(0, len(text), size))


def test_streaming_strips_the_same_block():
    out = "".join(_mask_stream(_stream(REPORTED)))

    assert "```chart:line" not in out
    assert "2778097051" not in out
    assert "| 2026-01 | 2,778,097,051 |" in out
    assert "3월 매출이 가장 높았습니다" in out


def test_streaming_and_non_streaming_agree():
    """경로가 갈리면 같은 질문에 답이 달라진다 (이 저장소가 반복해서 겪은 실패다)."""
    assert "".join(_mask_stream(_stream(REPORTED))) == _strip_model_chart_fences(REPORTED)


def test_streaming_still_applies_the_money_transform():
    """⚠️ 걷어내기를 끼우면서 금액 배율 교정이 빠지면 1,000배 사고가 되살아난다."""
    out = "".join(_mask_stream(
        _stream("총 매출 828.7억원\n"),
        transform=lambda t: t.replace("828.7억원", "8,287만원")))
    assert "8,287만원" in out


# ── 안전한 쪽으로 실패한다 ─────────────────────────────────────────────────

def test_an_unclosed_fence_is_not_swallowed():
    """⛔ 닫는 펜스가 안 오면 붙잡은 것을 그대로 내보낸다 — 답변 뒷부분을
    삼키는 것이 JSON 이 보이는 것보다 나쁘다."""
    broken = '앞 문장\n\n```chart:line\n{"labels": [1]}\n중요한 마지막 문장\n'
    out = _strip_model_chart_fences(broken)

    assert "중요한 마지막 문장" in out
    assert "앞 문장" in out


def test_streaming_flushes_an_unclosed_fence():
    broken = "앞\n```chart:line\n{}\n꼭 나가야 하는 문장\n"
    out = "".join(_mask_stream(_stream(broken)))
    assert "꼭 나가야 하는 문장" in out


def test_a_very_long_unclosed_fence_gives_up_and_emits():
    from app.agents.sql_agent import _MODEL_CHART_MAX_LINES

    lines = ["앞\n", "```chart:line\n"] + ["x\n"] * (_MODEL_CHART_MAX_LINES + 5)
    out = _strip_model_chart_fences("".join(lines))
    assert out.count("x") >= _MODEL_CHART_MAX_LINES, "상한을 넘겼는데도 계속 삼킨다"


def test_stripper_counts_what_it_dropped():
    """로그로 셀 수 있어야 한다 — 조용히 지우면 재발을 못 본다."""
    s = _ModelChartStripper()
    s.feed(REPORTED)
    s.flush()
    assert s.dropped == 1


# ── 배선: 두 경로에 **함께** 걸려 있는지 소스로 확인한다 ────────────────────

def test_both_paths_are_wired():
    src = _SRC.read_text(encoding="utf-8")

    # 비스트리밍: format_answer 가 LLM 답변을 받은 직후
    assert "_strip_model_chart_fences(answer)" in src
    # 스트리밍: _mask_stream 안에서 같은 스트리퍼를 쓴다
    mask = src.split("def _mask_stream(")[1].split("\ndef ")[0]
    assert "_ModelChartStripper()" in mask
    assert "stripper.feed(" in mask and "stripper.flush()" in mask


def test_the_prompt_also_says_not_to():
    """프롬프트는 확률이지만, 애초에 안 쓰게 하는 것이 먼저다."""
    src = _SRC.read_text(encoding="utf-8")
    assert re.search(r"차트.{0,20}직접 쓰지 마", src)
