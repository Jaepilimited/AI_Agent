"""Chart generation module — outputs Chart.js config JSON.

Instead of rendering server-side PNGs (slow, static), this module builds
Chart.js configuration objects that the frontend renders interactively
with animations, tooltips, responsive sizing, and theme-aware colors.

Flow: LLM decides chart config → build_chartjs_config() → JSON in markdown
      → frontend detectAndRenderCharts() → Chart.js canvas
"""

import json
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# Modern color palette — vibrant, accessible, distinct
COLORS = [
    "rgba(99, 102, 241, 0.85)",   # Indigo
    "rgba(245, 158, 11, 0.85)",   # Amber
    "rgba(16, 185, 129, 0.85)",   # Emerald
    "rgba(239, 68, 68, 0.85)",    # Red
    "rgba(139, 92, 246, 0.85)",   # Violet
    "rgba(6, 182, 212, 0.85)",    # Cyan
    "rgba(249, 115, 22, 0.85)",   # Orange
    "rgba(132, 204, 22, 0.85)",   # Lime
    "rgba(236, 72, 153, 0.85)",   # Pink
    "rgba(20, 184, 166, 0.85)",   # Teal
    "rgba(59, 130, 246, 0.85)",   # Blue
    "rgba(168, 85, 247, 0.85)",   # Purple
]

# Solid (opacity=1) variants — used for borders and point fills
COLORS_SOLID = [c.replace("0.85)", "1)") for c in COLORS]
BORDERS = COLORS_SOLID  # alias for backward compat


def _format_short(val: float) -> str:
    """Short format for data labels."""
    if abs(val) >= 1e9:
        return f"{val / 1e9:.1f}B"
    elif abs(val) >= 1e6:
        return f"{val / 1e6:.1f}M"
    elif abs(val) >= 1e3:
        return f"{val / 1e3:.0f}K"
    else:
        return f"{val:,.0f}"


def _find_numeric_column(data: List[Dict], exclude: List[str]) -> Optional[str]:
    """Find a numeric column in the data, excluding specified columns."""
    if not data:
        return None
    row = data[0]
    for col, val in row.items():
        if col in exclude:
            continue
        try:
            float(val if val is not None else 0)
            return col
        except (ValueError, TypeError):
            continue
    return None


def _pivot_grouped_data(data: List[Dict], x_col: str, y_col: str, group_col: str):
    """Pivot long-format grouped data into {group: [values]} for multi-series."""
    x_order = list(OrderedDict.fromkeys(str(row.get(x_col, "")) for row in data))
    groups = list(OrderedDict.fromkeys(str(row.get(group_col, "")) for row in data))

    pivot = {x: {} for x in x_order}
    group_totals = {g: 0.0 for g in groups}

    for row in data:
        x = str(row.get(x_col, ""))
        g = str(row.get(group_col, ""))
        v = float(row.get(y_col, 0) or 0)
        pivot[x][g] = v
        group_totals[g] += v

    # Sort groups by total (descending), limit to top 10 for chart readability
    groups = sorted(groups, key=lambda g: group_totals[g], reverse=True)[:10]

    return x_order, groups, pivot


