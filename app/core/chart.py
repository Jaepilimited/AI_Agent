"""Chart generation module using Plotly.

ChatGPT-style clean charts with data labels.
Saves as PNG files served via FastAPI static endpoint.
"""

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
import plotly.graph_objects as go

logger = structlog.get_logger(__name__)

# --- ChatGPT-style Light Theme ---
BG_COLOR = "#ffffff"
PLOT_BG = "#ffffff"
GRID_COLOR = "#e5e5e5"
TEXT_COLOR = "#374151"
TITLE_COLOR = "#111827"
LABEL_COLOR = "#6b7280"

# 30 distinct colors for data series (no duplicates)
COLORS = [
    "#6366f1",  # Indigo
    "#f59e0b",  # Amber
    "#10b981",  # Emerald
    "#ef4444",  # Red
    "#8b5cf6",  # Violet
    "#06b6d4",  # Cyan
    "#f97316",  # Orange
    "#84cc16",  # Lime
    "#ec4899",  # Pink
    "#14b8a6",  # Teal
    "#3b82f6",  # Blue
    "#a855f7",  # Purple
    "#22c55e",  # Green
    "#eab308",  # Yellow
    "#f43f5e",  # Rose
    "#0ea5e9",  # Sky
    "#d946ef",  # Fuchsia
    "#64748b",  # Slate
    "#78716c",  # Stone
    "#dc2626",  # Red-600
    "#2563eb",  # Blue-600
    "#16a34a",  # Green-600
    "#ca8a04",  # Yellow-600
    "#9333ea",  # Purple-600
    "#0891b2",  # Cyan-600
    "#ea580c",  # Orange-600
    "#be185d",  # Pink-600
    "#4f46e5",  # Indigo-600
    "#059669",  # Emerald-600
    "#7c3aed",  # Violet-600
]

CHARTS_DIR = Path(__file__).resolve().parent.parent / "static" / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def _format_number(val: float) -> str:
    """Format large numbers with Korean units (no decimals)."""
    if abs(val) >= 1e8:
        return f"{int(val / 1e8):,}억"
    elif abs(val) >= 1e4:
        return f"{int(val / 1e4):,}만"
    else:
        return f"{int(val):,}"


def _format_short(val: float) -> str:
    """Short format for data labels on chart (no decimals)."""
    if abs(val) >= 1e9:
        return f"{int(val / 1e9)}B"
    elif abs(val) >= 1e6:
        return f"{int(val / 1e6)}M"
    elif abs(val) >= 1e3:
        return f"{int(val / 1e3)}K"
    else:
        return f"{int(val):,}"


def _find_numeric_column(
    data: List[Dict], exclude: List[str]
) -> Optional[str]:
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


def _pivot_grouped_data(
    data: List[Dict], x_col: str, y_col: str, group_col: str
) -> tuple:
    """Pivot long-format grouped data into wide format for multi-series charts.

    Groups are sorted by total value (descending) for legend ordering.
    """
    from collections import OrderedDict

    try:
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

        # Sort groups by total value (descending)
        groups = sorted(groups, key=lambda g: group_totals[g], reverse=True)

        wide_data = []
        for x in x_order:
            row = {x_col: x}
            for g in groups:
                row[g] = pivot[x].get(g, 0)
            wide_data.append(row)

        logger.info("pivot_grouped_data", x_count=len(x_order), groups=len(groups))
        return wide_data, groups
    except Exception as e:
        logger.error("pivot_failed", error=str(e))
        return [], []


