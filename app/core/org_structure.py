"""Verified company-structure facts used by deterministic chat answers.

This module is the source of truth for official team names and the country
scopes that have been explicitly verified.  Data analysis questions continue
to use BigQuery; only simple organization-fact questions are answered here.
"""

from __future__ import annotations

import re
import unicodedata


# Official Team_NEW code/name mapping (confirmed against the organization chart).
TEAM_CODE2KR = {
    "B2B1": "영업1팀",
    "B2B2": "영업2팀",
    "DT1": "유통1팀",
    "DT2": "유통2팀",
    "EAST1": "동남아시아1팀",
    "EAST2": "동남아시아2팀",
    "WEST_MKT": "서구권마케팅팀",
    "WEST_Ecomm": "서구권이커머스팀",
    "CBT": "중국사업팀",
    "JBT": "일본사업팀",
    "KBT": "한국사업팀",
    "BCM": "브랜드커뮤니케이션팀",
}


TEAM_DIVISIONS = {
    "글로벌마케팅본부": [
        "CBT", "EAST1", "EAST2", "JBT", "KBT", "WEST_Ecomm", "WEST_MKT",
    ],
    "영업1본부": ["B2B1", "B2B2"],
    "유통1본부": ["DT1"],
    "유통2본부": ["DT2"],
    "상품본부": ["BCM"],
}


# Only scopes explicitly confirmed by the business are listed.  A missing team
# is unknown here, not evidence that it has no assigned countries.
#
# ⛔ 서구권 두 팀은 **광고가 아니라 매출로** 확정했다 (2026-09-02 사용자 확인).
#    광고 테이블로 세면 노출 지역까지 잡혀 WEST_Ecomm 201개국 · WEST_MKT 83개국이
#    나오는데, 그건 담당 범위가 아니라 노출 범위다. 실제로 판 곳(2026년 실측)은
#    WEST_Ecomm 미국 91.2%·호주 8.7%, WEST_MKT 미국뿐이다.
#    ⚠️ 대조군이 이 방법을 보증한다 — EAST1·EAST2 는 매출로 세도 등록값과 정확히 같다.
VERIFIED_TEAM_COUNTRY_SCOPES = {
    "EAST1": ("인도네시아", "필리핀"),
    "EAST2": ("말레이시아", "싱가포르"),
    "WEST_MKT": ("미국",),
    "WEST_Ecomm": ("미국", "호주"),
}


#: 국가 목록만 내놓으면 오해가 생기는 팀에 덧붙이는 한 줄.
#: ⛔ 비율을 적지 마라 — 해마다 변하고, 변하면 조용히 틀린 문장이 된다.
_SCOPE_NOTES = {
    "WEST_MKT": (
        "이 팀 매출의 대부분은 국가가 붙지 않는 글로벌 B2B 플랫폼 거래라, "
        "국가로 나누면 미국만 남습니다."
    ),
}


#: 권역명 — **여러 팀에 걸치는 말**이라 담당을 특정할 수 없다.
#: ⛔ 그냥 통과시키지 마라. 팀 이름이 아니니 관문을 못 넘고 검색 경로로 새서
#:    무관한 Notion 문서가 나온다 — 이 관문이 막으려던 바로 그 실패다.
#:    모르면 지어내지도 말고 흘리지도 말고 **되묻는다** (2026-09-02 사용자 지시).
_AMBIGUOUS_REGION_TEAMS = {
    "서구권": ("WEST_MKT", "WEST_Ecomm"),
    "동남아시아": ("EAST1", "EAST2"),
    "동남아": ("EAST1", "EAST2"),
}


