# -*- coding: utf-8 -*-
"""답변 속 수치가 조회 결과에서 나온 것인가 — 채팅 경로의 수치 검증.

보고서는 이미 이 방어선을 갖고 있다 (`app/reports/insight.py`: 문장 속 수치가 조회
결과에 없으면 그 문장을 버린다). **정작 사람들이 제일 많이 쓰는 채팅에는 없었다** —
가장 위험한 실패(그럴듯한데 틀린 숫자)가 가장 넓은 경로에서 무방비였다 (2026-08-13).

⛔ **채팅에서는 문장을 버리지 않는다.** 보고서와 달리 채팅 답변의 수치는 상당수가
   **파생값**이다 — 비중·증감률·차이는 행에 그대로 있지 않고 LLM 이 계산한다.
   대신 금액을 주장하는 미검증 수치(`significant`)가 있으면 **조회 원본 표를 답변에
   덧붙여** 사용자가 대조하게 한다 (`sql_agent._number_check_notice`).

⛔ **이 검증을 실사용 경로에 배선했는지 반드시 확인하라.** 2026-08-13 에 만들어
   놓고 호출부가 `format_answer`(비스트리밍) 하나뿐이어서, 채팅(스트리밍)에서는
   **계측조차 한 번도 돌지 않았다.** 그 사이 8,287만원이 "약 828.7억원"(1,000배)으로
   나갔고, 돌려 보면 미검증률 91.7% 로 걸리는 답변이었다 (2026-08-31 사용자 제보).

허용하는 값:
    ① 조회 결과 행에 있는 값 (표기 차이 흡수: 1,234.5 == 1234.50)
    ② 질문에 적힌 값 (기간·개수 등)
    ③ 행 값들의 **합계** (열 단위)
    ④ 두 행 값의 **비율(%)** — 비중·구성비
    ⑤ 두 행 값의 **증감률(%)** — 전년 대비 등
    ⑥ 두 행 값의 **차이** — "6월 대비 1.3억 증가"
    ⑦ 아주 작은 정수·연도 (순위 "상위 5개", 2026 같은 것)
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

# ── 어느 미검증 수치를 **사용자에게 보여줄 만큼** 심각하다고 볼 것인가 ──────────
# ⚠️ 전부 보여주면 소음이 되고, 소음이 된 경고는 곧 아무도 안 읽는다 (매일 같은
#    알림과 같은 이유). 금액을 주장하는 수치만 고른다 — 이 경로에서 실제로 사람을
#    속인 실패가 그것이다 (8,287만원을 828.7억원이라고 답했다, 2026-08-31).
_MONEY_TOKEN = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(?:억원|조원|만원|억|조|원)")
_SIGNIFICANT_MIN = 10_000    # 이보다 큰 값은 단위를 안 붙여도 금액·수량 주장이다


def _money_scaled_numbers(text: str) -> List[float]:
    """`828.7억원`처럼 **금액 단위를 달고 쓰인** 숫자만 뽑는다."""
    out = []
    for m in _MONEY_TOKEN.finditer(text or ""):
        v = _to_float(m.group(1))
        if v is not None:
            out.append(abs(v))
    return out


def _norm(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".")


def _to_float(tok: str):
    try:
        return float(tok.replace(",", ""))
    except ValueError:
        return None


def _numbers_in(text: str) -> List[float]:
    """⚠️ **낱말에 붙은 숫자는 값이 아니라 이름이다.** 그대로 세면 오탐이 된다 —
    실측에서 `SKIN1004` 의 **1004** 가 미검증 수치로 잡혔다 (2026-08-18 로그).
    같은 부류: `Q10`·`100ml`·`B2B`·`SPF50`. 앞뒤가 글자면 건너뛴다."""
    text = text or ""
    out = []
    for m in _NUM.finditer(text):
        before = text[m.start() - 1] if m.start() else ""
        after = text[m.end()] if m.end() < len(text) else ""
        # ⚠️ **ASCII 글자만 본다.** 파이썬에서 한글도 isalpha() 라 그냥 쓰면
        #    `87.2억`·`59건`의 단위에 걸려 **검출력이 통째로 죽는다** (실측에서 잡음).
        def _ascii_alpha(ch: str) -> bool:
            return bool(ch) and ch.isascii() and (ch.isalpha() or ch == "_")

        if _ascii_alpha(before) or _ascii_alpha(after):
            continue
        v = _to_float(m.group())
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


def _column_derived(cols: Dict[str, List[float]]) -> Set[float]:
    """열 합계·부분합·잔여합·평균 — 행에는 없지만 조회 결과가 설명하는 값.

    "상위 5개 합계"·"기타 27개 합계"·"월평균 208.3억" 이 여기서 나온다. 빼먹으면
    정상 답변이 계속 미검증으로 잡혀 경보가 소음이 된다 (실측 후 보강).
    ⚠️ `verify()` 와 금액 단위 교정이 **같은 함수**를 쓴다 — 두 번 구현하면
       한쪽만 고쳐진다.
    """
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
        # 행 수뿐 아니라 흔한 기간 분모(월·분기·반기)로도 나눠 본다
        for n in (len(v), 3, 4, 6, 12):
            if n:
                sums.add(abs(total / n))
    return sums


def grounded_amounts(rows: Sequence[Dict[str, Any]]) -> Set[float]:
    """조회 결과가 **실제로 설명하는 금액**의 집합 (행 값 + 열 파생값)."""
    vals, cols = _row_values(rows)
    return set(vals) | _column_derived(cols)


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
        return {"total": 0, "unverified": [], "significant": [], "rate": 0.0}

    vals, cols = _row_values(rows)
    q_vals = set(_numbers_in(question))
    # ⚠️ **행 수 자체가 답에 자주 쓰인다** — "총 59건의 프로모션". 조회 결과가 설명하는
    #    값인데 미검증으로 잡혀 발생률 1위였다 (2026-08-18 실측: 44.4%짜리 3건 전부 이것).
    vals = set(vals) | {float(len(rows or []))}

    # ③ 열 합계 + **부분합·잔여합**
    sums = _column_derived(cols)
    # ④⑤ 비율·증감률 — 행이 많으면 조합이 폭발하므로 상한을 둔다
    ratios: Set[float] = set()
    diffs: Set[float] = set()
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
            # 차이 — "6월 대비 7월 1.3억 증가". 비율·배수는 이미 허용하면서
            # 뺄셈만 빼 두면 정상 문장이 미검증으로 잡힌다. 차이는 원래 값보다
            # 항상 작아 파생값 공간이 크게 넓어지지 않는다 (배수와 달리).
            # ⚠️ `ratios` 가 아니라 여기 둔다 — 금액은 "1.3억"처럼 **단위를 바꿔
            #    쓰므로** 배율까지 맞춰 보는 쪽(`known`)에 있어야 한다.
            diffs.add(abs(a - b))
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

    known = vals | sums | diffs | q_vals
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

    # 금액을 주장하는 미검증 수치만 따로 센다 — 사용자에게 보여줄 것은 이것뿐이다.
    money = _money_scaled_numbers(body)
    significant = [u for u in unverified
                   if u >= _SIGNIFICANT_MIN or any(_close(u, m) for m in money)]

    return {"total": len(cands), "unverified": [_norm(u) for u in unverified],
            "significant": [_norm(u) for u in significant],
            "rate": round(len(unverified) / len(cands) * 100, 1)}


def log_verification(answer: str, rows: Sequence[Dict[str, Any]], question: str,
                     route: str = "bigquery") -> Dict[str, Any]:
    """검증하고 **WARNING 으로 남긴다** (프로덕션은 INFO 를 버린다).

    답변 문자열은 여기서 손대지 않는다. 사용자에게 보여줄지는 호출부가
    `significant` 를 보고 정한다 (`sql_agent._number_check_notice`).
    """
    try:
        res = verify(answer, rows, question)
    except Exception as e:                    # 검증 실패가 답변을 막으면 안 된다
        logger.warning("answer_check_failed", error=str(e)[:160])
        return {"total": 0, "unverified": [], "significant": [], "rate": 0.0}
    if res["unverified"]:
        logger.warning("answer_numbers_unverified", route=route,
                       question=(question or "")[:120], rows=len(rows or []),
                       total=res["total"], unverified=res["unverified"][:8],
                       significant=res["significant"][:8], rate=res["rate"])
    return res


# ── 금액 단위 환산은 **코드가 고친다** (2026-08-31, 같은 사고 2회차) ──────────
#
# 1회차: 표에 `33,399,393,579.80` (원값의 1,000배). → 프리뷰를 표시 정밀도로
#        미리 반올림해 미끼를 없앴다 (`sql_agent._display_round`).
# 2회차: 표는 맞는데 **요약 문장이** `82,871,719원`을 "약 828.7억원"이라고 썼다
#        (10만으로 나눴다). 1억 미만이라 애초에 '억'을 쓰면 안 되는 값이다.
#        사용자: *"표와 시각화는 값이 맞는데 ... 이부분이 안맞음"*
#
# ⛔ 프롬프트로 막을 수 없다 — 환산은 산술이고, LLM 의 산술은 확률이다.
#    자릿수를 옮기는 실수는 **그럴듯해 보여서** 사람이 못 잡는다.
# ⚠️ 그렇다고 아무 숫자나 고쳐 쓰면 더 나쁘다. 고치는 조건을 좁게 둔다:
#      ① 쓰인 그대로(숫자 × 단위)로는 조회 결과에 없고,
#      ② 같은 숫자를 **다른 배율**로 읽으면 조회 결과에 있고,
#      ③ 그렇게 읽히는 값이 **하나뿐**일 때.
#    셋 다 맞으면 "단위만 틀린 것"이 확정된다. 하나라도 아니면 손대지 않는다
#    (그때는 `significant` 공시가 받는다).
_MONEY_UNIT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|만)\s*(원)?")
_UNIT_VALUE = {"조": 1e12, "억": 1e8, "만": 1e4}
#: 자릿수를 잘못 옮기는 방향들. 실제 사고는 억(1e8)을 1e5 로 읽은 것이었다.
_ALT_SCALES = (1.0, 1e3, 1e4, 1e5, 1e6, 1e8, 1e12)
#: 교정은 검증보다 **엄격해야 한다** — 고쳐 쓰는 것은 되돌릴 수 없다.
#: 반올림 표기("828.7억")가 원값에 거의 정확히 얹힐 때만 손댄다.
_REPAIR_TOL = 0.005
#: 2등 후보가 이만큼 멀어야 "이 값이다"라고 확정한다.
_REPAIR_MARGIN = 3.0


def render_amount(value: float) -> str:
    """금액을 한국어 표기로 — 이 환산을 LLM 에게 시키지 않는 것이 요점이다."""
    v = abs(float(value))
    if v >= 1e8:
        return f"{v / 1e8:,.1f}억원"
    if v >= 1e4:
        return f"{round(v / 1e4):,}만원"
    return f"{round(v):,}원"


def repair_money_scale(text: str, known: Set[float]) -> Tuple[str, List[str]]:
    """`828.7억원` 처럼 **배율만 틀린 금액 표기**를 조회 결과 값으로 되돌린다."""
    if not text or not known:
        return text, []
    fixed: List[str] = []

    def _sub(m: "re.Match") -> str:
        number = _to_float(m.group(1))
        unit, won = m.group(2), m.group(3)
        if number is None:
            return m.group(0)
        # "3만 개" 처럼 '원' 없는 '만' 은 수량일 수 있다 — 건드리지 않는다
        if unit == "만" and not won:
            return m.group(0)
        written = number * _UNIT_VALUE[unit]
        if any(_close(written, k) for k in known):
            return m.group(0)                      # ① 그대로 맞다
        # ② 같은 숫자를 다른 배율로 읽으면 조회 결과의 어느 값이 되는가.
        #    ⚠️ 허용오차(2%) 안에 값이 둘 들어오는 일이 실제로 있다 —
        #       828.7 은 82,871,719(오차 0.002%)와 84,034,602(1.4%) 둘 다에 걸렸다.
        #       "하나만 걸릴 것"으로 두면 진짜 오답을 못 고친다. **가장 가까운 것**을
        #       고르되, 2등보다 확실히 가까울 때만 확정한다.
        scored = sorted(
            ((abs(number * s - k) / max(k, 1e-9), k)
             for s in _ALT_SCALES if s != _UNIT_VALUE[unit] for k in known),
            key=lambda x: x[0])
        if not scored or scored[0][0] > _REPAIR_TOL:
            return m.group(0)
        best = scored[0][1]
        runner = next((err for err, k in scored if abs(k - best) > max(best, 1) * _TOL),
                      None)
        if runner is not None and runner < scored[0][0] * _REPAIR_MARGIN:
            return m.group(0)                      # ③ 어느 값인지 확정할 수 없다
        correct = render_amount(best)
        fixed.append(f"{m.group(0).strip()} -> {correct}")
        return correct

    out = _MONEY_UNIT.sub(_sub, text)
    return out, fixed


def money_scale_repairer(rows: Sequence[Dict[str, Any]]):
    """행에서 기준값을 **한 번만** 뽑아 두는 줄 단위 교정기를 만든다.

    ⚠️ 줄마다 파생값을 다시 계산하면 스트리밍이 느려진다.
    """
    try:
        known = grounded_amounts(rows)
    except Exception as e:
        logger.warning("money_scale_known_failed", error=str(e)[:150])
        known = set()
    if not known:
        return None

    def _repair(chunk: str) -> str:
        try:
            out, fixed = repair_money_scale(chunk, known)
        except Exception as e:
            logger.warning("money_scale_repair_failed", error=str(e)[:150])
            return chunk
        if fixed:
            logger.warning("answer_money_scale_repaired", fixes=fixed[:5])
        return out

    return _repair
