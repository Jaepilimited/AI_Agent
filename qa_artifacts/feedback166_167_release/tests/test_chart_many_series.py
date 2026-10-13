# -*- coding: utf-8 -*-
"""시리즈가 많을 때 차트가 조용히 사라지지 않는다 — 붐따 #165.

2026-09-04 프로덕션: "라인이 랩인네이처인 2026년 월별 매출 … 제품별로도" 뒤에
"시각화도" 를 물었더니 차트가 **통째로 나오지 않았다**. 로그는
`too_many_series unique_x=10 unique_groups=19` 였고, 화면에는 아무 말도 없었다.
사용자는 세 번 다시 물었다.

같은 함수에 조용한 실패가 하나 더 있었다 — `_pivot_grouped_data` 가 그룹을
`[:10]` 으로 잘라, 상한을 통과한 11~15개짜리 차트에서 **시리즈가 말없이 사라졌다**.
"""

import json

import pytest

from app.core import chart


def _monthly_rows(n_products: int, n_months: int = 10, base: float = 1_000_000.0):
    """월 × 제품 긴 형식 — #165 가 실제로 받은 모양."""
    rows = []
    for m in range(1, n_months + 1):
        for p in range(n_products):
            rows.append({
                "month": f"2026-{m:02d}",
                "product_name": f"P{p:02d}",
                "total_revenue": base * (n_products - p) + m,
            })
    return rows


CFG = {
    "chart_type": "line",
    "x_column": "month",
    "y_column": "total_revenue",
    "group_column": "product_name",
    "title": "월별 제품 매출",
    "y_label": "매출액",
}


def _series(out):
    return {ds["label"]: ds["data"] for ds in json.loads(out)["data"]["datasets"]}


def test_붐따165_제품19개_월별차트가_사라지지_않는다():
    rows = _monthly_rows(19)
    notes = []
    out = chart.build_chartjs_config(dict(CFG), rows, notes=notes)
    assert out is not None, "차트가 통째로 사라졌다 — 붐따 #165 재발"
    series = _series(out)
    assert len(series) <= chart.MAX_SERIES
    assert any(s.startswith("기타") for s in series), (
        "나머지 제품이 '기타' 로 묶이지 않고 사라졌다")


def test_접었어도_각_시점_합계는_보존된다():
    """'기타' 는 버린 것이 아니라 **합친 것**이다 — 합이 틀리면 접으면 안 된다."""
    rows = _monthly_rows(19)
    out = chart.build_chartjs_config(dict(CFG), rows, notes=[])
    parsed = json.loads(out)
    labels = parsed["data"]["labels"]
    series = _series(out)
    for i, month in enumerate(labels):
        drawn = sum(vals[i] for vals in series.values())
        raw = sum(r["total_revenue"] for r in rows if r["month"] == month)
        assert drawn == pytest.approx(raw, rel=1e-9), f"{month} 합계가 어긋난다"


def test_상한_이하에서도_시리즈가_조용히_잘리지_않는다():
    """예전 `_pivot_grouped_data` 의 `[:10]` — 12개 중 2개가 말없이 사라졌다."""
    rows = _monthly_rows(12)
    out = chart.build_chartjs_config(dict(CFG), rows, notes=[])
    series = _series(out)
    drawn_products = {s for s in series if not s.startswith("기타")}
    assert len(series) <= chart.MAX_SERIES
    # 잘렸다면 잘린 만큼이 '기타' 로 남아 있어야 한다 (조용히 없어지면 안 된다)
    if len(drawn_products) < 12:
        assert any(s.startswith("기타") for s in series)


def test_비율_지표는_합치지_않고_제외사실을_적는다():
    """증감률을 더해 '기타' 를 만들면 통계처럼 생긴 거짓말이 된다."""
    rows = []
    for m in range(1, 11):
        for p in range(19):
            rows.append({"month": f"2026-{m:02d}", "product_name": f"P{p:02d}",
                         "growth_rate": 1.0 + p})
    cfg = dict(CFG, y_column="growth_rate", y_label="증감률")
    notes = []
    out = chart.build_chartjs_config(cfg, rows, notes=notes)
    assert out is not None
    series = _series(out)
    assert not any(s.startswith("기타") for s in series), "비율을 합쳤다"
    assert notes and any("제외" in n for n in notes), (
        "그린 것이 전부가 아닌데 아무 말도 하지 않았다")


def test_기간이_너무_많으면_그리지_않되_이유를_남긴다():
    rows = _monthly_rows(3, n_months=40)
    notes = []
    out = chart.build_chartjs_config(dict(CFG), rows, notes=notes)
    assert out is None
    assert notes, "차트를 못 그렸는데 사유가 비어 있다"


def test_항목이_너무_많은_단일계열도_이유를_남긴다():
    rows = [{"product_name": f"P{i:03d}", "total_revenue": float(i)} for i in range(60)]
    cfg = {"chart_type": "bar", "x_column": "product_name",
           "y_column": "total_revenue", "title": "제품별"}
    notes = []
    assert chart.build_chartjs_config(cfg, rows, notes=notes) is None
    assert notes


def test_notes_없이_불러도_예전처럼_동작한다():
    """기존 호출부·테스트가 그대로 살아 있어야 한다."""
    rows = _monthly_rows(3)
    assert chart.build_chartjs_config(dict(CFG), rows) is not None


def test_너무_잘게_흩어져_있으면_접지_않고_이유를_말한다():
    """'기타' 가 97%면 그 차트는 상위가 아니라 기타 하나를 보여주는 것이다."""
    rows = [{"month": f"2026-{(i % 10) + 1:02d}", "product_name": f"P{i:03d}",
             "total_revenue": 1.0} for i in range(400)]
    notes = []
    assert chart.build_chartjs_config(dict(CFG), rows, notes=notes) is None
    assert notes and "상위" in notes[0], "생략만 하고 다음 수를 알려주지 않았다"


def test_고르게_나뉘어도_상위가_이야기를_가지면_그린다():
    """상위 9개가 전체의 10% 이상이면 접어서 그린다 (문턱을 과하게 잡지 않는다)."""
    rows = []
    for m in range(1, 11):
        for p in range(30):
            rows.append({"month": f"2026-{m:02d}", "product_name": f"P{p:02d}",
                         "total_revenue": float(30 - p)})
    notes = []
    out = chart.build_chartjs_config(dict(CFG), rows, notes=notes)
    assert out is not None, "30개 정도는 접어서 그려야 한다"
    assert any(s.startswith("기타") for s in _series(out))
