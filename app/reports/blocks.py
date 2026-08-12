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
    chart_key: str = "value" # 막대가 무엇을 그리는가. 비율 절은 'ratio' 로 둔다
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


def _names(items, n=3, key="dim") -> str:
    """이름 나열은 짧게. 다섯 개만 이어붙여도 문장이 아니라 문단이 된다 (2026-08-12)."""
    head = ", ".join(str(r[key]) for r in items[:n])
    return head + (f" 외 {len(items) - n}개" if len(items) > n else "")


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
                "증가를 이끈 곳: "
                + ", ".join(f"{r['dim']} {_fmt(r['delta'], m.unit)}" for r in g[:3])
                + f" — 증가분의 {share}%")
        if loss:
            l = sorted(loss, key=lambda r: r["delta"])[:3]
            s.findings.append(
                "줄어든 곳: "
                + ", ".join(f"{r['dim']} {_fmt(r['delta'], m.unit)}" for r in l[:3]))
        newcomers = [r for r in rows if r["prev"] == 0 and r["value"] > 0]
        if newcomers:
            s.findings.append(
                f"직전 기간에 없던 곳 {len(newcomers)}개 — {_names(newcomers)}")
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


@block("contribution", "성장 기여도",
       "성장이 어디서 왔는지 분해 — 몇 개 항목이 증가분의 대부분을 만들었는가")
class Contribution:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        return Compare.queries(p, ctx)

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        d = DIMENSIONS[p["dim"]]
        cur = {r["dim"]: float(r["value"] or 0) for r in facts["cur"]}
        prv = {r["dim"]: float(r["value"] or 0) for r in facts["prv"]}
        net = sum(cur.values()) - sum(prv.values())

        items = [{"dim": k, "delta": round(cur.get(k, 0) - prv.get(k, 0), 1)}
                 for k in set(cur) | set(prv)]
        items.sort(key=lambda r: -r["delta"])
        # 순증감 대비 기여도. 순증감이 0 에 가까우면 비율이 폭발하므로 절대증감 기준으로 바꾼다
        gross = sum(abs(r["delta"]) for r in items) or 1.0
        base = net if abs(net) > gross * 0.1 else None
        up_sum = sum(r["delta"] for r in items if r["delta"] > 0)
        down_sum = sum(r["delta"] for r in items if r["delta"] < 0)
        # ⚠️ 줄어든 항목이 있으면 양수 기여도의 합이 순증감을 넘어 **누적이 100%를 넘는다**
        #    (실측 248%). 그 열은 읽는 사람을 혼란스럽게만 하므로 상쇄가 있을 땐 빼고,
        #    대신 증가분·감소분 총량을 문장으로 밝힌다 (2026-08-12 사용자 보고).
        has_offset = bool(down_sum)
        cum = 0.0
        rows = []
        for r in items:
            share = _pct(r["delta"], base) if base else _pct(abs(r["delta"]), gross)
            cum += share
            row = {**r, "share": round(share, 1)}
            if not has_offset:
                row["cum"] = round(cum, 1)
            rows.append(row)
        rows = relabel_rows(rows, p["dim"])

        cols = [{"key": "dim", "label": d.label},
                {"key": "delta", "label": "증감", "fmt": "metric"},
                {"key": "share", "label": "순증감 대비 기여도", "fmt": "pct"}]
        if not has_offset:
            cols.append({"key": "cum", "label": "누적", "fmt": "pct"})
        s = Section(block="contribution",
                    title=p.get("title") or f"{d.label}별 {m.label} 성장 기여도",
                    metric=p["metric"], dim=p["dim"], unit=m.unit, rows=rows,
                    chart="bar", chart_key="delta",   # 이 절의 수치는 value 가 아니라 delta 다
                    columns=cols)
        if has_offset:
            s.findings.append(
                f"늘어난 곳 합계 {_fmt(up_sum, m.unit)} · 줄어든 곳 합계 "
                f"{_fmt(down_sum, m.unit)} → 순증감 {_fmt(net, m.unit)}. "
                f"상쇄가 있어 개별 기여도의 합은 100%를 넘는다")
        else:
            s.findings.append(f"순증감 {_fmt(net, m.unit)}")
        if not base:
            s.findings.append("증감이 서로 상쇄돼 기여도는 절대 증감 기준으로 계산했다")
        pos = [r for r in rows if r["delta"] > 0]
        if pos:
            need = 0
            acc = 0.0
            tot_pos = sum(r["delta"] for r in pos)
            for r in pos:
                acc += r["delta"]
                need += 1
                if acc >= tot_pos * 0.8:
                    break
            s.findings.append(
                f"증가분의 80%를 {need}개 항목이 만들었다 — {_names(pos[:need])}")
        neg = [r for r in rows if r["delta"] < 0]
        if neg:
            worst = min(neg, key=lambda r: r["delta"])
            s.findings.append(
                f"가장 크게 줄어든 곳은 {worst['dim']} {_fmt(worst['delta'], m.unit)} "
                f"— 줄어든 곳이 {len(neg)}개")
        return s


