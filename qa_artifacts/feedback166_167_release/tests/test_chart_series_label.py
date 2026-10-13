# -*- coding: utf-8 -*-
"""차트 시리즈 라벨은 **측정값**을 가리켜야 한다 — 붐따 #138 (양승민, 2026-08-19).

    질문: "Top 10 제품 매출 순위 알려줘"
    제보: "시각화 대시보드에 제품명:숫자 로 나와요"

저장된 차트 config 를 꺼내 보니 원인이 그대로 있었다:

    {"datasets": [{"label": "제품명", "data": [25541542844.6, ...]}]}

`label` 은 Chart.js 가 툴팁·범례에 `<label>: <값>` 으로 그리는 **시리즈 이름**이다.
거기에 x축이 무엇인지(`제품명`)를 넣으면 화면에 "제품명: 25,541,542,845" 가 뜬다.
그 자리에 와야 할 것은 "총 매출" 이다.

⛔ 코드가 LLM 이 준 `y_label` 을 그대로 믿었다. 라벨은 숫자가 아니라 **말**이라
   틀려도 에러가 안 나고, 표는 멀쩡한데 차트만 이상해 보인다.
   측정값을 가리키는 말인지 코드가 확인한다 — 아니면 실제로 그린 컬럼명을 쓴다.
"""
import json

import pytest

from app.core import chart

ROWS = [{"product_name": f"SK_Product_{i}", "total_revenue": 1e10 - i * 1e9}
        for i in range(5)]


def _label(y_label):
    cfg = {"chart_type": "bar", "x_column": "product_name",
           "y_column": "total_revenue", "y_label": y_label, "title": "t"}
    out = json.loads(chart.build_chartjs_config(cfg, ROWS))
    return out["data"]["datasets"][0]["label"]


def test_x_axis_name_never_becomes_the_series_label():
    """#138 그 자체 — '제품명: 255억' 이 뜨면 안 된다."""
    assert _label("제품명") != "제품명"
    assert _label("제품명") == "total_revenue"


@pytest.mark.parametrize("bad", ["제품명", "국가", "국가명", "채널", "팀", "월"])
def test_category_words_are_rejected(bad):
    """축·범주를 가리키는 말은 시리즈 이름이 될 수 없다."""
    assert _label(bad) == "total_revenue"


@pytest.mark.parametrize("good", ["총 매출", "매출액(원)", "판매수량", "광고비",
                                  "비중(%)", "Revenue", "total sales"])
def test_measure_words_are_kept(good):
    """측정값을 가리키는 말은 그대로 쓴다 — 영문 컬럼명보다 읽기 좋다."""
    assert _label(good) == good


def test_missing_label_falls_back_to_the_column():
    assert _label("") == "total_revenue"


def test_multi_series_labels_are_untouched():
    """⛔ 여러 시리즈일 때 라벨은 그룹 값(연도·대륙)이다 — 이 규칙을 적용하면 안 된다."""
    rows = []
    for c in ("남아메리카", "중앙아메리카"):
        for m in range(1, 7):
            rows.append({"month": f"2026-{m:02d}", "continent": c, "revenue": 1e9})
    cfg = chart.apply_chart_intent(
        {"chart_type": "line", "x_column": "month", "y_column": "revenue",
         "group_column": "continent", "y_label": "매출", "title": "t"},
        "월별 매출 추이", rows)
    out = json.loads(chart.build_chartjs_config(cfg, rows))
    labels = [d["label"] for d in out["data"]["datasets"]]
    assert set(labels) == {"남아메리카", "중앙아메리카"}


def test_y_axis_title_is_guarded_too():
    """⛔ 매출 축에 '제품명' 이 축 제목으로 붙으면 축이 거짓말을 한다."""
    cfg = {"chart_type": "bar", "x_column": "product_name",
           "y_column": "total_revenue", "y_label": "제품명", "title": "t"}
    out = json.loads(chart.build_chartjs_config(cfg, ROWS))
    title = out["options"]["scales"]["y"]["title"]["text"]
    assert title != "제품명"
