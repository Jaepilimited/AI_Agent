# -*- coding: utf-8 -*-
"""회사 식별번호를 검색 없이 즉답한다.

**왜 만들었나** (2026-08-27, 붐따 #154 + 로그 실측):

    사업자등록번호       notion  11.1s
    크레이버 사업자번호   notion   6.2s
    우리회사 사업자등록번호 notion  11.2s
    cs 대시보드 주소알려줘  direct   0.0s   ← 결정적으로 답하는 것은 0초다

    같은 것을 **8명이 다른 문장으로** 물었고 매번 5~11초가 걸렸다. 전부 노션 벡터
    검색을 타기 때문이다. 붐따 #154: *"사업자등록번호 같이 금방 나올 수 있는
    결과값은 출력 시간을 단축할 수 있는지"*.

⛔ **여기에 바뀌는 값을 넣지 마라.** 담는 것은 **법으로 부여돼 사실상 바뀌지 않는
   식별번호**뿐이다. 대표자·주소·조직 같은 것은 바뀌고, 손으로 적으면 반드시 낡는다
   (실제로 direct 프롬프트의 주소가 노션과 이미 어긋나 있다 — 층수가 다르다).
   그런 것은 계속 노션이 답한다. 여기서 빠르게 답하려고 넣는 순간 조용한 오답이 된다.

⛔ **값을 추정하지 마라.** 2026-08-27 에 사내 노션
   (`PEOPLE > CRAVER 지식in > 회사 정보 Craver`)에서 실제로 조회해 넣었다.
   등록번호는 그럴듯하게 틀리면 대외 문서에 그대로 쓰이고, 틀렸다는 사실이 한참
   뒤에 드러난다. 바꿀 일이 생기면 노션 원본을 먼저 확인할 것.
"""
from __future__ import annotations

import re

#: 출처: 사내 노션 `회사 정보 Craver` (2026-08-27 확인)
#: ⚠️ 사업자등록증은 하나로 통합돼 있고 SKIN1004·COMMONLABS 도 이 번호를 쓴다.
BUSINESS_NUMBER = "261-81-14845"
CORPORATE_NUMBER = "110111-5449312"
COMPANY_NAME = "(주)크레이버코퍼레이션"
SOURCE_NOTE = "사내 노션 「회사 정보 Craver」 기준"

#: 묻는 대상. ⚠️ `법인서류`·`법인카드` 는 **절차 문서**라 여기서 가로채면 안 된다 —
#: 노션이 답해야 하는 질문이다. 그래서 번호를 가리키는 표현만 좁게 잡는다.
_BUSINESS_RE = re.compile(r"사업자\s*(등록)?\s*번호|사업자등록증\s*번호")
_CORPORATE_RE = re.compile(r"법인\s*(등록)?\s*번호")

#: ⛔ 번호 자체가 아니라 **서류**를 원하는 질문은 넘긴다. "사업자등록증 사본 어디
#:    있어" 에 번호만 던지면 묻지 않은 것을 답하는 셈이다.
_WANTS_DOCUMENT = re.compile(
    r"등록증|사본|파일|다운|발급|첨부|어디\s*있|어딧|스캔|pdf", re.IGNORECASE)

#: ⛔ 남의 회사를 물었으면 우리 번호를 주면 안 된다.
_OTHER_COMPANY = re.compile(r"거래처|고객사|상대\s*회사|협력사|벤더|공급업체")


def answer(query: str) -> str | None:
    """식별번호 질문이면 즉답을, 아니면 None 을 돌려준다 (원래 경로로 보낸다)."""

    text = (query or "").strip()
    if not text or _OTHER_COMPANY.search(text):
        return None

    wants_business = bool(_BUSINESS_RE.search(text))
    wants_corporate = bool(_CORPORATE_RE.search(text))
    if not (wants_business or wants_corporate):
        return None

    # 번호가 아니라 서류를 원하면 노션이 답해야 한다. 단 "등록증 번호" 는 번호다.
    if _WANTS_DOCUMENT.search(text) and not re.search(r"등록증\s*번호", text):
        return None

    lines = [f"{COMPANY_NAME} 정보입니다."]
    if wants_business:
        lines.append(f"- 사업자등록번호: **{BUSINESS_NUMBER}**")
    if wants_corporate:
        lines.append(f"- 법인등록번호: **{CORPORATE_NUMBER}**")
    lines.append("")
    lines.append(f"({SOURCE_NOTE})")
    return "\n".join(lines)
