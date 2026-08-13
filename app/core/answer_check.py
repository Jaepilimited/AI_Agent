# -*- coding: utf-8 -*-
"""답변 속 수치가 조회 결과에서 나온 것인가 — 채팅 경로의 수치 검증.

보고서는 이미 이 방어선을 갖고 있다 (`app/reports/insight.py`: 문장 속 수치가 조회
결과에 없으면 그 문장을 버린다). **정작 사람들이 제일 많이 쓰는 채팅에는 없었다** —
가장 위험한 실패(그럴듯한데 틀린 숫자)가 가장 넓은 경로에서 무방비였다 (2026-08-13).

⛔ **채팅에서는 문장을 버리지 않는다.** 보고서와 달리 채팅 답변의 수치는 상당수가
   **파생값**이다 — 비중·증감률·합계는 행에 그대로 있지 않고 LLM 이 계산한다.
   그래서 여기서는 *검증 불가한 수치를 세어 남기는 것*까지만 한다. 먼저 실제 발생률을
   재고, 그 데이터를 보고 다음 단계(경고 표시·재생성)를 정한다.

허용하는 값:
    ① 조회 결과 행에 있는 값 (표기 차이 흡수: 1,234.5 == 1234.50)
    ② 질문에 적힌 값 (기간·개수 등)
    ③ 행 값들의 **합계** (열 단위)
    ④ 두 행 값의 **비율(%)** — 비중·구성비
    ⑤ 두 행 값의 **증감률(%)** — 전년 대비 등
    ⑥ 아주 작은 정수·연도 (순위 "상위 5개", 2026 같은 것)
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

import structlog

logger = structlog.get_logger(__name__)

# 1,234.5 / -12 / 35.4 형태. 백분율 기호·단위는 따로 붙는다
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# 표·코드·SQL 블록은 조회 결과 그대로라 검증 대상이 아니다
_SQL_BLOCK = re.compile(r"```.*?```|<details>.*?</details>", re.S)
# ⚠️ 날짜 조각(2026-06-30 의 06·30)이 미검증으로 잡혔다 — 숫자를 뽑기 전에 지운다
_DATE = re.compile(r"\d{4}\s*[-/.년]\s*\d{1,2}\s*[-/.월]?\s*\d{0,2}\s*일?"
                   r"|\d{1,2}\s*[월일]")
_TOL = 0.02          # 반올림 표기 차이 허용 (2%)
_SMALL_INT_MAX = 12  # "상위 5개", "3분기" 같은 작은 수는 검증하지 않는다


def _norm(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".")


def _to_float(tok: str):
    try:
        return float(tok.replace(",", ""))
    except ValueError:
        return None


def _numbers_in(text: str) -> List[float]:
    out = []
    for m in _NUM.findall(text or ""):
        v = _to_float(m)
        if v is not None:
            out.append(abs(v))
    return out


def _row_values(rows: Sequence[Dict[str, Any]]) -> Tuple[Set[float], Dict[str, List[float]]]:
    """행에 있는 숫자 값과 열별 숫자 목록."""
    vals: Set[float] = set()
    cols: Dict[str, List[float]] = {}
    for r in rows or []:
        for k, v in (r or {}).items():
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float)):
                f = abs(float(v))
            else:
                f = None
                s = str(v)
                if _NUM.fullmatch(s.strip()):
                    f = _to_float(s.strip())
                    f = abs(f) if f is not None else None
            if f is None:
                continue
            vals.add(f)
            cols.setdefault(str(k), []).append(f)
    return vals, cols


def _close(a: float, b: float) -> bool:
    if b == 0:
        return abs(a) < 1e-9
    return abs(a - b) / max(abs(b), 1e-9) <= _TOL


# ⚠️ 행은 원 단위인데 답변은 "1,153.6억" 처럼 **단위를 바꿔 쓴다.** 배율을 맞춰 보지
#    않으면 정상 답변의 수치가 전부 '미검증'으로 잡힌다 (첫 구현에서 실제로 그랬다).
_SCALES = (1.0, 1e3, 1e4, 1e6, 1e8, 1e12)   # 원 · 천 · 만 · 백만 · 억 · 조


def _close_any_scale(c: float, k: float) -> bool:
    return any(_close(c * s, k) for s in _SCALES)


def verify(answer: str, rows: Sequence[Dict[str, Any]], question: str = "",
           max_pairs: int = 400) -> Dict[str, Any]:
    """답변 속 수치 중 **조회 결과로 설명되지 않는 것**을 찾는다.

    돌려주는 값은 판정이 아니라 계측이다: `unverified` 가 비어 있지 않다고 해서
    답이 틀렸다는 뜻은 아니다 (LLM 이 여러 값을 조합했을 수 있다). 발생률을 보고
    다음 단계를 정하기 위한 자료다.
    """
    body = _DATE.sub(" ", _SQL_BLOCK.sub(" ", answer or ""))
    cands = _numbers_in(body)
    if not cands:
        return {"total": 0, "unverified": [], "rate": 0.0}

    vals, cols = _row_values(rows)
    q_vals = set(_numbers_in(question))

    # ③ 열 합계 + **부분합·잔여합**
    #    "상위 5개 합계"·"기타 27개 합계" 는 행에 없지만 조회 결과로 설명되는 값이다.
    #    이걸 빼먹으면 정상 답변이 계속 미검증으로 잡혀 경보가 소음이 된다 (실측 후 보강)
    sums: Set[float] = set()
    for v in cols.values():
        if not v:
            continue
        total = abs(sum(v))
        sums.add(total)
        desc = sorted(v, reverse=True)
        run = 0.0
        for x in desc[:20]:              # 상위 N 누적합과 그 잔여분
            run += x
            sums.add(abs(run))
            sums.add(abs(total - run))
        # 평균 — "월평균 208.3억"(=1,249.6÷6)이 미검증으로 잡혔다 (실측). 정상 파생값이다.
        # 행 수뿐 아니라 흔한 기간 분모(월·분기·반기)로도 나눠 본다
        for n in (len(v), 3, 4, 6, 12):
            if n:
                sums.add(abs(total / n))
    # ④⑤ 비율·증감률 — 행이 많으면 조합이 폭발하므로 상한을 둔다
    ratios: Set[float] = set()
    flat = sorted(vals)[:60]
    pairs = 0
    for i, a in enumerate(flat):
        for b in flat:
            if b == 0 or a == b:
                continue
            pairs += 1
            if pairs > max_pairs:
                break
            ratios.add(abs(a / b * 100.0))          # 비중
            ratios.add(abs((a - b) / b * 100.0))    # 증감률
            ratios.add(abs(a / b))                  # 배수 — "미국이 일본의 4.5배"
        if pairs > max_pairs:
            break
    # ⚠️ **합계끼리의 비율까지 허용하지 마라.** 파생값 공간이 1600개로 불어나
    #    "베트남 88.3억"(조회에 없는 행) 같은 값이 우연히 설명돼 버렸다 — 검출력이 죽는다.
    #    행 값 대비 합계(=비중)까지만 둔다.
    for sv in list(sums)[:40]:
        if not sv:
            continue
        for a in flat:
            ratios.add(abs(a / sv * 100.0))

    known = vals | sums | q_vals
    unverified = []
    for c in cands:
        if c <= _SMALL_INT_MAX and float(c).is_integer():
            continue                                  # ⑥ 작은 정수
        if 1900 <= c <= 2100 and float(c).is_integer():
            continue                                  # ⑥ 연도
        # 단위(억·만)를 바꿔 쓴 것도 같은 값으로 본다
        if any(_close_any_scale(c, k) for k in known):
            continue
        if any(_close(c, r) for r in ratios):
            continue
        # ⚠️ 아주 작은 비율은 **상대 오차로 보면 안 된다** — 0.1% 로 반올림된 값이
        #    실제 0.05% 면 상대오차 100% 라 늘 미검증으로 잡힌다 (실측). 절대 오차로 본다
        if c < 1.0 and any(abs(c - r) <= 0.05 for r in ratios):
            continue
        unverified.append(c)

    return {"total": len(cands), "unverified": [_norm(u) for u in unverified],
            "rate": round(len(unverified) / len(cands) * 100, 1)}


def log_verification(answer: str, rows: Sequence[Dict[str, Any]], question: str,
                     route: str = "bigquery") -> Dict[str, Any]:
    """검증하고 **WARNING 으로 남긴다** (프로덕션은 INFO 를 버린다).

    답변은 손대지 않는다 — 지금 단계는 계측이다.
    """
    try:
        res = verify(answer, rows, question)
    except Exception as e:                    # 검증 실패가 답변을 막으면 안 된다
        logger.warning("answer_check_failed", error=str(e)[:160])
        return {"total": 0, "unverified": [], "rate": 0.0}
    if res["unverified"]:
        logger.warning("answer_numbers_unverified", route=route,
                       question=(question or "")[:120], rows=len(rows or []),
                       total=res["total"], unverified=res["unverified"][:8],
                       rate=res["rate"])
    return res