@block("concentration", "집중도",
       "소수에 몰려 있는가 넓게 퍼져 있는가 — 파레토·상위 점유")
class Concentration:
    LIMIT = 500

    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        f = p.get("filters", {})
        return {
            "cur": Query(metric=p["metric"], dim=p["dim"], filters=f,
                         start=ctx["focus_start"], end=ctx["focus_end"],
                         limit=Concentration.LIMIT),
            # ⚠️ 잘린 목록의 합을 전체로 쓰면 비중이 부풀려지고 "N개가 80%" 도 틀린다.
            #    전체 합계는 축 없이 따로 구한다 (2026-08-12).
            "tot": Query(metric=p["metric"], filters=f,
                         start=ctx["focus_start"], end=ctx["focus_end"]),
        }

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        d = DIMENSIONS[p["dim"]]
        rows_all = sorted(facts["cur"], key=lambda r: -(float(r.get("value") or 0)))
        truncated = len(rows_all) >= Concentration.LIMIT
        tot_row = facts.get("tot") or []
        grand = float(tot_row[0]["value"]) if tot_row and tot_row[0].get("value") else 0.0
        listed = _total(rows_all)
        # 집중도는 **그 축에 귀속된 범위 안에서** 본다. 전체 합계를 분모로 쓰면
        # 축이 비어 있는 행(제품명 없는 매출 등)까지 섞여 집중도가 실제보다 낮게 보인다.
        # 잘렸을 때만 전체 합계를 분모로 쓰고 그 사실을 밝힌다.
        total = grand if (truncated and grand) else listed
        coverage = _pct(listed, grand) if grand else None
        cum = 0.0
        n50 = n80 = None
        for i, r in enumerate(rows_all, 1):
            cum += float(r["value"] or 0)
            if n50 is None and cum >= total * 0.5:
                n50 = i
            if n80 is None and cum >= total * 0.8:
                n80 = i
                break

        shown = rows_all[:15]
        c = 0.0
        out = []
        for i, r in enumerate(shown, 1):
            c += float(r["value"] or 0)
            out.append({"rank": i, "dim": r["dim"], "value": r["value"],
                        "share": _pct(r["value"], total), "cum": round(_pct(c, total), 1)})
        out = relabel_rows(out, p["dim"])

        s = Section(block="concentration",
                    title=p.get("title") or f"{d.label} 집중도",
                    metric=p["metric"], dim=p["dim"], unit=m.unit, rows=out, chart="bar",
                    columns=[{"key": "rank", "label": "순위"},
                             {"key": "dim", "label": d.label},
                             {"key": "value", "label": m.label, "fmt": "metric"},
                             {"key": "share", "label": "비중", "fmt": "pct"},
                             {"key": "cum", "label": "누적", "fmt": "pct"}])
        s.findings.append(
            f"{d.label} "
            + (f"{Concentration.LIMIT}개 이상 (상위 {Concentration.LIMIT}개만 조회)"
               if truncated else f"{len(rows_all)}개")
            + f" · 집계 대상 {_fmt(total, m.unit)}")
        if coverage is not None and coverage < 99.5:
            s.findings.append(
                f"{d.label} 값이 붙은 것은 전체 {_fmt(grand, m.unit)} 중 {coverage}%다 "
                f"— 비중은 이 범위 안에서 읽어야 한다")
        if n50:
            s.findings.append(
                f"절반을 {n50}개가 차지한다"
                + (f" · 80%를 {n80}개가 차지한다" if n80 else ""))
        elif truncated:
            s.findings.append(
                f"상위 {Concentration.LIMIT}개로도 절반에 못 미친다 — 매우 넓게 퍼져 있다")
        if n80 and len(rows_all) and not truncated:
            ratio = _pct(n80, len(rows_all))
            s.findings.append(
                f"상위 {ratio}%가 80%를 만든다 — "
                + ("소수 집중형이라 몇 건의 협상·정책으로 움직인다" if ratio <= 25
                   else "고르게 퍼져 있어 개별 대응보다 전반 정책이 맞다"))
        return s


