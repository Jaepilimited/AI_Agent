# -*- coding: utf-8 -*-
"""연도 비교(YoY) 차트 — 붐따 #145 · #146 (임재필, 2026-08-21~24, 사흘간 5건).

두 가지가 따로 깨져 있었고 사용자의 말이 정확히 그 둘을 갈랐다.

  #145 "차트가 안나옴"       → 대륙 2개를 함께 물으면 **차트가 통째로 사라졌다**
  #146 "원하는 차트가 안나옴" → 차트는 나오는데 **연도가 아니라 대륙이 선(series)** 이었다

⛔ #145 의 원인은 **판정 순서**다. 가독성 상한(행 25/36개)이 YoY 정규화보다 **먼저**
   돌았다. 정규화 전 데이터는 긴 형식(long)이라 대륙 2 × 월 20 = 40행이고, 게다가
   `apply_chart_intent` 가 `group_column` 을 지워 버려 그룹 기준 상한(고유 x 36 ·
   그룹 15)이 아니라 **평평한 행 수 상한**에 걸렸다. 정규화만 먼저 했으면
   월 12 × 연도 2 로 여유롭게 통과한다. `return None` 이라 화면엔 아무 말도 없다.

⛔ #146 의 원인은 **의도 판정**이다. "2025년 라벨1 2026년 라벨2" 에는 `yoy` 도 `전년` 도
   없어서 trend 로 분류됐고, 그래서 한 선 안에 2025 와 2026 이 이어져 그려졌다.
   ⚠️ 다만 연도 두 개가 보인다고 무조건 YoY 로 보면 **"2025년 1월부터 2026년 6월까지"**
      같은 **기간 범위**까지 YoY 가 된다. 범위 표현이 있으면 YoY 가 아니다.
"""
import json

import pytest

from app.core import chart


def _rows(continents=("남아메리카", "중앙아메리카"), months_2026=8):
    out = []
    for c in continents:
        for y, last in ((2025, 12), (2026, months_2026)):
            for m in range(1, last + 1):
                out.append({"month": f"{y}-{m:02d}", "continent": c,
                            "revenue": 1e9 * (m + (5 if y == 2026 else 0))})
    return out


def _build(query, rows, group_column="continent"):
    cfg = {"chart_type": "line", "x_column": "month", "y_column": "revenue",
           "group_column": group_column, "title": "t"}
    cfg = chart.apply_chart_intent(cfg, query, rows)
    out = chart.build_chartjs_config(cfg, rows)
    return cfg, (json.loads(out) if out else None)


def test_yoy_with_two_groups_still_draws_a_chart():
    """#145 그 자체 — 대륙 2개라고 차트가 사라지면 안 된다."""
    cfg, out = _build("YoY로", _rows())
    assert cfg["chart_intent"] == "yoy"
    assert out is not None, "차트가 통째로 사라졌다 (#145)"
    labels = [d["label"] for d in out["data"]["datasets"]]
    # 대륙 × 연도 — 어느 대륙의 어느 해인지 구분돼야 한다
    assert len(labels) == 4, labels
    for want in ("남아메리카 2025", "남아메리카 2026",
                 "중앙아메리카 2025", "중앙아메리카 2026"):
        assert want in labels, labels


def test_yoy_with_one_group_keeps_plain_year_labels():
    """대륙이 하나면 예전처럼 연도만 — 라벨에 군더더기를 붙이지 않는다."""
    _, out = _build("YoY로", _rows(continents=("남아메리카",)))
    assert out is not None
    assert [d["label"] for d in out["data"]["datasets"]] == ["2025", "2026"]


def test_year_labels_phrasing_is_recognized_as_yoy():
    """#146 그 자체 — "2025년 라벨1 2026년 라벨2" 는 연도별로 나눠 달라는 뜻이다."""
    q = "남아메리카 2025년 라벨1 2026년 라벨2 / 월별로 중아메리카도 시계열 그래프 그려줘"
    assert chart.infer_chart_intent(q, []) == "yoy"
    _, out = _build(q, _rows())
    assert out is not None
    labels = [d["label"] for d in out["data"]["datasets"]]
    assert any("2025" in l for l in labels) and any("2026" in l for l in labels), labels


@pytest.mark.parametrize("q", [
    "2025년 1월부터 2026년 6월까지 월별 매출 추이",
    "2025년 1월 ~ 2026년 6월 매출 시계열로 보여줘",
    "2025-01 부터 2026-06 까지 월별 매출",
])
def test_a_date_range_is_not_a_year_comparison(q):
    """⛔ 연도 두 개가 보인다고 YoY 가 아니다 — 기간 범위를 쪼개면 없던 비교가 생긴다."""
    assert chart.infer_chart_intent(q, []) == "trend"


def test_readability_limit_still_drops_genuinely_huge_charts():
    """상한 자체는 남는다 — 순서만 고친 것이지 제한을 없앤 게 아니다."""
    rows = [{"month": f"2025-{(i % 12) + 1:02d}", "continent": f"C{i}",
             "revenue": 1.0} for i in range(400)]
    _, out = _build("YoY로", rows)
    assert out is None


def test_wide_yoy_table_is_not_transposed():
    """⛔ 축이 뒤집혀 **월이 선**이 되던 것 (프로덕션 실측, 2026-08-25).

    LLM 이 YoY 를 넓은(wide) 형태로 뽑는 경우가 있다 — 행은 월, 열은 계열:

        month | south_america_2025_sales | south_america_2026_sales | ...
        '01'  | ...                      | ...

    전치 규칙은 "제품 × 분기" 처럼 **행이 엔티티**인 표를 위한 것인데, 여기서는
    행이 시간축이다. 그런데 x 값이 `'01'`·`'08'` 같은 **맨숫자**라 시간처럼 안 보였고,
    열 이름에는 `2025`·`2026` 이 있어 시간처럼 보였다 — 그래서 뒤집혔다.
    결과: x축이 `['south america 2025', …]`, 선이 `'08'`·`'07'` 인 차트.

    ⚠️ x **컬럼 이름**이 기간을 가리키면(`month`·`분기`·`year`) 행이 시간축이다.
    """
    rows = [{"month": f"{m:02d}",
             "south_america_2025_sales": 1e9 * m,
             "south_america_2026_sales": 2e9 * m,
             "central_america_2025_sales": 3e8 * m,
             "central_america_2026_sales": 4e8 * m} for m in range(1, 13)]
    cfg = {"chart_type": "line", "x_column": "month",
           "y_column": ["south_america_2025_sales", "south_america_2026_sales",
                        "central_america_2025_sales", "central_america_2026_sales"],
           "title": "t"}
    out = json.loads(chart.build_chartjs_config(cfg, rows))
    assert out["data"]["labels"][:3] == ["01", "02", "03"], out["data"]["labels"][:5]
    labels = [d["label"] for d in out["data"]["datasets"]]
    assert len(labels) == 4, labels
    assert all("america" in l.lower() for l in labels), labels


def test_entity_by_period_table_is_still_transposed():
    """⚠️ 원래 의도는 살린다 — 행이 제품이면 전치가 맞다 (제품명이 x축에 깔리면 겹친다)."""
    rows = [{"product": f"P{i}", "2025 Q1 sales": 1e9, "2025 Q2 sales": 2e9} for i in range(3)]
    cfg = {"chart_type": "line", "x_column": "product",
           "y_column": ["2025 Q1 sales", "2025 Q2 sales"], "title": "t"}
    out = json.loads(chart.build_chartjs_config(cfg, rows))
    assert [d["label"] for d in out["data"]["datasets"]] == ["P0", "P1", "P2"]