def build_chartjs_config(
    chart_config: Dict[str, Any],
    data: List[Dict[str, Any]],
) -> Optional[str]:
    """Build a Chart.js configuration JSON string.

    Returns:
        JSON string for Chart.js, or None if generation fails.
    """
    try:
        chart_type = chart_config.get("chart_type", "bar")
        x_col = chart_config.get("x_column", "")
        y_col = chart_config.get("y_column", "")
        title = chart_config.get("title", "")
        x_label = chart_config.get("x_label", "")
        y_label = chart_config.get("y_label", "")
        group_col = chart_config.get("group_column")

        if not data or not x_col or not y_col:
            return None

        # Validate x_column exists in data — auto-fix if not found
        data_keys = list(data[0].keys()) if data else []
        if x_col not in data_keys:
            # Try case-insensitive match
            x_lower = x_col.lower()
            matched = [k for k in data_keys if k.lower() == x_lower]
            if matched:
                x_col = matched[0]
            else:
                # Pick first non-numeric column as x
                for k in data_keys:
                    try:
                        float(data[0].get(k, ""))
                    except (ValueError, TypeError):
                        x_col = k
                        break

        if isinstance(y_col, str) and y_col not in data_keys:
            y_lower = y_col.lower()
            matched = [k for k in data_keys if k.lower() == y_lower]
            if matched:
                y_col = matched[0]
            else:
                y_col = _find_numeric_column(data, exclude=[x_col]) or y_col

        # Validate y_column is numeric — auto-fix if needed
        if isinstance(y_col, str):
            sample_val = data[0].get(y_col)
            try:
                float(sample_val if sample_val is not None else 0)
            except (ValueError, TypeError):
                sample_x = data[0].get(x_col)
                try:
                    float(sample_x if sample_x is not None else "")
                    x_col, y_col = y_col, x_col
                except (ValueError, TypeError):
                    fixed = _find_numeric_column(data, exclude=[x_col, y_col])
                    if fixed:
                        x_col = y_col
                        y_col = fixed
                    else:
                        return None

        # Readability limits
        max_items = {"bar": 15, "horizontal_bar": 20, "pie": 12, "line": 36}
        limit = max_items.get(chart_type, 20)

        # For grouped charts, check unique x-values (not total rows)
        if group_col and chart_type == "line":
            unique_x = len(set(str(row.get(x_col, "")) for row in data))
            unique_g = len(set(str(row.get(group_col, "")) for row in data))
            if unique_x > 36 or unique_g > 15:
                return None  # Too many periods or groups
        elif chart_type == "pie" and len(data) > 10:
            _pie_y = y_col if isinstance(y_col, str) else y_col[0]
            data_sorted = sorted(data, key=lambda r: float(r.get(_pie_y, 0) or 0), reverse=True)
            top_items = data_sorted[:9]
            others_val = sum(float(r.get(_pie_y, 0) or 0) for r in data_sorted[9:])
            data = top_items + [{x_col: "기타", _pie_y: others_val}]
        elif len(data) > limit:
            return None

        # --- Build Chart.js config ---
        labels = [str(row.get(x_col, "")) for row in data]
        datasets = []

        # Handle grouped data (pivot)
        if group_col and isinstance(y_col, str):
            x_order, groups, pivot = _pivot_grouped_data(data, x_col, y_col, group_col)
            labels = x_order
            for i, g in enumerate(groups):
                color = COLORS[i % len(COLORS)]
                border = COLORS_SOLID[i % len(COLORS_SOLID)]
                values = [pivot[x].get(g, 0) for x in x_order]
                ds = {
                    "label": g,
                    "data": values,
                    "backgroundColor": color,
                    "borderColor": border,
                    "borderWidth": 2,
                }
                if chart_type == "line":
                    ds["fill"] = False
                    ds["tension"] = 0.35
                    ds["pointRadius"] = 5
                    ds["pointHoverRadius"] = 8
                    ds["pointBackgroundColor"] = border
                    ds["borderWidth"] = 2.5
                datasets.append(ds)

        elif isinstance(y_col, list):
            # Multiple y columns
            for i, col in enumerate(y_col):
                color = COLORS[i % len(COLORS)]
                border = COLORS_SOLID[i % len(COLORS_SOLID)]
                values = [float(row.get(col, 0) or 0) for row in data]
                ds = {
                    "label": col,
                    "data": values,
                    "backgroundColor": color,
                    "borderColor": border,
                    "borderWidth": 2,
                }
                if chart_type == "line":
                    ds["fill"] = False
                    ds["tension"] = 0.35
                    ds["pointRadius"] = 5
                    ds["pointHoverRadius"] = 8
                    ds["pointBackgroundColor"] = border
                    ds["borderWidth"] = 2.5
                datasets.append(ds)
        else:
            # Single series
            values = [float(row.get(y_col, 0) or 0) for row in data]

            if chart_type == "pie":
                datasets.append({
                    "data": values,
                    "backgroundColor": COLORS[:len(values)],
                    "borderColor": ["rgba(255,255,255,0.8)"] * len(values),
                    "borderWidth": 2,
                    "hoverOffset": 8,
                })
            else:
                # Bar/line with gradient effect via per-bar colors
                if chart_type in ("bar", "horizontal_bar") and len(values) <= 15:
                    bg = COLORS[:len(values)]
                    bd = BORDERS[:len(values)]
                else:
                    bg = COLORS[0]
                    bd = BORDERS[0]

                ds = {
                    "label": y_label or y_col,
                    "data": values,
                    "backgroundColor": bg,
                    "borderColor": bd,
                    "borderWidth": 2,
                    "borderRadius": 6,
                }
                if chart_type == "line":
                    ds["fill"] = "origin"
                    ds["backgroundColor"] = COLORS[0].replace("0.85)", "0.15)")
                    ds["borderColor"] = COLORS_SOLID[0]
                    ds["tension"] = 0.35
                    ds["pointRadius"] = 5
                    ds["pointHoverRadius"] = 8
                    ds["pointBackgroundColor"] = COLORS_SOLID[0]
                    ds["borderWidth"] = 2.5
                datasets.append(ds)

        # Sort bars by value (descending) for non-time-series
        _TIME_HINTS = {"월", "년", "분기", "주차", "week", "month", "quarter",
                       "jan", "feb", "mar", "apr", "may", "jun",
                       "jul", "aug", "sep", "oct", "nov", "dec"}
        is_time_series = any(
            any(h in str(x).lower() for h in _TIME_HINTS) for x in labels
        )

        if not is_time_series and chart_type in ("bar", "horizontal_bar") and len(datasets) == 1:
            pairs = list(zip(labels, datasets[0]["data"]))
            pairs.sort(key=lambda p: p[1], reverse=True)
            labels = [p[0] for p in pairs]
            datasets[0]["data"] = [p[1] for p in pairs]
            if isinstance(datasets[0].get("backgroundColor"), list):
                # Keep color mapping consistent after sort
                datasets[0]["backgroundColor"] = COLORS[:len(labels)]
                datasets[0]["borderColor"] = BORDERS[:len(labels)]

        # Map chart types to Chart.js types
        cjs_type = {
            "bar": "bar",
            "horizontal_bar": "bar",
            "line": "line",
            "pie": "doughnut",  # Doughnut looks more modern than pie
            "grouped_bar": "bar",
            "stacked_bar": "bar",
        }.get(chart_type, "bar")

        is_horizontal = chart_type == "horizontal_bar"

        # Build config
        config = {
            "type": cjs_type,
            "data": {
                "labels": labels,
                "datasets": datasets,
            },
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "animation": {
                    "duration": 800,
                    "easing": "easeOutQuart",
                },
                "plugins": {
                    "title": {
                        "display": bool(title),
                        "text": title,
                        "font": {"size": 16, "weight": "bold"},
                        "padding": {"bottom": 16},
                    },
                    "legend": {
                        "display": len(datasets) > 1 or chart_type == "pie",
                        "position": "top",
                        "labels": {
                            "usePointStyle": True,
                            "padding": 16,
                            "font": {"size": 12},
                        },
                    },
                    "tooltip": {
                        "backgroundColor": "rgba(0,0,0,0.8)",
                        "titleFont": {"size": 13},
                        "bodyFont": {"size": 12},
                        "cornerRadius": 8,
                        "padding": 12,
                        "displayColors": True,
                    },
                },
            },
        }

        # Axis config (not for pie/doughnut)
        if cjs_type != "doughnut":
            x_axis = {
                "title": {"display": bool(x_label), "text": x_label, "font": {"size": 13}},
                "grid": {"display": False},
                "ticks": {"font": {"size": 11}},
            }
            y_axis = {
                "title": {"display": bool(y_label), "text": y_label, "font": {"size": 13}},
                "grid": {"color": "rgba(0,0,0,0.06)"},
                "ticks": {"font": {"size": 11}},
                "beginAtZero": True,
            }

            if is_horizontal:
                config["options"]["indexAxis"] = "y"
                x_axis, y_axis = y_axis, x_axis
                y_axis["ticks"]["font"] = {"size": 11}

            # Rotate x labels if many items
            if len(labels) > 8 and not is_horizontal:
                x_axis["ticks"]["maxRotation"] = 45

            config["options"]["scales"] = {"x": x_axis, "y": y_axis}

            # Stacked
            if chart_type == "stacked_bar":
                config["options"]["scales"]["x"]["stacked"] = True
                config["options"]["scales"]["y"]["stacked"] = True

        else:
            # Doughnut options
            config["options"]["cutout"] = "55%"
            config["options"]["plugins"]["legend"]["position"] = "right"

        logger.info("chartjs_config_built", chart_type=chart_type, labels=len(labels), datasets=len(datasets))
        return json.dumps(config, ensure_ascii=False)

    except Exception as e:
        logger.error("chartjs_config_failed", error=str(e))
        return None


