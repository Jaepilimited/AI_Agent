# -*- coding: utf-8 -*-
"""차트는 조건 없이 그린다 — 많으면 상위만, 기준이 섞이면 하나만 (2026-10-06).

사용자 지시: *"그냥 조건없이 잘 대답하면 안되나"*. 실제 답변은 이랬다:
"이번 조회 결과가 집계 기준이 섞인 180여 행이었기 때문입니다. 전사 합계 +
국가별 170여 개 + 팀별 12개 … 범위를 좁혀 다시 보내주시면 차트가 함께 나옵니다."
시스템이 할 판단을 사람에게 넘긴 것이다.

⛔ 잘랐거나 골랐으면 **반드시 말한다** — 말이 빠지면 "15개국이 전부" 로 읽힌다.
"""

import json

from app.core import chart

CFG = {"chart_type": "bar", "x_column": "name", "y_column": "sales",
       "title": "9월 대비 10월 매출"}


def _mixed_rows():
    """사고 모양 그대로 — 전사 1 + 국가 170 + 팀 12 가 한 표에."""
    rows = [{"level": "전사", "name": "전사", "sales": 100_000.0}]
    rows += [{"level": "국가", "name": f"국가{i:03d}", "sales": float(1000 - i)}
             for i in range(170)]
    rows += [{"level": "팀", "name": f"팀{i:02d}", "sales": float(5000 - i)}
             for i in range(12)]
    return rows


def _build(rows, query="", cfg=CFG):
    notes = []
    out = chart.build_chartjs_config(dict(cfg, _query=query), rows, notes=notes)
    return (json.loads(out) if out else None), notes


def test_섞인_기준에서_질문이_부른_팀_기준만_그린다():
    out, notes = _build(_mixed_rows(), "2026년 9월 대비 10월 팀별 매출 비교 차트")
    assert out is not None, "기준이 섞였다고 차트를 버렸다"
    labels = out["data"]["labels"]
    assert len(labels) == 12 and all(l.startswith("팀") for l in labels)
    assert any("팀" in n and "섞여" in n for n in notes), "기준을 골랐는데 말하지 않았다"


def test_질문이_기준을_안_부르면_가장_잘게_나눈_기준의_상위를_그린다():
    out, notes = _build(_mixed_rows(), "9월 대비 10월 매출 비교")
    assert out is not None
    labels = out["data"]["labels"]
    assert len(labels) == chart.TOP_N and all(l.startswith("국가") for l in labels)
    assert labels[0] == "국가000", "값이 큰 순이 아니다"
    assert "전사" not in labels, "합계 행이 막대를 눌러 버린다"
    joined = " ".join(notes)
    assert "섞여" in joined and "상위" in joined, "고르고 자른 사실이 둘 다 보여야 한다"


def test_교차표는_기준_열로_오인하지_않는다():
    """브랜드 × 국가 — 같은 국가가 브랜드마다 나온다. 이건 섞인 기준이 아니다."""
    rows = [{"brand": b, "name": f"국가{i}", "sales": float(i + 1)}
            for b in ("SK", "UM") for i in range(6)]
    out, notes = _build(rows, "브랜드 국가별 매출")
    assert out is not None
    assert len(out["data"]["labels"]) == 12, "교차표 행을 기준으로 잘라 버렸다"
    assert not any("섞여" in n for n in notes)


def test_합계_행은_차트에서만_뺀다():
    rows = [{"name": n, "sales": v} for n, v in
            [("합계", 600.0), ("일본", 300.0), ("미국", 200.0), ("중국", 100.0)]]
    out, notes = _build(rows)
    assert out["data"]["labels"] == ["일본", "미국", "중국"]
    assert notes and "합계" in notes[0]


def test_증감이_섞이면_크기가_큰_순으로_자른다():
    rows = [{"name": f"국가{i:02d}", "sales": float(i)} for i in range(30)]
    rows.append({"name": "급감국", "sales": -500.0})
    out, notes = _build(rows, cfg=dict(CFG, y_column="sales"))
    assert "급감국" in out["data"]["labels"], "가장 크게 줄어든 곳이 잘려 나갔다"
    assert notes and "크기가" in notes[0]


def test_날짜_축은_값이_아니라_최근을_남긴다():
    rows = [{"name": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "sales": float(i % 7)}
            for i in range(60)]
    out, notes = _build(rows, cfg=dict(CFG, chart_type="bar"))
    labels = out["data"]["labels"]
    assert len(labels) == chart.TOP_N
    assert labels[-1] == rows[-1]["name"], "날짜를 값 순으로 섞어 잘랐다"
    assert notes and "최근" in notes[0]


def test_적은_표는_손대지_않는다():
    rows = [{"name": n, "sales": v} for n, v in [("일본", 3.0), ("미국", 2.0), ("중국", 1.0)]]
    out, notes = _build(rows)
    assert out["data"]["labels"] == ["일본", "미국", "중국"]
    assert notes == []


def test_LLM_에게_항목수로_차트를_포기하라고_하지_않는다():
    prompt = chart.get_chart_config_prompt("q", "select 1", "[]", 182)
    assert "needs_chart: false" not in prompt.split("항목 수 때문에")[0][-200:]
    assert "카테고리가 15개 초과" not in prompt, "옛 포기 규칙이 프롬프트에 남아 있다"
    assert "섞여" in prompt
