# -*- coding: utf-8 -*-
"""질문 → 검색 키워드. **한 곳에서만 뽑는다.**

⛔ 예전엔 검색하는 곳마다 각자 불용어 목록을 갖고 있었고, **어느 것도 조사를 떼지
   않았다.** 한국어는 교착어라 그러면 조용히 빗나간다:

    드라이브 : "구글드라이브에서 내가 작성한 신규 입사자 교안 자료 찾아줘"
               → 문장 전체가 검색어가 돼 **항상 0건** (2026-08-14 사용자 제보,
                 제미나이는 찾는 파일을 우리만 못 찾았다)
    위키     : "일본에서 매출이 왜 늘었는지" → `매출이`·`일본에서` 로 LIKE 매칭 →
               "매출" 이 든 문서를 못 찾는다

두 곳 다 **에러가 나지 않는다.** "검색 결과가 없습니다" 는 정말 없을 때와 검색어가
망가졌을 때가 똑같이 생겼다 — 그래서 오래 안 잡혔다.

사용하는 곳: `gws_agent`(드라이브) · `wiki_search`. 새 검색 경로도 여기를 쓴다.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Set

import structlog

from app.core.textmatch import strip_particle

logger = structlog.get_logger(__name__)

# 한글·영문·숫자로 시작하고 하이픈/슬래시/점으로 이어지는 덩어리 (B2B, 2026-08, A/B 보존)
_TOKEN = re.compile(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9\-_/.]*")

# 어느 검색에서나 뜻을 좁히지 못하는 말 — 명령·의문·지시어와 너무 일반적인 명사
BASE_STOP: Set[str] = {
    # 명령·요청
    "찾아줘", "찾아", "찾기", "검색해줘", "검색", "보여줘", "보여", "알려줘", "알려",
    "해줘", "주세요", "부탁", "부탁해", "정리해줘", "설명해줘", "말해줘",
    # 의문·지시
    "뭐", "뭐야", "무엇", "무슨", "어디", "어느", "언제", "누가", "왜", "어떻게",
    "어떤", "이거", "그거", "저거", "이것", "그것", "관련", "관련된", "대한", "대해",
    # 너무 일반적인 명사
    "자료", "파일", "폴더", "문서", "내용", "정보", "것", "거", "좀", "때", "중",
    "있는", "없는", "있어", "있나", "있지", "된", "하는", "한", "들", "등",
    # 인칭·소유
    "내", "내가", "나의", "제", "제가", "저의", "우리", "우리의", "너", "당신",
}


def extract(question: str, extra_stop: Optional[Iterable[str]] = None,
            min_len: int = 2, limit: int = 8) -> List[str]:
    """검색에 쓸 키워드만 남긴다.

    ⚠️ **조사를 떼고 나서** 불용어를 본다. 순서를 바꾸면 `내가`·`매출이` 가 그대로
       남아 아무것도 안 걸린다 — 이 함수가 생긴 이유다.
    ⚠️ 원문 표기를 유지한다 (`B2B`·`GM WEST` 처럼 대문자가 뜻인 경우가 있다).

    >>> extract("구글드라이브에서 내가 작성한 신규 입사자 교안 자료 찾아줘",
    ...         extra_stop={"구글드라이브", "작성한"})
    ['신규', '입사자', '교안']
    """
    stop = set(BASE_STOP) | {s.lower() for s in (extra_stop or ())}
    out: List[str] = []
    seen: Set[str] = set()
    for raw in _TOKEN.findall(question or ""):
        tok = strip_particle(raw)
        low = tok.lower()
        if len(tok) < min_len or low in stop or low in seen:
            continue
        # 조사를 뗀 뒤에도 불용어면 버린다 ("매출이" → "매출" 은 남고, "것을" → "것" 은 간다)
        seen.add(low)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


# ⛔ **동의어 목록을 손으로 쌓지 않는다** (2026-08-14 사용자 판단).
#    쌓는 방식은 두 가지로 위험하다:
#      ① 끝이 없다 — 새 용어마다 사람이 검수해야 하고, 안 하면 목록만 쌓인다
#      ② **틀린 동의어가 조용히 오답을 만든다** — 실제로 씨앗에 {실적·성과·매출·결산}
#         을 넣었었다. 그러면 "실적 자료" 를 찾는데 "매출 시트" 가 나오고 **그게
#         정답처럼 보인다.** 오늘 내내 막아 온 실패와 같은 종류다.
#    남긴 것은 **표기 변형**뿐이다 — 뜻이 같은 게 확실한 것만 (매뉴얼/메뉴얼).
#    뜻이 비슷할 뿐인 말은 여기 넣지 말고 LLM 확장(_llm_variants)에 맡긴다.
_SYNONYMS: List[Set[str]] = [
    {"매뉴얼", "메뉴얼", "manual"},
    {"스프레드시트", "시트", "spreadsheet"},
    {"프레젠테이션", "슬라이드", "ppt"},
]


def llm_variants(question: str, keywords: List[str]) -> List[List[str]]:
    """0건일 때 **LLM 에게 대안 검색어를 묻는다.** 사전을 쌓는 대신 이걸 쓴다.

    왜 이게 나은가:
      - 관리가 없다. 새 용어("뉴크루")가 나와도 사람이 등록할 필요가 없다
      - 맥락을 안다 — "교안" 이 교육자료·온보딩 문서라는 걸 사전 없이 안다
      - **성공 경로를 늦추지 않는다.** 0건일 때만 한 번 부른다 (Flash, 1~2초)

    ⚠️ LLM 이 낸 말이 틀릴 수 있다. 그래서 ① 파일이 실제로 그 낱말을 갖고 있어야
       결과가 나오고(판정은 Drive 가 한다) ② 넓혀 찾았다는 사실을 답변에 밝힌다.
    """
    if not keywords:
        return []
    prompt = f"""사내 구글 드라이브에서 파일을 찾으려 합니다.