def generate_chart(
    chart_config: Dict[str, Any],
    data: List[Dict[str, Any]],
) -> Optional[str]:
    """Generate a clean ChatGPT-style chart as PNG.

    Returns:
        Filename of saved PNG, or None if generation fails.
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

        # Validate y_column is numeric — auto-fix if LLM picked a string column
        if isinstance(y_col, str):
            sample_val = data[0].get(y_col)
            try:
                float(sample_val if sample_val is not None else 0)
            except (ValueError, TypeError):
                # y_col contains strings — try to find a numeric column instead
                logger.warning("chart_y_col_not_numeric", y_col=y_col, sample=str(sample_val)[:50])
                fixed = _find_numeric_column(data, exclude=[x_col, y_col])
                if fixed:
                    # Swap: old y_col might be the label, use it as x_col
                    logger.info("chart_y_col_auto_fixed", old_y=y_col, new_y=fixed)
                    if x_col == fixed:
                        x_col = y_col
                    y_col = fixed
                else:
                    logger.warning("chart_no_numeric_column_found")
                    return None

        # Pivot grouped data
        if group_col and isinstance(y_col, str):
            data, y_col = _pivot_grouped_data(data, x_col, y_col, group_col)
            if not data:
                return None

        # Readability guard
        max_items = {"bar": 15, "horizontal_bar": 20, "pie": 10, "line": 36}
        limit = max_items.get(chart_type, 20)
        if len(data) > limit:
            logger.info("chart_skipped_too_many_items", chart_type=chart_type, items=len(data))
            return None

        x_values = [str(row.get(x_col, "")) for row in data]

        if isinstance(y_col, list):
            y_series = {col: [float(row.get(col, 0) or 0) for row in data] for col in y_col}
        else:
            y_values = [float(row.get(y_col, 0) or 0) for row in data]

        fig = go.Figure()

        # Calculate legend item count for layout adjustment
        legend_count = len(y_col) if isinstance(y_col, list) else 1

        # --- LINE CHART ---
        if chart_type == "line":
            if isinstance(y_col, list):
                for i, col in enumerate(y_col):
                    color = COLORS[i % len(COLORS)]
                    fig.add_trace(go.Scatter(
                        x=x_values,
                        y=y_series[col],
                        mode="lines+markers+text",
                        name=col,
                        line=dict(color=color, width=2.5),
                        marker=dict(size=8, color=color),
                        text=[_format_short(v) for v in y_series[col]],
                        textposition="top center",
                        textfont=dict(size=9, color=color),
                        hovertemplate=f"<b>{col}</b><br>%{{x}}: %{{y:,.0f}}<extra></extra>",
                    ))
            else:
                color = COLORS[0]
                fig.add_trace(go.Scatter(
                    x=x_values,
                    y=y_values,
                    mode="lines+markers+text",
                    name=y_col if y_col else "값",
                    line=dict(color=color, width=3),
                    marker=dict(size=10, color=color),
                    text=[_format_short(v) for v in y_values],
                    textposition="top center",
                    textfont=dict(size=10, color=TEXT_COLOR, family="Arial"),
                    hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
                ))

        # --- BAR CHART ---
        elif chart_type == "bar":
            fig.add_trace(go.Bar(
                x=x_values,
                y=y_values,
                marker_color=COLORS[0],
                text=[_format_short(v) for v in y_values],
                textposition="outside",
                textfont=dict(size=11, color=TEXT_COLOR, family="Arial"),
                hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
            ))

        # --- HORIZONTAL BAR ---
        elif chart_type == "horizontal_bar":
            # Sort by value for better readability
            sorted_pairs = sorted(zip(x_values, y_values), key=lambda x: x[1], reverse=True)
            x_values, y_values = zip(*sorted_pairs) if sorted_pairs else ([], [])

            fig.add_trace(go.Bar(
                x=list(y_values),
                y=list(x_values),
                orientation="h",
                marker_color=COLORS[0],
                text=[_format_short(v) for v in y_values],
                textposition="outside",
                textfont=dict(size=10, color=TEXT_COLOR),
                hovertemplate="%{y}<br>%{x:,.0f}<extra></extra>",
            ))

        # --- PIE/DONUT ---
        elif chart_type == "pie":
            filtered = [(x, y) for x, y in zip(x_values, y_values) if y > 0]
            if not filtered:
                return None
            pie_labels, pie_values = zip(*filtered)

            fig.add_trace(go.Pie(
                labels=pie_labels,
                values=pie_values,
                hole=0.45,
                marker=dict(colors=COLORS[:len(pie_values)], line=dict(color=BG_COLOR, width=2)),
                textinfo="label+percent",
                textposition="outside",
                textfont=dict(size=11, color=TEXT_COLOR),
                texttemplate="%{label}<br>%{percent:.1%}",
                hovertemplate="%{label}<br>%{value:,} (%{percent:.1%})<extra></extra>",
                pull=[0.02] * len(pie_values),
            ))
            legend_count = len(pie_values)

        # --- STACKED BAR ---
        elif chart_type == "stacked_bar" and isinstance(y_col, list):
            for i, col in enumerate(y_col):
                fig.add_trace(go.Bar(
                    x=x_values,
                    y=y_series[col],
                    name=col,
                    marker_color=COLORS[i % len(COLORS)],
                    text=[_format_short(v) if v > 0 else "" for v in y_series[col]],
                    textposition="inside",
                    textfont=dict(size=9, color="white"),
                    hovertemplate=f"<b>{col}</b><br>%{{x}}: %{{y:,.0f}}<extra></extra>",
                ))
            fig.update_layout(barmode="stack")

        # --- GROUPED BAR ---
        elif chart_type == "grouped_bar" and isinstance(y_col, list):
            for i, col in enumerate(y_col):
                fig.add_trace(go.Bar(
                    x=x_values,
                    y=y_series[col],
                    name=col,
                    marker_color=COLORS[i % len(COLORS)],
                    text=[_format_short(v) for v in y_series[col]],
                    textposition="outside",
                    textfont=dict(size=9, color=TEXT_COLOR),
                    hovertemplate=f"<b>{col}</b><br>%{{x}}: %{{y:,.0f}}<extra></extra>",
                ))
            fig.update_layout(barmode="group")

        # --- LAYOUT ---
        # Calculate margins and image size based on legend count
        top_margin = 80
        bottom_margin = 80
        right_margin = 40
        img_width = 1000
        img_height = 600

        if len(x_values) > 8 or any(len(str(x)) > 10 for x in x_values):
            bottom_margin = 100  # More space for rotated labels

        # Adjust for many legend items
        if legend_count > 10:
            # Large legend - use wider image with legend on right
            right_margin = 200
            img_width = 1300
            img_height = 700
        elif legend_count > 5:
            right_margin = 180
            img_width = 1200

        layout = dict(
            title=dict(
                text=f"<b>{title}</b>" if title else "",
                font=dict(size=18, color=TITLE_COLOR, family="Arial"),
                x=0.5,
                xanchor="center",
                y=0.98,
                yanchor="top",
            ),
            paper_bgcolor=BG_COLOR,
            plot_bgcolor=PLOT_BG,
            font=dict(color=TEXT_COLOR, family="Arial"),
            margin=dict(l=80, r=right_margin, t=top_margin, b=bottom_margin),
            showlegend=legend_count > 1,
        )

        # Legend positioning - always outside chart area
        if legend_count > 1:
            if legend_count <= 4:
                # Horizontal legend above chart (only for few items)
                layout["legend"] = dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="center",
                    x=0.5,
                    bgcolor="rgba(255,255,255,0.95)",
                    bordercolor=GRID_COLOR,
                    borderwidth=1,
                    font=dict(size=11, color=TEXT_COLOR),
                )
                layout["margin"]["t"] = 100
            else:
                # Vertical legend to the right (for many items)
                layout["legend"] = dict(
                    orientation="v",
                    yanchor="top",
                    y=1,
                    xanchor="left",
                    x=1.02,
                    bgcolor="rgba(255,255,255,0.95)",
                    bordercolor=GRID_COLOR,
                    borderwidth=1,
                    font=dict(size=10, color=TEXT_COLOR),
                    tracegroupgap=3,  # Reduce gap between items
                )

        # Axis styling
        if chart_type != "pie":
            layout["xaxis"] = dict(
                title=dict(text=x_label, font=dict(size=12, color=LABEL_COLOR)),
                tickfont=dict(size=10, color=LABEL_COLOR),
                gridcolor=GRID_COLOR,
                linecolor=GRID_COLOR,
                showgrid=False,
                zeroline=False,
                tickangle=-45 if len(x_values) > 8 else 0,
            )
            layout["yaxis"] = dict(
                title=dict(text=y_label, font=dict(size=12, color=LABEL_COLOR)),
                tickfont=dict(size=10, color=LABEL_COLOR),
                gridcolor=GRID_COLOR,
                linecolor=GRID_COLOR,
                showgrid=True,
                zeroline=False,
                tickformat=",",
            )

        # Horizontal bar adjustments
        if chart_type == "horizontal_bar":
            layout["xaxis"]["title"]["text"] = y_label
            layout["yaxis"]["title"]["text"] = x_label
            layout["yaxis"]["tickangle"] = 0
            layout["xaxis"]["tickangle"] = 0
            layout["margin"]["l"] = 150  # More space for y-axis labels

        fig.update_layout(**layout)

        # Ensure data labels don't get clipped
        if chart_type in ("bar", "line"):
            fig.update_yaxes(
                range=[0, max(y_values) * 1.15] if not isinstance(y_col, list) else None
            )

        # Save as PNG with dynamic size
        filename = f"{uuid.uuid4().hex}.png"
        filepath = CHARTS_DIR / filename

        try:
            fig.write_image(
                str(filepath),
                format="png",
                width=img_width,
                height=img_height,
                scale=2,
            )
            logger.info("chart_generated", chart_type=chart_type, data_points=len(data), file=filename)
            return filename
        except Exception as img_error:
            logger.warning("chart_png_failed", error=str(img_error))
            # Fallback to HTML
            html_filename = f"{uuid.uuid4().hex}.html"
            html_filepath = CHARTS_DIR / html_filename
            fig.write_html(str(html_filepath), include_plotlyjs="cdn", full_html=True)
            logger.info("chart_generated_html_fallback", file=html_filename)
            return html_filename

    except Exception as e:
        logger.error("chart_generation_failed", error=str(e))
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
- pie/donut: 항목이 10개 초과
- line: x축 고유값 36개 초과. 단, group_column이 있으면 x축 고유값 기준으로 판단

## 차트 타입 선택
- **line**: 월별/일별 추이, 시계열. 대륙별/국가별 그룹이 있으면 group_column 지정
- bar: 카테고리별 비교 (국가별, 플랫폼별 등)
- horizontal_bar: 항목이 많을 때 (7개 이상) 또는 긴 이름
- pie: 비율/구성 (전체 대비 비중)
- grouped_bar: 여러 지표를 카테고리별로 비교
- stacked_bar: 누적 비교

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