@block("movers", "급변",
       "전년 대비 급증·급감한 항목 — 규모가 의미 있는 것만")
class Movers:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        return Compare.queries(p, ctx)

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        d = DIMENSIONS[p["dim"]]
        cur = {r["dim"]: float(r["value"] or 0) for r in facts["cur"]}
        prv = {r["dim"]: float(r["value"] or 0) for r in facts["prv"]}
        total = sum(cur.values()) or 1.0
        # ⚠️ 규모 하한이 없으면 "0.1억 → 0.4억, +300%" 같은 잡음이 1위로 올라온다
        floor = max(total * 0.005, 0.0)

        rows = []
        for k in set(cur) | set(prv):
            c, q = cur.get(k, 0.0), prv.get(k, 0.0)
            if max(c, q) < floor:
                continue
            rows.append({"dim": k, "prev": round(q, 1), "value": round(c, 1),
                         "delta": round(c - q, 1),
                         "growth": _pct(c - q, q) if q else None})
        # ⚠️ 라벨 정리를 **먼저** 한다. 나중에 하면 ups/downs 만 정리되고 news 는
        #    원문 세트명("A + B + C…")으로 문장에 실린다 (2026-08-12 사용자 보고).
        rows = relabel_rows(rows, p["dim"])
        ups = sorted([r for r in rows if r["growth"] is not None and r["growth"] > 0],
                     key=lambda r: -r["growth"])[:8]
        downs = sorted([r for r in rows if r["growth"] is not None and r["growth"] < 0],
                       key=lambda r: r["growth"])[:8]
        news = sorted([r for r in rows if r["growth"] is None and r["value"] > 0],
                      key=lambda r: -r["value"])[:5]
        out = ups + downs

        s = Section(block="movers", title=p.get("title") or f"{d.label} 급변 항목",
                    metric=p["metric"], dim=p["dim"], unit=m.unit, rows=out, chart="none",
                    columns=[{"key": "dim", "label": d.label},
                             {"key": "prev", "label": ctx["compare_label"], "fmt": "metric"},
                             {"key": "value", "label": ctx["focus_label"], "fmt": "metric"},
                             {"key": "delta", "label": "증감", "fmt": "metric"},
                             {"key": "growth", "label": "성장률", "fmt": "pct"}])
        s.findings.append(
            f"전체의 {_pct(floor, total):.1f}% 미만인 항목은 뺐다 — "
            f"작은 수의 배율 변동이 상위를 차지하는 것을 막기 위해서다")
        if ups:
            s.findings.append(
                "급증: " + ", ".join(f"{r['dim']} {r['growth']:+.0f}%" for r in ups[:3]))
        if downs:
            s.findings.append(
                "급감: " + ", ".join(f"{r['dim']} {r['growth']:+.0f}%" for r in downs[:3]))
        if news:
            s.findings.append(
                f"직전 기간에 없던 항목 {len(news)}개 — "
                + ", ".join(f"{r['dim']} {_fmt(r['value'], m.unit)}" for r in news[:3])
                + (f" 외 {len(news) - 3}개" if len(news) > 3 else ""))
        return s


@block("mixshift", "구성 변화",
       "비중이 몇 %p 움직였는가 — 성장과는 다른 이야기다")