def get_chart_config_prompt(query: str, sql: str, results_preview: str, row_count: int) -> str:
    """Build a prompt for the LLM to decide chart configuration."""
    return f"""사용자의 질문과 SQL 결과를 분석해서 시각화가 적절한지 판단하고, 차트 설정을 JSON으로 반환하세요.

## 사용자 질문
{query}

## SQL
{sql}

## 결과 ({row_count}행)
{results_preview}

## 판단 기준
- 사용자가 "시각화", "차트", "그래프", "보여줘", "비교", "추이", "트렌드" 등을 요청하면 차트를 생성
- 비교 데이터(국가별, 월별, 플랫폼별 등)는 차트가 효과적
- 단일 숫자 결과(총 매출 1개)는 차트 불필요
- 결과가 2행 이상이고 숫자 컬럼이 있으면 차트 권장

## ⚠️ 가독성 판단 — 아래 경우 needs_chart: false
- bar 차트: 카테고리가 15개 초과
- horizontal_bar: 항목이 20개 초과
- pie/donut: 항목이 많아도 OK (시스템이 자동으로 Top 9 + 기타로 집계)
- line: x축 고유값 36개 초과. 단, group_column이 있으면 x축 고유값 기준으로 판단

## 차트 타입 선택 (우선순위)
- **line**: 월별/일별/분기별 추이, 시계열 데이터는 **반드시 line** 사용. "월별", "추이", "트렌드" 키워드가 있으면 무조건 line.
  - 그룹별(국가별/몰별/브랜드별) 비교가 있으면 group_column 지정 → 멀티라인 차트
  - 단일 시계열이면 영역(fill) 라인 차트
- bar: 카테고리별 비교 — **카테고리 5개 이하 + 이름이 짧을 때만** (시계열 아닐 때만)
- **horizontal_bar**: 제품명, 브랜드명, SKU명 등 긴 텍스트 라벨이면 **반드시 사용**
- pie: 비율/구성 (전체 대비 비중). 항목 6개 이하
- grouped_bar: 여러 지표를 카테고리별로 비교 (시계열 아닐 때만)
- stacked_bar: 누적 비교

## ⚠️ 핵심: 시계열(월별/일별/분기별) 데이터는 line 차트가 기본. bar로 선택하지 마세요.

## 반환 JSON 형식
{{
  "needs_chart": true/false,
  "chart_type": "bar|horizontal_bar|line|pie|grouped_bar|stacked_bar",
  "x_column": "결과 컬럼명",
  "y_column": "결과 컬럼명" 또는 ["컬럼1", "컬럼2"],
  "group_column": "그룹 컬럼명 (없으면 null)",
  "title": "차트 제목 (한국어)",
  "x_label": "X축 라벨",
  "y_label": "Y축 라벨"
}}

needs_chart가 false이면 다른 필드는 비워두세요."""