_TEAM_SCOPE_ALIASES = {
    "EAST1": ("EAST1", "동남아시아1팀", "동남아시아 1팀", "동남아1팀", "동남아 1팀"),
    "EAST2": ("EAST2", "동남아시아2팀", "동남아시아 2팀", "동남아2팀", "동남아 2팀"),
    "WEST_MKT": ("WEST_MKT", "서구권마케팅팀", "서구권 마케팅팀", "서구권마케팅", "WEST MKT"),
    "WEST_Ecomm": (
        "WEST_Ecomm", "서구권이커머스팀", "서구권 이커머스팀", "서구권이커머스",
        "WEST Ecomm", "서구권 e커머스팀", "서구권e커머스팀",
    ),
}

_SCOPE_INTENT_RE = re.compile(
    r"(?:(?:담당|관할).*(?:국가|나라|지역|어디)|"
    r"(?:국가|나라|지역|어디).*(?:담당|관할))",
    re.IGNORECASE,
)
#: 반대 방향 — "미국은 어느 팀이 맡아?". 국가를 대고 팀을 묻는다.
_TEAM_LOOKUP_RE = re.compile(
    r"(?:어느|어떤|무슨|어디)팀|담당팀|관할팀|팀.{0,6}(?:담당|관할)",
    re.IGNORECASE,
)
_DATA_INTENT_RE = re.compile(
    r"(?:매출|판매|판매량|수량|광고|광고비|roas|비용|원가|실적|추이|증감|"
    r"합계|평균|순위|프로모션|캘린더|일정|재고|보고서|리포트)",
    re.IGNORECASE,
)


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", value)


def _scope_team_code(query: str) -> str | None:
    normalized = _normalize(query)
    for code, aliases in _TEAM_SCOPE_ALIASES.items():
        if any(_normalize(alias) in normalized for alias in aliases):
            return code
    return None


def _has_final_consonant(word: str) -> bool:
    """마지막 글자에 받침이 있는가. 한글이 아니면 없는 것으로 본다."""
    if not word:
        return False
    last = word[-1]
    if not ("가" <= last <= "힣"):
        return False
    return (ord(last) - 0xAC00) % 28 != 0


def _josa(word: str, with_final: str, without_final: str) -> str:
    """받침에 따라 조사를 고른다 — `미국은` / `호주는`, `미국과` / `호주와`."""
    return with_final if _has_final_consonant(word) else without_final


def _join_ko(items, bases=None) -> str:
    """`미국과 호주` / `인도네시아와 필리핀` — 받침에 따라 과·와를 고른다.

    ⛔ `"와 ".join(...)` 로 두지 마라. 받침 있는 낱말에 붙으면 `미국와 호주` 가 되고,
       자동 생성 티가 나는 순간 답 전체의 신뢰가 깎인다.
    ⚠️ `bases` 는 **조사를 정할 때만** 쓰는 맨 낱말이다. 화면에 나가는 문자열이
       `**팀이름(CODE)**` 처럼 장식돼 있으면 마지막 글자가 한글이 아니라서
       받침 판정이 통째로 빗나간다 — 그때 맨 이름을 따로 넘긴다.
    """
    items = list(items)
    keys = list(bases) if bases is not None else items
    if len(items) <= 1:
        return "".join(items)
    out = ""
    for item, key in zip(items[:-1], keys[:-1]):
        out += item + _josa(key, "과 ", "와 ")
    return out + items[-1]


def _ambiguous_region(query: str) -> tuple[str, tuple[str, ...]] | None:
    """질문이 팀 대신 **권역**을 댔으면 (권역명, 그 안의 팀들) 을 돌려준다.

    ⚠️ 긴 것부터 본다 — `동남아` 가 `동남아시아` 안에 들어 있어, 짧은 쪽이 먼저
       맞으면 되묻는 문장에 엉뚱한 낱말이 실린다.
    """
    normalized = _normalize(query)
    for word in sorted(_AMBIGUOUS_REGION_TEAMS, key=len, reverse=True):
        if _normalize(word) in normalized:
            return word, _AMBIGUOUS_REGION_TEAMS[word]
    return None