질문: {question}
지금 검색어: {' '.join(keywords)}

이 검색어로는 결과가 없었습니다. **파일 이름이나 문서 본문에 실제로 쓰였을 법한**
다른 표현을 최대 3개 제안하세요. 각 제안은 낱말 1~3개로 된 검색어입니다.

규칙:
- 뜻이 같거나 같은 문서를 가리키는 말만 (예: 교안 → 온보딩, 교육자료)
- ⛔ 뜻이 다른 말로 넓히지 마세요 (예: 실적 → 매출 은 금지). 넓히면 엉뚱한 파일이
  정답처럼 보입니다 — 제안할 게 없으면 빈 배열을 주세요
- 설명 없이 JSON 만: {{"variants": ["온보딩 교육", "신입 교육자료"]}}"""
    try:
        from app.core.llm import get_flash_client
        raw = get_flash_client().generate_json(prompt, temperature=0.3)
        import json as _json
        data = _json.loads(raw) if isinstance(raw, str) else raw
        out = []
        for v in (data.get("variants") or [])[:3]:
            words = [w for w in str(v).split() if w][:3]
            if words:
                out.append(words)
        return out
    except Exception as e:
        logger.warning("llm_variants_failed", error=str(e)[:150])
        return []


def _alias_groups() -> List[Set[str]]:
    """`term_aliases` 사전에서 **같은 정식명칭을 가리키는 말끼리** 묶는다.

    ⛔ 동의어 목록을 코드에 또 만들지 마라. 이 프로젝트엔 이미 사전이 있다 —
       DB 테이블 + 관리자 화면(`/api/admin/aliases`) + **미등록 용어 자동 수집**
       (`term_alias_candidates`). 새 용어가 나올 때마다 코드를 고치는 방식은
       "수십 개를 넘기면 관리가 무너진다"고 그 모듈이 이미 적어 뒀다.

    사전은 `별칭 → 정식명칭` 한 방향인데, **같은 정식명칭을 가진 별칭들은 서로
    동의어**다. 그래서 정식명칭으로 묶으면 그대로 검색 확장에 쓸 수 있다.
    """
    try:
        from app.db.mariadb import fetch_all
        rows = fetch_all("SELECT alias, canonical FROM term_aliases")
    except Exception:
        return []
    by_canon: dict = {}
    for r in rows or []:
        c = (r.get("canonical") or "").strip()
        if not c:
            continue
        by_canon.setdefault(c, {c}).add((r.get("alias") or "").strip())
    return [g for g in by_canon.values() if len(g) > 1]


def expand(keywords: Iterable[str], limit: int = 4) -> List[List[str]]:
    """키워드를 **같은 뜻의 다른 말**로 바꾼 대안 조합들.

    원 키워드로 0건일 때만 쓴다. 한 번에 하나씩만 바꾼다 — 여러 개를 동시에 바꾸면
    조합이 폭발하고 엉뚱한 문서가 걸린다.

    >>> expand(["신규", "입사자", "교안"])[:2]
    [['신규', '신입', '교안'], ['신규', '신규입사', '교안']]
    """
    kws = [k for k in keywords if k]
    out: List[List[str]] = []
    # 사내 사전이 먼저다 — 아래 _SYNONYMS 는 사전에 없는 일반 업무어의 **씨앗**일 뿐이다
    groups = _alias_groups() + _SYNONYMS
    for i, k in enumerate(kws):
        low = k.lower()
        for group in groups:
            if low not in {g.lower() for g in group}:
                continue
            for alt in group:
                if alt.lower() == low:
                    continue
                cand = kws[:i] + [alt] + kws[i + 1:]
                if cand not in out:
                    out.append(cand)
                if len(out) >= limit:
                    return out
    return out


def log_empty(source: str, question: str, keywords: List[str], **extra) -> None:
    """0건일 때 **왜 0건인지** 남긴다.

    ⛔ "검색 결과가 없습니다" 는 정말 없을 때와 검색어가 망가졌을 때가 똑같이 생겼다.
       검색어를 함께 남겨야 나중에 구분할 수 있다 (프로덕션은 INFO 를 버리므로 WARNING).
    """
    logger.warning("search_empty", source=source, question=(question or "")[:120],
                   keywords=keywords, **extra)
    # 못 찾은 질문의 미등록 용어를 **후보 사전에 자동 적재**한다.
    # ⛔ 새 용어가 나올 때마다 코드에 동의어를 하나씩 넣는 방식은 관리가 무너진다
    #    (2026-08-14 사용자 지적). 이 프로젝트엔 이미 후보 수집 장치가 있고
    #    (`term_alias_candidates` + 관리자 화면), 채팅 0건에서만 쓰이고 있었다 —
    #    검색 0건도 같은 신호라 여기서도 태운다. 응답 경로를 늦추지 않게 별도 스레드.
    try:
        import threading

        from app.core.term_aliases import collect_candidates
        # ⚠️ **질문 문장이 아니라 추출된 키워드만** 넘긴다. 문장을 그대로 넘겼더니
        #    `드라이브`·`자료`·`찾아줘` 까지 후보로 쌓였다 (2026-08-14 실측) —
        #    검수할 사람이 잡음을 걸러야 하면 그 목록은 곧 안 보게 된다
        terms = " ".join(k for k in keywords if k)
        if terms:
            threading.Thread(target=collect_candidates, args=(terms,),
                             daemon=True).start()
    except Exception as e:
        logger.warning("candidate_collect_skipped", error=str(e)[:120])