class MixShift:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        return Compare.queries(p, ctx)

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        d = DIMENSIONS[p["dim"]]
        cur = {r["dim"]: float(r["value"] or 0) for r in facts["cur"]}
        prv = {r["dim"]: float(r["value"] or 0) for r in facts["prv"]}
        tc, tp = sum(cur.values()) or 1.0, sum(prv.values()) or 1.0
        rows = []
        for k in set(cur) | set(prv):
            sc, sp = _pct(cur.get(k, 0), tc), _pct(prv.get(k, 0), tp)
            rows.append({"dim": k, "prev_share": sp, "share": sc,
                         "shift": round(sc - sp, 1), "value": round(cur.get(k, 0), 1)})
        rows.sort(key=lambda r: -abs(r["shift"]))
        rows = relabel_rows(rows[:15], p["dim"])

        s = Section(block="mixshift", title=p.get("title") or f"{d.label} 구성 변화",
                    metric=p["metric"], dim=p["dim"], unit=m.unit, rows=rows, chart="none",
                    columns=[{"key": "dim", "label": d.label},
                             {"key": "prev_share", "label": f"{ctx['compare_label']} 비중",
                              "fmt": "pct"},
                             {"key": "share", "label": f"{ctx['focus_label']} 비중",
                              "fmt": "pct"},
                             {"key": "shift", "label": "변화(%p)", "fmt": "pct"}])
        up = [r for r in rows if r["shift"] > 0][:3]
        dn = [r for r in rows if r["shift"] < 0][:3]
        if up:
            s.findings.append(
                "비중이 커진 곳: " + ", ".join(f"{r['dim']} {r['shift']:+.1f}%p" for r in up))
        if dn:
            s.findings.append(
                "비중이 줄어든 곳: " + ", ".join(f"{r['dim']} {r['shift']:+.1f}%p" for r in dn))
        s.findings.append(
            "비중이 줄었다고 매출이 줄어든 것은 아니다 — 다른 곳이 더 빨리 컸을 수 있다")
        return s


@block("seasonality", "계절성",
       "전년 동월과 나란히 — 주기가 있는가, 이번 달이 원래 그런 달인가")
class Seasonality:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        return {"cur": Query(metric=p["metric"], dim="월", filters=p.get("filters", {}),
                             start=ctx["start"], end=ctx["end"], limit=60,
                             having_positive=False)}

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m = METRICS[p["metric"]]
        by_ym = {r["dim"]: float(r["value"] or 0) for r in facts["cur"]}
        rows = []
        for ym in sorted(by_ym):
            y, mm = ym.split("-")
            prev_ym = f"{int(y) - 1}-{mm}"
            q = by_ym.get(prev_ym)
            if q is None:
                continue
            c = by_ym[ym]
            rows.append({"dim": ym, "prev": round(q, 1), "value": round(c, 1),
                         "growth": _pct(c - q, q) if q else None})
        s = Section(block="seasonality", title=p.get("title") or f"{m.label} 전년 동월 대비",
                    metric=p["metric"], dim="월", unit=m.unit, rows=rows, chart="line",
                    columns=[{"key": "dim", "label": "월"},
                             {"key": "prev", "label": "전년 동월", "fmt": "metric"},
                             {"key": "value", "label": "당월", "fmt": "metric"},
                             {"key": "growth", "label": "전년 동월 대비", "fmt": "pct"}])
        if not rows:
            s.findings.append("전년 동월과 짝지을 데이터가 없어 계절성을 볼 수 없다")
            return s
        g = [r["growth"] for r in rows if r["growth"] is not None]
        if g:
            s.findings.append(
                f"전년 동월 대비 평균 {sum(g)/len(g):+.1f}% "
                f"(최고 {max(g):+.0f}% · 최저 {min(g):+.0f}%)")
        # 달마다 몰리는 패턴 — 월별 평균을 전체 평균과 비교
        per_month: Dict[str, List[float]] = {}
        for ym, v in by_ym.items():
            per_month.setdefault(ym.split("-")[1], []).append(v)
        avg_all = sum(by_ym.values()) / max(len(by_ym), 1)
        peaks = sorted(((sum(v) / len(v), mm) for mm, v in per_month.items()), reverse=True)
        if peaks and avg_all:
            hi = peaks[0]
            if hi[0] > avg_all * 1.3:
                s.findings.append(
                    f"{int(hi[1])}월이 평균보다 {_pct(hi[0] - avg_all, avg_all):.0f}% 높다 "
                    f"— 주기적 성수기로 보인다")
        return s


