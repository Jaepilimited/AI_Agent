# -*- coding: utf-8 -*-
"""판정 계층 — 표를 '읽어야 하는 것'에서 '판단이 끝난 것'으로 바꾼다.

데이터분석파트의 `Paid_Profitability_Report` 를 보고 들여온 구조다 (2026-08-13).
그 문서가 좋았던 이유는 차트가 예뻐서가 아니라 **각 장이 판정을 내려 놓기 때문**이다:

    KEY MESSAGE  — 이 장에서 무엇이 결론인가 (한 줄)
    손익 판정     — 행마다 흑자/적자 (읽는 사람이 계산하지 않는다)
    실행안 버킷   — 키울 곳 / 막을 곳 / 확인할 곳

세 가지 다 **숫자에서 기계적으로 나온다.** 그래서 그대로 가져왔다. 반대로 그 문서의
수수료·배송비·인건비 P&L 은 **가져오지 않았다** — 우리 데이터로는 만들 수 없고
(`Service_Fee` 가 음수로 깨져 있다), 그 문서도 수수료를 "보수적 임계치 30%" 라는
가정으로 채웠다. 가정으로 채운 손익은 통계처럼 생겨서 가장 잘 믿긴다.

⛔ 여기서도 LLM 은 한 글자도 쓰지 않는다. 판정은 임계값과 부등호로만 한다.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# ── 임계값 — 판정의 전부다. 바꾸려면 여기만 고친다 ────────────────────────────
SURGE, RISE, FLAT = 50.0, 5.0, 5.0        # 성장률 %: 급증 / 증가 / 보합 폭
SLUMP = -50.0
BAND = 20.0                               # 비율이 전체 평균에서 ±몇 % 벗어나면 높다/낮다
FOCUS_MIN_SHARE = 3.0                     # 실행안에 올릴 최소 비중 (%) — 잔챙이를 빼려고
FOCUS_N = 3                               # 버킷당 최대 항목 수

_HEADLINE_MAX = 120                       # 두 문장을 붙일지 결정하는 길이 상한
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# 분자가 분모의 **일부**인 비율 — 100% 를 넘을 수 없다. 넘으면 데이터 문제다.
# (참조 문서의 "필리핀 104.6% 는 플랫폼 중복 어트리뷰션" 과 같은 종류의 발견)
_COMPONENT_OF: Dict[str, set] = {
    "매출": {"할인", "유상원가", "FOC원가", "전환매출"},
}


def _nums(text: str) -> set:
    return {m.replace(",", "") for m in _NUM.findall(text or "")}


# ── 1) KEY MESSAGE — 절마다 결론 한 줄 ───────────────────────────────────────

# ⛔ **방법론 문장은 결론이 아니다.** "전체의 0.5% 미만인 항목은 뺐다" 가 결론 자리에
#    올라온 적이 있다 (2026-08-13 실측) — 무엇을 뺐는지는 근거지 결론이 아니다.
_METHOD = re.compile(r"뺐다|제외|빼고|위해서다|않도록|막기 위|기준은|집계되지|"
                     r"구분되지|넘긴다|만들지 않|인과가 아니")


def headline(section: Dict[str, Any]) -> None:
    """절의 발견 문장 중 **결론에 해당하는 것**을 따로 세운다.

    글머리표 다섯 줄 중 무엇이 요점인지는 쓴 사람만 안다 — 하나를 올려 두면 읽는
    사람이 고르지 않아도 된다. 다음 문장이 **새 숫자를 말하고** 짧으면 함께 붙인다
    (참조 문서의 KEY MESSAGE 가 대개 '무엇이 최고 · 무엇이 최저' 두 짝이다).

    ⚠️ 발견 목록에서 지우지는 않는다 — `insight` 가 사실 묶음으로 쓰기 때문이다.
       대신 `headline_skip` 으로 화면에서 겹쳐 찍지 않게 알린다.
    """
    finds = section.get("findings") or []
    cand = [i for i, f in enumerate(finds) if not _METHOD.search(f)]
    if not cand:
        return                      # 방법론뿐인 절은 결론을 세우지 않는다
    i = cand[0]
    head, used = finds[i], [i]
    for j in cand[1:]:
        if (_nums(finds[j]) - _nums(head)) and len(head) + len(finds[j]) <= _HEADLINE_MAX:
            head, used = f"{head} · {finds[j]}", used + [j]
        break                       # 두 문장까지만 — 세 문장이면 그건 요약이 아니라 목록이다
    section["headline"] = head
    section["headline_skip"] = used


# ── 2) 판정 열 — 행마다 결론 ─────────────────────────────────────────────────

def _growth_verdict(g: Optional[float]) -> str:
    if g is None:
        return ""
    if g >= SURGE:
        return "급증"
    if g >= RISE:
        return "증가"
    if g > -FLAT:
        return "보합"
    if g > SLUMP:
        return "감소"
    return "급감"


def verdicts(section: Dict[str, Any]) -> None:
    """행별 판정 열을 붙인다. 붙일 근거가 없으면 아무것도 하지 않는다.

    판정은 **행 하나만 보고** 내리지 않는다 — 성장은 부호와 폭으로, 비율은 전체
    평균과 견줘서 낸다. 기준이 없는 값에 등급을 매기면 그건 판정이 아니라 인상이다.
    """
    rows = section.get("rows") or []
    if not rows or any(c.get("key") == "verdict" for c in section.get("columns") or []):
        return

    kind = ""
    if any(r.get("growth") is not None for r in rows):
        for r in rows:
            g = r.get("growth")
            r["verdict"] = _growth_verdict(None if g is None else float(g))
        kind = "성장 판정"
    elif section.get("block") == "ratio" and any("ratio" in r for r in rows):
        # ⛔ 기준선에서 **이미 문제로 찍힌 행과 0 을 뺀다.** 있을 수 없는 값 하나가
        #    평균을 끌어올리면 멀쩡한 행들이 죄다 '낮음'이 된다 (실제로 120% 하나에
        #    평균이 46 으로 뛰어 10%·8% 가 나란히 '낮음'으로 찍혔다)
        live = [float(r["ratio"]) for r in rows
                if r.get("ratio") is not None and float(r["ratio"]) > 0
                and not r.get("verdict")]
        if not live:
            return
        avg = sum(live) / len(live)
        hi, lo = avg * (1 + BAND / 100), avg * (1 - BAND / 100)
        for r in rows:
            if r.get("verdict"):        # 확인 필요 — 등급을 매길 값이 아니다
                continue
            v = float(r.get("ratio") or 0)
            r["verdict"] = ("미집계" if v <= 0 else
                            "높음" if v > hi else "낮음" if v < lo else "평균")
        kind = f"정상 행 평균 {avg:,.1f} 대비 ±{BAND:.0f}%"
    else:
        return

    section.setdefault("columns", []).append({"key": "verdict", "label": "판정"})
    section["verdict_basis"] = kind


def impossible_ratio(section: Dict[str, Any]) -> None:
    """분자가 분모의 일부인데 **100% 를 넘는** 행을 데이터 문제로 짚는다.

    할인이 매출보다 클 수 없고 원가가 매출보다 클 수 없다. 넘었다면 계산이 아니라
    적재·귀속이 어긋난 것이다 — 그냥 두면 '할인율 1위' 로 표 맨 위에 앉는다.
    """
    if section.get("block") != "ratio":
        return
    num, den = section.get("metric"), section.get("metric2")
    if not den or num not in _COMPONENT_OF.get(den, set()):
        return
    bad = [r for r in (section.get("rows") or []) if float(r.get("ratio") or 0) > 100]
    if not bad:
        return
    names = ", ".join(str(r["dim"]) for r in bad[:3])
    section.setdefault("findings", []).append(
        f"{len(bad)}곳({names})은 비율이 100%를 넘는다 — {num}이 {den}의 일부이므로 "
        f"있을 수 없는 값이다. 계산이 아니라 적재·귀속이 어긋난 것으로 봐야 한다")
    for r in bad:
        r["verdict"] = "확인 필요"


# ── 3) 실행안 버킷 — 데이터가 가리키는 곳 ────────────────────────────────────

_BUCKETS = [
    ("키울 곳", "성장이 크고 규모도 있는 곳"),
    ("막을 곳", "빠지는 폭이 큰 곳"),
    ("확인할 곳", "숫자를 그대로 믿으면 안 되는 곳"),
]


def _sized(rows: List[Dict]) -> List[Dict]:
    """규모가 되는 행만 남긴다. 잔챙이의 +300% 를 실행안 맨 위에 올리지 않기 위해서다.

    ⛔ 처음엔 `share` 가 없으면 통과시켰는데, **성장 비교 절에는 `share` 가 없어
       전부 통과했다** — 실제로 러시아 +183.6%·불가리아 −100% 가 실행안 1·4위로
       올라왔다 (2026-08-13 실측). 없으면 이 절 안에서 직접 구한다.
    """
    if not rows:
        return []
    has_share = any(r.get("share") is not None for r in rows)
    if not has_share:
        base = sum(abs(float(r.get("value") or 0)) for r in rows)
        if not base:
            return []
        rows = [{**r, "share": abs(float(r.get("value") or 0)) / base * 100} for r in rows]
    return [r for r in rows
            if r.get("share") is not None and float(r["share"]) >= FOCUS_MIN_SHARE]


def focus(sections: List[Dict[str, Any]], ctx: Dict[str, Any],
          quality: List[Dict[str, str]], skipped: List[str]) -> Optional[Dict[str, Any]]:
    """"다음에 볼 곳" 절. 규칙이 데이터에서 직접 고른다.

    LLM 의 '다음 할 일' 과 역할이 다르다 — 여기는 **어디를** 볼지를 숫자로 지목하고,
    거기는 **무엇을 할지**를 문장으로 쓴다. 겹치면 `insight` 의 되풀이 방어가 걸러낸다.
    """
    up: List[Dict] = []
    down: List[Dict] = []
    check: List[Dict] = []

    for s in sections:
        rows = s.get("rows") or []
        label = s.get("dim") or ""
        for r in _sized(rows):
            g = r.get("growth")
            if g is None:
                continue
            g = float(g)
            if -FLAT < g < RISE:            # 보합은 볼 곳이 아니다
                continue
            item = {"name": str(r.get("dim")), "dim": label, "g": g,
                    "why": f"{g:+.1f}%" +
                           (f" · 비중 {float(r['share']):.1f}%" if r.get("share") else "")}
            (up if g >= RISE else down).append(item)
        # 비율 절에서 분자가 0인 행 = 실적이 0인지 미집계인지 모르는 곳
        if s.get("block") == "ratio":
            zeros = [str(r.get("dim")) for r in rows if float(r.get("ratio") or 0) <= 0]
            if zeros:
                check.append({"name": f"{s.get('title') or '비율 절'} 0 값 {len(zeros)}곳",
                              "dim": label,
                              "why": ", ".join(zeros[:4]) +
                                     " — 실제 0인지 미집계인지 구분되지 않는다"})

    # ⚠️ 데이터 구멍을 **앞에** 놓는다. 뒤에 두면 버킷 상한에 걸려 잘려 나가는데,
    #    "숫자를 믿어도 되는가"는 개별 행 하나보다 먼저 알아야 할 것이다
    gaps = [{"name": n["label"], "dim": "데이터", "why": n["text"][:90]} for n in quality]
    if skipped:
        gaps.append({"name": "조회 0건인 절", "dim": "데이터",
                     "why": ", ".join(skipped[:4]) + " — 결과가 없어 빼고 실었다"})
    check = gaps + check

    up.sort(key=lambda x: -x["g"])          # 많이 오른 순
    down.sort(key=lambda x: x["g"])         # 많이 빠진 순

    rows_out: List[Dict[str, str]] = []
    for (name, desc), items in zip(_BUCKETS, (up, down, check)):
        seen = set()
        for it in items:
            key = (it["name"], it["dim"])
            if key in seen:
                continue
            seen.add(key)
            rows_out.append({"bucket": name, "dim": f"{it['name']}",
                             "axis": it["dim"], "why": it["why"]})
            if sum(1 for r in rows_out if r["bucket"] == name) >= FOCUS_N:
                break

    if not rows_out:
        return None

    counts = {b: sum(1 for r in rows_out if r["bucket"] == b) for b, _ in _BUCKETS}
    finds = [f"{b} {counts[b]}곳" for b, _ in _BUCKETS if counts[b]]
    return {
        "block": "focus", "title": "다음에 볼 곳 — 데이터가 가리키는 곳",
        "metric": "", "dim": None, "unit": "", "rows": rows_out,
        "chart": "none", "chart_key": "value",
        "columns": [{"key": "bucket", "label": "구분"}, {"key": "dim", "label": "대상"},
                    {"key": "axis", "label": "축"}, {"key": "why", "label": "근거"}],
        "findings": [" · ".join(finds),
                     f"성장 {RISE:.0f}% 이상을 키울 곳, {FLAT:.0f}% 이상 감소를 막을 곳으로 "
                     f"나눴고 비중 {FOCUS_MIN_SHARE:.0f}% 미만은 뺐다 (잔챙이의 큰 증가율이 "
                     f"맨 위에 오지 않도록)"],
        "note": ("무엇을 할지가 아니라 어디를 볼지를 고른 목록이다. 판단은 아래 해석과 "
                 "현장 사정을 함께 놓고 하라."),
    }


def apply(sections: List[Dict[str, Any]]) -> None:
    """절마다 판정을 붙인다. 하나가 실패해도 나머지는 그대로 간다."""
    for s in sections:
        if s.get("block") in ("lead", "external", "focus"):
            continue
        try:
            impossible_ratio(s)
            verdicts(s)
            headline(s)
        except Exception as e:      # 판정이 못 붙는다고 보고서를 버리지 않는다
            logger.warning("judge_failed", block=s.get("block"), error=str(e)[:160])
