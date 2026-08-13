# -*- coding: utf-8 -*-
"""질문 → 보고서 스펙·파라미터.

**여기서 LLM 을 쓰지 않는다.** 보고서 종류는 유한하고, 기간 표현도 유한하다.
확률적 판정을 끼워 넣으면 "왜 이 기간이 나왔는지" 설명할 수 없는 보고서가 나온다 —
국가·팀 리터럴을 후처리로 교정하는 것과 같은 이유다.

스펙이 없는 주제는 **없다고 답한다.** 그럴듯한 문서를 만들어 주는 것보다 낫다.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Optional, Tuple

from app.reports.spec import ReportSpec

# 스펙 id → (모듈 경로, 사람이 읽을 이름, 이 스펙을 부르는 말)
SPECS: Dict[str, Dict[str, Any]] = {
    "cost_efficiency": {
        "module": "app.reports.specs.cost_efficiency",
        "label": "FOC·바우처 비용 효율화",
        "desc": "무상 출고(FOC)와 바우처·할인이 어디에 얼마나 쓰이는지, 상한을 걸면 얼마가 남는지",
        # 주제어 — 하나라도 있어야 이 스펙 후보가 된다
        "topics": ["foc", "무상", "무상출고", "무상 출고", "증정", "바우처", "voucher",
                   "할인", "쿠폰", "인센티브", "비용효율", "비용 효율", "원가율", "마진"],
    },
}

# 보고서를 원한다는 신호. 주제어만으로는 단순 조회 질문과 구분되지 않는다.
_REPORT_WORDS = [
    "보고서", "리포트", "report", "분석해줘", "분석 해줘", "진단", "종합",
    "정리해줘", "브리핑", "심층", "딥다이브", "deep dive",
]


def get_spec(spec_id: str, **overrides) -> ReportSpec:
    import importlib
    if spec_id not in SPECS:
        raise KeyError(f"등록되지 않은 보고서 스펙: {spec_id}")
    mod = importlib.import_module(SPECS[spec_id]["module"])
    return mod.build_spec(**overrides)


def available() -> list[Dict[str, str]]:
    return [{"id": k, "label": v["label"], "desc": v["desc"]} for k, v in SPECS.items()]


# ── 기간 파싱 ─────────────────────────────────────────────────────────────────

def _half(year: int, first: bool) -> Tuple[str, str]:
    return (f"{year}-01-01", f"{year}-06-30") if first else (f"{year}-07-01", f"{year}-12-31")


def parse_period(q: str, today: Optional[date] = None) -> Dict[str, Any]:
    """질문에서 중점 기간과 비교 기간을 뽑는다.

    기간을 명시하지 않으면 **직전에 끝난 반기**를 쓴다. 진행 중인 반기를 기본으로 잡으면
    두 달치로 반기 비율을 내고 그걸 반기 실적처럼 읽게 된다 (원본 파이프라인도
    "마감 완료 월만 사용한다"를 규칙으로 뒀다).

    비교 기간은 항상 **전년 동기**다. 직전 반기와 비교하면 계절성이 섞인다.
    """
    today = today or date.today()
    ql = q.lower()

    m = re.search(r"(20\d{2})\s*년?", q)
    y = int(m.group(1)) if m else today.year

    if re.search(r"하반기|2h|h2", ql):
        first = False
    elif re.search(r"상반기|1h|h1", ql):
        first = True
    elif re.search(r"작년|전년|지난해", ql) and not m:
        y, first = today.year - 1, False
    else:
        # 직전 완료 반기 — 7월 이후면 올해 상반기, 6월 이전이면 작년 하반기
        if today.month > 6:
            first = True
        else:
            y, first = y - 1, False

    fs, fe = _half(y, first)
    cs, ce = _half(y - 1, first)
    label = f"{y} {'상반기' if first else '하반기'}"
    cmp_label = f"{y - 1} {'상반기' if first else '하반기'}"

    # 집계 구간은 비교 기간 시작부터 중점 기간 끝까지 — 월별 추이를 보려면 필요하다
    return {
        "start": cs,
        "end": fe,
        "focus_start": fs, "focus_end": fe,
        "compare_start": cs, "compare_end": ce,
        "focus_label": label, "compare_label": cmp_label,
        "window_label": f"{y - 1}년 {'1월' if first else '7월'} ~ {label}",
    }


_BRANDS = {"스킨천사": "SK", "skin1004": "SK", "sk": "SK",
           "우마": "UM", "umma": "UM", "um": "UM",
           "커먼랩스": "CL", "좀비뷰티": "SK"}


def extract_filters(q: str) -> Dict[str, Any]:
    """질문에 등장한 국가·대륙·팀·영업유형을 필터로 뽑는다.

    **LLM 에 맡기지 않는다.** "일본 매출 보고서"의 '일본'을 놓치면 전사 보고서가 나와서
    질문에 답하지 않은 문서가 된다. 이건 확률에 걸 문제가 아니다.
    값은 실제 데이터에 있는 것만 쓴다 (CLAUDE.md 의 국가명은 한국어).
    """
    out: Dict[str, Any] = {}

    hits = [c for c in _COUNTRIES if c in q]
    # '한국'이 '한국사업팀' 의 일부로 잡히는 것 같은 부분 일치를 막는다
    hits = [c for c in hits if not any(c != o and c in o for o in hits)]
    if hits:
        out["국가"] = hits[:5]

    conts = [c for c in ["유럽", "아시아", "북미", "남미", "중미", "중동", "아프리카",
                         "오세아니아", "CIS"] if c in q]
    if conts and "국가" not in out:
        out["대륙"] = conts[:3]

    from app.agents.sql_agent import TEAM_CODE2KR
    teams = [code for code, kr in TEAM_CODE2KR.items() if kr in q]
    if teams:
        out["팀"] = teams

    if "b2b" in q.lower():
        out["영업유형"] = ["B2B"]
    elif "b2c" in q.lower():
        out["영업유형"] = ["B2C"]
    return out


# 실제 데이터에 있는 주요 국가 (한국어). 전체 191개를 다 볼 필요는 없다 —
# 보고서로 물어보는 국가는 사실상 여기 안에 있고, 없으면 전사 보고서로 나간다.
_COUNTRIES = [
    "한국", "일본", "미국", "중국", "인도네시아", "베트남", "태국", "필리핀", "말레이시아",
    "싱가포르", "대만", "홍콩", "호주", "캐나다", "영국", "프랑스", "독일", "스페인",
    "이탈리아", "네덜란드", "폴란드", "러시아", "인도", "브라질", "멕시코", "터키",
    "사우디아라비아", "아랍에미리트", "카자흐스탄", "우크라이나",
]


def parse_params(q: str, spec_id: str) -> Dict[str, Any]:
    params: Dict[str, Any] = dict(parse_period(q))
    ql = q.lower()
    for word, code in _BRANDS.items():
        if word in ql:
            # ⚠️ UM·CBT 는 제품원가가 사실상 미적재다 (99% 가 0원) — 원가 보고서는 SK 만 유효.
            #    다른 브랜드를 지정하면 무시하고 SK 로 간다. 그 사실은 응답에서 알린다.
            if spec_id == "cost_efficiency" and code != "SK":
                params["_brand_downgraded"] = code
                break
            params["brand"] = code
            break
    return params


def match(question: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """고정 스펙에 해당하면 (스펙 id, 파라미터). 아니면 None."""
    ql = question.lower()
    if not any(w in ql for w in _REPORT_WORDS):
        return None
    for spec_id, meta in SPECS.items():
        if any(t in ql for t in meta["topics"]):
            return spec_id, parse_params(question, spec_id)
    return None


# 보고서 **기능에 대해 묻는** 질문. 보고서를 달라는 게 아니다.
# ⛔ "보고서 기능은 어떤 때 쓰면 좋아?" 가 매번 전사 매출 보고서를 만들고 있었다
#    (2026-08-13 실측, 10건 생성). 질문에 답하지도 못하면서 플래너 LLM 1회 +
#    BigQuery 8~12회를 태우고 9~18초를 쓴다. 신호어만 보면 요청과 질문을 못 가른다.
_REPORT_META = re.compile(
    r"(보고서|리포트)\s*(기능|메뉴|탭)"                      # 보고서 기능/메뉴/탭
    r"|(보고서|리포트)\s*(이란|란\b|이 뭐|가 뭐|는 뭐)"        # 보고서란 / 보고서가 뭐야
    r"|(보고서|리포트)[^?.!]{0,10}(사용법|쓰는\s*법|만드는\s*법)"
    r"|어떻게\s*(보고서|리포트)"                             # 어떻게 보고서를 만들어?
    r"|(보고서|리포트)[^?.!]{0,10}어떻게\s*(만들|써|사용|쓰|봐|보나)"
)

# ⛔ 위 정규식만으로는 부족했다. **"이 시스템으로 뭘 할 수 있는지 짧게 정리해줘" 도
#    보고서를 만들었다** — '보고서'라는 말이 없어도 `정리해줘`가 신호어이기 때문이다
#    (2026-08-13, 2건 생성). 신호어는 여러 개고 대상어도 여러 개라 문구를 하나씩
#    막는 방식으로는 끝이 없다. **대상이 시스템 자신이고 데이터 명사가 하나도 없으면
#    분석할 대상이 없는 질문**이라는 구조로 판정한다.
_SELF_SUBJECT = re.compile(r"시스템|서비스|어시스턴트|에이전트|챗봇|프로그램|기능|너는|네가|당신")
_DATA_NOUN = re.compile(
    r"매출|판매|수량|물량|원가|비용|할인|바우처|실적|이익|마진|객단가|성장|점유|"
    r"거래처|고객|제품|상품|채널|국가|지역|권역|대륙|팀|브랜드|라인|카테고리|"
    r"광고|마케팅|재고|리뷰|프로모션|foc|sku|b2b|b2c")


def _is_meta_question(ql: str) -> bool:
    """보고서를 **달라는** 게 아니라 기능·시스템을 **설명해 달라는** 질문인가."""
    if _REPORT_META.search(ql):
        return True
    return bool(_SELF_SUBJECT.search(ql)) and not _DATA_NOUN.search(ql)


def wants_report(question: str) -> bool:
    """보고서를 **달라는** 질문인가.

    신호어("보고서·리포트·분석해줘·진단…")가 있어야 한다. "일본 매출 얼마야?" 같은
    단순 조회를 보고서로 만들어 스무 초 기다리게 하지 않는다.

    ⛔ 신호어가 있어도 **기능을 묻는 질문이면 만들지 않는다.** 대응은 신호어를
       빼는 게 아니다 — "매출 보고서 만들어줘"는 계속 만들어야 한다. 요청형과
       설명 요구를 결정적으로 가른다.
    """
    ql = question.lower()
    if not any(w in ql for w in _REPORT_WORDS):
        return False
    return not _is_meta_question(ql)


def route(question: str) -> Optional[Dict[str, Any]]:
    """질문 → 어떤 경로로 보고서를 만들 것인가.

    - `spec`    : 손으로 검증한 고정 스펙 (집계 계약·시뮬레이션이 있는 깊은 분석)
    - `dynamic` : 블록을 조합하는 넓은 분석. 스펙이 없는 주제는 전부 이쪽

    고정 스펙이 있으면 **그쪽이 이긴다.** 같은 주제를 동적으로 다시 만들면
    검증된 분석을 얕은 것으로 덮어쓰게 된다.
    """
    if not wants_report(question):
        return None
    hit = match(question)
    if hit:
        return {"kind": "spec", "spec_id": hit[0], "params": hit[1]}
    params = parse_params(question, "")
    params["_filters"] = extract_filters(question)
    return {"kind": "dynamic", "spec_id": None, "params": params}