@block("ratio", "비율 지표",
       "두 지표의 비 — 원가율·할인율·객단가처럼 '얼마당 얼마'를 보는 절")
class Ratio:
    @staticmethod
    def queries(p, ctx) -> Dict[str, Query]:
        f = p.get("filters", {})
        return {
            "num": Query(metric=p["metric"], dim=p["dim"], filters=f,
                         start=ctx["focus_start"], end=ctx["focus_end"],
                         limit=p.get("limit", 25)),
            "den": Query(metric=p["metric2"], dim=p["dim"], filters=f,
                         start=ctx["focus_start"], end=ctx["focus_end"],
                         limit=p.get("limit", 25)),
        }

    @staticmethod
    def build(p, facts, ctx) -> Section:
        m, m2 = METRICS[p["metric"]], METRICS[p["metric2"]]
        d = DIMENSIONS[p["dim"]]
        num = {r["dim"]: float(r["value"] or 0) for r in facts["num"]}
        den = {r["dim"]: float(r["value"] or 0) for r in facts["den"]}

        # ⚠️ 단위가 같을 때만 백분율이다 (할인÷매출 = 할인율).
        #    단위가 다르면(매출÷주문수) 백분율은 무의미하다 — 실제로 "객단가 0.04%" 가
        #    나왔었다 (2026-08-12). 이럴 땐 실제 값끼리 나눠 '원/건' 으로 낸다.
        as_pct = m.base_unit == m2.base_unit
        rate_unit = "%" if as_pct else f"{m.base_unit}/{m2.base_unit}"

        def _rate(nv, dv):
            if not dv:
                return 0.0
            if as_pct:
                return round(nv / dv * 100, 2)
            return round((nv * m.scale) / (dv * m2.scale), 0)

        def _rate_text(v):
            return f"{v:,.2f}%" if as_pct else f"{v:,.0f}{rate_unit}"

        rows = []
        for k, dv in den.items():
            if dv <= 0:
                continue
            nv = num.get(k, 0.0)
            rows.append({"dim": k, "value": round(nv, 1), "base": round(dv, 1),
                         "ratio": _rate(nv, dv), "ratio_text": _rate_text(_rate(nv, dv))})
        rows.sort(key=lambda r: -r["ratio"])
        rows = relabel_rows(rows[:20], p["dim"])

        tot_n, tot_d = sum(num.values()), sum(den.values())
        overall = _rate(tot_n, tot_d)
        s = Section(block="ratio",
                    title=p.get("title") or f"{d.label}별 {m.label}÷{m2.label}",
                    metric=p["metric"], dim=p["dim"], unit=m.unit, rows=rows,
                    chart="bar", chart_key="ratio",   # 막대는 비율 기준 (분자로 그리면 표와 어긋난다)
                    columns=[{"key": "dim", "label": d.label},
                             {"key": "value", "label": m.label, "fmt": "metric"},
                             # ⚠️ 분모는 **자기 단위**로 찍는다. 분자 단위(억)를 쓰면
                             #    476,835건이 "423,030.0억" 으로 나온다 (2026-08-12 실측)
                             {"key": "base", "label": m2.label, "fmt": "metric2",
                              "unit": m2.unit},
                             {"key": "ratio_text",
                              "label": "비율" if as_pct else f"{m.label}/{m2.label}"}])
        if tot_d:
            s.findings.append(
                f"전체 {_rate_text(overall)} "
                f"({_fmt(tot_n, m.unit)} ÷ {_fmt(tot_d, m2.unit)})")
        if rows:
            s.findings.append(
                f"가장 높은 곳 {rows[0]['dim']} {rows[0]['ratio_text']}"
                + (f" · 가장 낮은 곳 {rows[-1]['dim']} {rows[-1]['ratio_text']}"
                   if len(rows) > 1 else ""))
            if tot_d and len(rows) > 2:
                over = [r for r in rows if r["ratio"] > overall]
                s.findings.append(f"전체 평균을 넘는 곳이 {len(over)}개")
        return s


def available() -> Dict[str, str]:
    return {k: v["desc"] for k, v in BLOCKS.items()}