def _teams_for_country(query: str) -> tuple[str, list[str]] | None:
    """질문에 등록된 국가가 있으면 (국가, 담당 팀 코드들) 을 돌려준다."""
    normalized = _normalize(query)
    for country in sorted(
        {c for scope in VERIFIED_TEAM_COUNTRY_SCOPES.values() for c in scope},
        key=len,
        reverse=True,
    ):
        if _normalize(country) in normalized:
            codes = [code for code, scope in VERIFIED_TEAM_COUNTRY_SCOPES.items()
                     if country in scope]
            if codes:
                return country, codes
    return None


def answer_team_country_scope(query: str) -> str | None:
    """Answer verified team-country responsibility questions without retrieval.

    Metric, schedule, and report questions are deliberately excluded so they
    keep using the data pipeline instead of being replaced by a static fact.
    """
    text = query or ""
    if _DATA_INTENT_RE.search(text):
        return None
    normalized = _normalize(text)

    # ── 팀 → 국가 ─────────────────────────────────────────────
    if _SCOPE_INTENT_RE.search(normalized):
        code = _scope_team_code(text)
        countries = VERIFIED_TEAM_COUNTRY_SCOPES.get(code or "")
        if code and countries:
            label = TEAM_CODE2KR[code]
            body = (
                f"**{label}({code}) 담당 국가**\n\n"
                f"{label}이 담당하는 국가는 **{_join_ko(countries)}**입니다.\n\n"
            )
            note = _SCOPE_NOTES.get(code)
            if note:
                body += f"{note}\n\n"
            return body + "> 현재 시스템에 등록된 회사 조직 기준입니다."

    # ── 국가 → 팀 ─────────────────────────────────────────────
    # ⚠️ 한 나라를 두 팀이 맡을 수 있다 (서구권은 국가가 아니라 기능으로 나뉜다).
    #    하나만 고르면 나머지 팀이 조용히 사라진다 — 둘 다 적는다.
    if _TEAM_LOOKUP_RE.search(normalized):
        found = _teams_for_country(text)
        if found:
            country, codes = found
            labels = [TEAM_CODE2KR[c] for c in codes]
            named = [f"**{TEAM_CODE2KR[c]}({c})**" for c in codes]
            subject = country + _josa(country, "은", "는")
            if len(codes) == 1:
                line = f"{subject} {named[0]}이 담당합니다."
                tail = "> 현재 시스템에 등록된 회사 조직 기준입니다."
            else:
                line = (f"{subject} {_join_ko(named, labels)} "
                        f"**{len(codes)}개 팀**이 함께 담당합니다.")
                tail = (
                    "> 서구권은 국가가 아니라 기능(마케팅 · 이커머스)으로 나뉘어 있어 "
                    "한 나라를 두 팀이 함께 맡습니다."
                )
            return f"**{country} 담당 팀**\n\n{line}\n\n{tail}"

    # ── 권역명은 팀이 아니다 — 되묻는다 ───────────────────────
    # ⚠️ 되묻되 **후보를 담당 국가까지 붙여서** 보여준다. 그냥 "어느 팀인가요?" 만
    #    던지면 사용자가 팀 이름을 몰라 한 턴을 더 버린다.
    if _SCOPE_INTENT_RE.search(normalized) or _TEAM_LOOKUP_RE.search(normalized):
        region = _ambiguous_region(text)
        if region:
            word, codes = region
            lines = [
                f"**{word}**{_josa(word, '은', '는')} 팀 이름이 아니라 권역이라 "
                f"담당을 특정할 수 없습니다. {word}에는 팀이 {len(codes)}개 있습니다 — "
                "어느 팀을 말씀하시나요?",
                "",
            ]
            for code in codes:
                scope = VERIFIED_TEAM_COUNTRY_SCOPES.get(code) or ()
                scope_text = _join_ko(scope) if scope else "담당 국가 미등록"
                lines.append(f"- **{TEAM_CODE2KR[code]}({code})** — {scope_text}")
            return "\n".join(lines)

    return None

