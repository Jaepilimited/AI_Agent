# -*- coding: utf-8 -*-
"""분석 블록 — 보고서를 이루는 최소 단위.

블록 하나 = 조회 + 표/차트 + **규칙으로 뽑은 발견(finding) 문장**.

핵심은 여기다: **finding 을 LLM 이 쓰지 않는다.** 규칙이 뽑는다.
"1위가 전체의 X%", "전년 대비 Y% 늘었다", "상위 3개가 증가분의 Z%를 만들었다" —
이런 문장은 숫자에서 기계적으로 나온다. LLM 에 맡기면 그럴듯한데 틀린 문장이 섞이고,
그게 보고서에서 가장 위험한 실패다.

블록을 조합해 보고서를 만들기 때문에 **질문이 달라지면 조합이 달라진다.**
고정 스펙과 다른 점이 이것이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.reports.semantic import METRICS, DIMENSIONS, Query, build_sql, relabel_rows


@dataclass
class Section:
    """렌더된 보고서의 한 절."""
    block: str
    title: str
    metric: str
    dim: Optional[str]
    unit: str
    rows: List[Dict[str, Any]] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    chart: str = ""          # 'line' | 'bar' | 'none'
    columns: List[Dict[str, str]] = field(default_factory=list)
    note: str = ""


def _fmt(v: Any, unit: str, nd: int = 1) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if unit == "억":
        return f"{f:,.1f}억"
    return f"{f:,.0f}{unit}"


def _pct(a, b, nd=1) -> float:
    b = float(b or 0)
    return round(float(a or 0) / b * 100, nd) if b else 0.0


def _total(rows) -> float:
    return sum(float(r.get("value") or 0) for r in rows)


# ── 블록 정의 ─────────────────────────────────────────────────────────────────
# 각 블록은 queries(plan) 로 조회를 선언하고, build(plan, facts) 로 Section 을 만든다.


BLOCKS: Dict[str, Dict[str, Any]] = {}


def block(key: str, label: str, desc: str, needs_dim: bool = True):
    def deco(cls):
        BLOCKS[key] = {"label": label, "desc": desc, "cls": cls, "needs_dim": needs_dim}
        return cls
    return deco


@block("trend", "추세", "지표가 기간에 따라 어떻게 움직였는가 (월·분기)")
class Trend:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        dim = p.get("dim") or "월"
        return {"cur": Query(metric=p["metric"], dim=dim, filters=p.get("filters", {}),
                             start=ctx["start"], end=ctx["end"], limit=60,
                             having_positive=False)}

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        dim = p.get("dim") or "월"
        rows = facts["cur"]
        s = Section(block="trend", title=p.get("title") or f"{m.label} 추세",
                    metric=p["metric"], dim=dim, unit=m.unit, rows=rows, chart="line",
                    columns=[{"key": "dim", "label": DIMENSIONS[dim].label},
                             {"key": "value", "label": m.label, "fmt": "metric"}])
        if len(rows) >= 2:
            first, last = rows[0], rows[-1]
            hi = max(rows, key=lambda r: float(r.get("value") or 0))
            lo = min(rows, key=lambda r: float(r.get("value") or 0))
            chg = _pct(float(last["value"]) - float(first["value"]), first["value"])
            s.findings.append(
                f"{first['dim']} {_fmt(first['value'], m.unit)} → {last['dim']} "
                f"{_fmt(last['value'], m.unit)} ({chg:+.1f}%)")
            s.findings.append(
                f"최고 {hi['dim']} {_fmt(hi['value'], m.unit)} · "
                f"최저 {lo['dim']} {_fmt(lo['value'], m.unit)}")
            # 가장 큰 월간 변동 — 눈으로 훑어 찾던 것을 규칙이 짚는다
            jumps = [(float(rows[i]["value"] or 0) - float(rows[i - 1]["value"] or 0), i)
                     for i in range(1, len(rows))]
            if jumps:
                up = max(jumps)
                dn = min(jumps)
                if up[0] > 0:
                    s.findings.append(
                        f"가장 큰 증가는 {rows[up[1]]['dim']} "
                        f"(직전 대비 {_fmt(up[0], m.unit)})")
                if dn[0] < 0:
                    s.findings.append(
                        f"가장 큰 감소는 {rows[dn[1]]['dim']} "
                        f"(직전 대비 {_fmt(dn[0], m.unit)})")
        return s


@block("breakdown", "구성", "축별로 얼마씩 차지하는가 + 집중도")
class Breakdown:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        return {"cur": Query(metric=p["metric"], dim=p["dim"], filters=p.get("filters", {}),
                             start=ctx["focus_start"], end=ctx["focus_end"],
                             limit=p.get("limit", 20))}

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        d = DIMENSIONS[p["dim"]]
        rows = relabel_rows(facts["cur"], p["dim"])
        total = _total(rows)
        for r in rows:
            r["share"] = _pct(r["value"], total)
        s = Section(block="breakdown", title=p.get("title") or f"{d.label}별 {m.label}",
                    metric=p["metric"], dim=p["dim"], unit=m.unit, rows=rows, chart="bar",
                    columns=[{"key": "dim", "label": d.label},
                             {"key": "value", "label": m.label, "fmt": "metric"},
                             {"key": "share", "label": "비중", "fmt": "pct"}])
        if rows:
            top = rows[0]
            s.findings.append(
                f"1위 {top['dim']} {_fmt(top['value'], m.unit)} — 전체의 {top['share']}%")
            if len(rows) >= 3:
                top3 = _pct(sum(float(r["value"] or 0) for r in rows[:3]), total)
                s.findings.append(f"상위 3개가 {top3}% — {'소수에 몰려 있다' if top3 >= 60 else '비교적 고르게 퍼져 있다'}")
            if len(rows) >= 5:
                tail = [r for r in rows if r["share"] < 1.0]
                if tail:
                    s.findings.append(f"비중 1% 미만이 {len(tail)}개")
        return s


@block("compare", "전년 대비", "같은 축을 직전 기간과 비교 — 무엇이 성장을 만들었는가")
class Compare:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        f = p.get("filters", {})
        return {
            "cur": Query(metric=p["metric"], dim=p["dim"], filters=f,
                         start=ctx["focus_start"], end=ctx["focus_end"],
                         limit=p.get("limit", 25)),
            "prv": Query(metric=p["metric"], dim=p["dim"], filters=f,
                         start=ctx["compare_start"], end=ctx["compare_end"],
                         limit=p.get("limit", 25)),
        }

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        d = DIMENSIONS[p["dim"]]
        cur = {r["dim"]: float(r["value"] or 0) for r in facts["cur"]}
        prv = {r["dim"]: float(r["value"] or 0) for r in facts["prv"]}
        rows = []
        for k in sorted(set(cur) | set(prv), key=lambda x: -cur.get(x, 0)):
            c, q = cur.get(k, 0.0), prv.get(k, 0.0)
            rows.append({"dim": k, "value": round(c, 1), "prev": round(q, 1),
                         "delta": round(c - q, 1), "growth": _pct(c - q, q) if q else None})
        rows = relabel_rows(rows, p["dim"])
        s = Section(block="compare", title=p.get("title") or f"{d.label}별 {m.label} 전년 대비",
                    metric=p["metric"], dim=p["dim"], unit=m.unit, rows=rows, chart="bar",
                    columns=[{"key": "dim", "label": d.label},
                             {"key": "prev", "label": ctx["compare_label"], "fmt": "metric"},
                             {"key": "value", "label": ctx["focus_label"], "fmt": "metric"},
                             {"key": "delta", "label": "증감", "fmt": "metric"},
                             {"key": "growth", "label": "성장률", "fmt": "pct"}])
        tc, tp = sum(r["value"] for r in rows), sum(r["prev"] for r in rows)
        if tp:
            s.findings.append(
                f"전체 {_fmt(tp, m.unit)} → {_fmt(tc, m.unit)} ({_pct(tc - tp, tp):+.1f}%)")
        gain = [r for r in rows if r["delta"] > 0]
        loss = [r for r in rows if r["delta"] < 0]
        if gain:
            g = sorted(gain, key=lambda r: -r["delta"])[:3]
            share = _pct(sum(r["delta"] for r in g), sum(r["delta"] for r in gain))
            s.findings.append(
                "증가를 이끈 곳: " + ", ".join(f"{r['dim']} {_fmt(r['delta'], m.unit)}" for r in g)
                + f" — 증가분의 {share}%")
        if loss:
            l = sorted(loss, key=lambda r: r["delta"])[:3]
            s.findings.append(
                "줄어든 곳: " + ", ".join(f"{r['dim']} {_fmt(r['delta'], m.unit)}" for r in l))
        newcomers = [r for r in rows if r["prev"] == 0 and r["value"] > 0]
        if newcomers:
            s.findings.append(
                f"직전 기간에 없던 곳 {len(newcomers)}개 — "
                + ", ".join(r["dim"] for r in newcomers[:5]))
        return s


@block("ranking", "순위", "상위 N 나열 — 제품·거래처처럼 항목이 많은 축")
class Ranking:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        return {"cur": Query(metric=p["metric"], dim=p["dim"], filters=p.get("filters", {}),
                             start=ctx["focus_start"], end=ctx["focus_end"],
                             limit=p.get("limit", 15))}

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        d = DIMENSIONS[p["dim"]]
        rows = relabel_rows(facts["cur"], p["dim"])
        total = _total(rows)
        for i, r in enumerate(rows, 1):
            r["rank"] = i
            r["share"] = _pct(r["value"], total)
        s = Section(block="ranking", title=p.get("title") or f"{d.label} 상위 {len(rows)}",
                    metric=p["metric"], dim=p["dim"], unit=m.unit, rows=rows, chart="bar",
                    columns=[{"key": "rank", "label": "순위"},
                             {"key": "dim", "label": d.label},
                             {"key": "value", "label": m.label, "fmt": "metric"},
                             {"key": "share", "label": "표시분 내 비중", "fmt": "pct"}])
        if rows:
            s.findings.append(
                f"1위 {rows[0]['dim']} {_fmt(rows[0]['value'], m.unit)}"
                + (f", 2위와 {_pct(rows[0]['value'] - rows[1]['value'], rows[1]['value']):.0f}% 차이"
                   if len(rows) > 1 and rows[1]["value"] else ""))
        return s


@block("cross", "교차", "두 축을 겹쳐 본다 (국가 × 채널 등)")
class Cross:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        return {"cur": Query(metric=p["metric"], dim=p["dim"], dim2=p["dim2"],
                             filters=p.get("filters", {}),
                             start=ctx["focus_start"], end=ctx["focus_end"],
                             limit=p.get("limit", 40))}

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        d, d2 = DIMENSIONS[p["dim"]], DIMENSIONS[p["dim2"]]
        rows = relabel_rows(facts["cur"], p["dim"])
        total = _total(rows)
        for r in rows:
            r["share"] = _pct(r["value"], total)
        s = Section(block="cross", title=p.get("title") or f"{d.label} × {d2.label} {m.label}",
                    metric=p["metric"], dim=p["dim"], unit=m.unit, rows=rows, chart="none",
                    columns=[{"key": "dim", "label": d.label},
                             {"key": "dim2", "label": d2.label},
                             {"key": "value", "label": m.label, "fmt": "metric"},
                             {"key": "share", "label": "비중", "fmt": "pct"}])
        if rows:
            s.findings.append(
                f"가장 큰 조합은 {rows[0]['dim']} × {rows[0]['dim2']} "
                f"{_fmt(rows[0]['value'], m.unit)} (전체의 {rows[0]['share']}%)")
        return s


@block("total", "총량", "기간 전체 합계 하나 — 표지 지표", needs_dim=False)
class Total:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        f = p.get("filters", {})
        return {
            "cur": Query(metric=p["metric"], filters=f,
                         start=ctx["focus_start"], end=ctx["focus_end"]),
            "prv": Query(metric=p["metric"], filters=f,
                         start=ctx["compare_start"], end=ctx["compare_end"]),
        }

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        c = float((facts["cur"][0] if facts["cur"] else {}).get("value") or 0)
        q = float((facts["prv"][0] if facts["prv"] else {}).get("value") or 0)
        s = Section(block="total", title=p.get("title") or f"{m.label} 총량",
                    metric=p["metric"], dim=None, unit=m.unit,
                    rows=[{"dim": ctx["focus_label"], "value": round(c, 1)},
                          {"dim": ctx["compare_label"], "value": round(q, 1)}],
                    chart="none",
                    columns=[{"key": "dim", "label": "기간"},
                             {"key": "value", "label": m.label, "fmt": "metric"}])
        s.findings.append(
            f"{ctx['focus_label']} {_fmt(c, m.unit)}"
            + (f" — {ctx['compare_label']} 대비 {_pct(c - q, q):+.1f}%" if q else ""))
        return s


def available() -> Dict[str, str]:
    return {k: v["desc"] for k, v in BLOCKS.items()}
