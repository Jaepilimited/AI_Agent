# -*- coding: utf-8 -*-
"""수출 물류 금액 = **유상 + 무상**. `amount` 만 쓰면 무상분이 통째로 빠진다.

붐따 #159 (2026-09-03, 전휘빈 제보):

    "주문 금액이 다르게 나와야 됩니다.
     (지금 나온 값은 유상 7555.72 인데 무상1,108.40 까지 합쳐진 8,664.12가
      출력되어야 함) 지금은 amount 컬럼의 값만 가져오는 것 같아요"

실측(`202606010105`)이 제보와 정확히 같다:

    amount 7,555.72 · free_amount 1,108.40 · total_amount **8,664.12**

⛔ **그런데 `total_amount` 만 쓰면 절반이 사라진다** (2026-09-03 프로덕션 실측,
   삭제 제외 2,491행):

    amount 있음                     2,231
    total_amount 있음               1,046
    ⛔ amount 는 있는데 total 없음   1,220   ← total 만 쓰면 이만큼 금액이 빈다
    ⛔ total 만 있고 amount 없음        35   ← amount+free 만 쓰면 이만큼 빠진다
    ⛔ 둘 다 있는데 값이 어긋남         107

   그래서 `IFNULL(total_amount, amount + IFNULL(free_amount, 0))` 이다.
   ⚠️ 둘 다 있으면 `total_amount` 를 믿는다 (2026-09-03 사용자 확정) — 시트에
      나중에 추가된 칸이고, 제보자가 그것을 답으로 지목했다.

**방어는 두 겹이고, 두 겹이 하는 일이 다르다**:

  1. `fix_sql()` — **집계 안의 `amount` 만** 고쳐 쓴다 (`SUM`·`AVG`·`MAX`·`MIN`).
     ⛔ 집계 밖의 `amount` 는 건드리지 않는다. 셀렉트 목록에서 바꾸려면 별칭을
        붙여야 하는데(`… AS amount`), 같은 토큰이 `GROUP BY`·`ORDER BY` 에도
        나올 수 있어 거기에 별칭을 붙이면 **SQL 이 깨진다**. 그리고 우리에겐
        SQL 파서가 없다 — `validate_sql()` 은 문법을 보지 않는다.
        고쳐 쓰는 것은 되돌릴 수 없으므로 **확실히 안전한 자리에서만** 한다
        (금액 배율 교정이 허용오차를 검증보다 엄격하게 둔 것과 같은 사상).
  2. `notice()` — 그 밖의 경우에는 **고치지 않고 말한다**. 유상만 나갔다는
     사실이 답변에 남으면, 사람이 8,664.12 를 기대하다 7,555.72 를 보고
     조용히 넘어가는 일은 없다.

생성 자체는 프롬프트(`prompts/sql_generator.txt` 테이블 16)가 맡는다 — 여기는
그것이 안 걸렸을 때의 그물이다.
"""
from __future__ import annotations

import re

import structlog

logger = structlog.get_logger(__name__)

TABLE = "export_logistics"

# 유상+무상. ⚠️ 한 곳에만 둔다 — 프롬프트·SQL 교정·회귀가 같은 문자열을 본다
TOTAL_EXPR = "IFNULL(total_amount, amount + IFNULL(free_amount, 0))"

# `SUM(amount)` · `sum( amount )` — 집계 안의 홑 `amount` 만.
# ⚠️ `\bamount\b` 는 `free_amount`·`total_amount` 에 매치되지 않는다
#    (`_` 가 단어 문자라 경계가 생기지 않는다). 그 성질에 기대므로 회귀가 지킨다.
_RE_AGG = re.compile(r"\b(SUM|AVG|MAX|MIN)\s*\(\s*amount\s*\)", re.IGNORECASE)

# 이 SQL 이 금액을 읽는가 (교정·공시 판정 공통)
_RE_BARE_AMOUNT = re.compile(r"\bamount\b", re.IGNORECASE)


def _touches_logistics(sql: str) -> bool:
    return TABLE in (sql or "").lower()


def fix_sql(sql: str) -> str:
    """집계 안의 `amount` 를 유상+무상으로 고친다. 그 밖은 그대로 둔다."""
    if not sql or not _touches_logistics(sql):
        return sql
    if "total_amount" in sql.lower():
        # 이미 총액을 다루고 있다 — 손대면 두 번 더하거나 뜻이 겹친다
        return sql
    fixed, n = _RE_AGG.subn(lambda m: f"{m.group(1)}({TOTAL_EXPR})", sql)
    if n:
        logger.info("logistics_amount_sql_fixed", replaced=n)
    return fixed


def notice(sql: str = "") -> str:
    """고쳐 쓰지 못한 채 유상 금액만 나가면 그 사실을 공시한다.

    ⛔ **표보다 먼저 말한다** — 뒤에 붙이면 후속 질문 제안·실행된 쿼리 뒤로
       밀려 정작 숫자를 읽는 사람 눈에 닿지 않는다 (같은 파일들의 다른 공시와
       같은 규칙).
    """
    low = (sql or "").lower()
    if not _touches_logistics(low):
        return ""
    if not _RE_BARE_AMOUNT.search(low):
        return ""                      # 금액을 안 물었다
    if "total_amount" in low:
        return ""                      # 총액을 쓰고 있다 — 공시할 것이 없다
    logger.info("logistics_amount_paid_only_notice")
    nl = chr(10)
    return (
        "> ⚠️ **아래 금액은 유상분만입니다 — 무상(FOC) 금액이 빠져 있습니다.**" + nl
        + "> 수출 금액은 보통 **유상 + 무상**으로 봅니다. 총액이 필요하시면 "
        + "「무상 포함 금액」·「총액」이라고 덧붙여 다시 물어봐 주세요."
        + nl + nl)


# ── 「한화로 바꿔줘」 — 환산하지 않는다. 그렇다고 딴 컬럼을 갖다 붙이지도 않는다 ──
#
# 붐따 #162 (2026-09-04, 정다운 제보):
#
#     "수출 자료 다운로드시, 한화로 변환이 안되는것같습니다!"
#
# 실제로 일어난 일은 "환산이 안 된 것" 이 아니라 **엉뚱한 값이 한화 금액 행세를
# 한 것**이다. "금액은 한화로 다 바꿔줘" 에 대해 생성된 SQL 이 이랬다:
#
#     SUM(cost_total_krw) AS total_export_amount_krw,  'KRW' AS currency
#
# `cost_total_krw` 는 **물류비**(포워더·관세·통관 수수료)이지 수출 금액이 아니다.
# 게다가 거의 비어 있다 — 실측(2026-08, 삭제 제외 385건):
#
#     금액(유상+무상) 채워진 행   302건
#     cost_total_krw 채워진 행     14건  (3.6%)   ← 이걸로 답했다
#
# 그래서 답변 표는 미국 65건·캐나다 44건이 전부 `null` 이었고, 제목은
# 「담당자별 한화(KRW) 기준 수출 물류 실적」이었으며, 인사이트는 그 null 을
# **"원화 정산 처리가 완료되지 않았을 가능성"** 이라고 지어냈다. 에러는 없었다.
#
# ⛔ 프롬프트에는 이미 "환율 환산도 하지 마라" 가 있었다. LLM 은 그 말을 지키면서
#    **다른 컬럼으로 우회**했다 — 금지어를 늘리는 것으로는 끝나지 않는다.
#    프롬프트는 확률이고 보증은 코드다 (FI 마스킹·내부 경로 마스킹과 같은 사상).
#
# ⚠️ 환율 데이터로 진짜 환산하지 않는 이유: `fx_rates` 는 **오늘치 고시**를 모으는
#    것이라 지난달 선적을 오늘 환율로 바꾸면 그럴듯하게 틀린다. 조용한 오답을
#    만드느니 못 한다고 말한다. 환산이 가능해지면 이 공시를 걷어내면 된다.

_KRW_WORD = r"(?:한화|원화|KRW|krw|원貨)"
_CONVERT_VERB = r"(?:환산|변환|바꿔|바꾸|바꾼|통일|맞춰|convert)"
# "한화로 다 바꿔줘" · "원화로 환산해줘" · "KRW 로 통일" — 통화어 → 동사
_RE_KRW_THEN_VERB = re.compile(_KRW_WORD + r".{0,12}?" + _CONVERT_VERB, re.IGNORECASE)
# "환산해서 원화로" — 드물지만 반대 순서도 받는다
_RE_VERB_THEN_KRW = re.compile(_CONVERT_VERB + r".{0,12}?" + _KRW_WORD, re.IGNORECASE)


def wants_krw_conversion(question: str) -> bool:
    """질문이 **금액을 원화로 바꿔 달라**고 했는가.

    ⚠️ 통화어만으로는 판정하지 않는다 — "KRW 로 결제된 건" 은 환산 요청이 아니다.
       바꿔 달라는 **동사**가 함께 있을 때만 참이다.
    """
    q = question or ""
    return bool(_RE_KRW_THEN_VERB.search(q) or _RE_VERB_THEN_KRW.search(q))


# `SUM(cost_total_krw) AS total_export_amount_krw` — 물류비를 집계해 놓고
# 별칭이 **금액·매출** 을 주장하는 자리. 별칭이 `cost`·`물류비` 면 정상이라 안 건다.
_RE_COST_AS_AMOUNT = re.compile(
    r"\b(?:SUM|AVG|MAX|MIN)\s*\(\s*(?:[a-z_]*_)?cost(?:_[a-z_]*)?_krw\s*\)"
    r"\s*AS\s+([A-Za-z0-9_가-힣]+)",
    re.IGNORECASE)
_RE_AMOUNT_CLAIM = re.compile(
    r"(amount|revenue|sales|export|금액|매출|실적)", re.IGNORECASE)
_RE_COST_CLAIM = re.compile(r"(cost|비용|물류비|운임|관세)", re.IGNORECASE)


def cost_labelled_as_amount(sql: str) -> bool:
    """물류비 컬럼에 **금액이라고 주장하는 별칭**이 붙었는가."""
    if not _touches_logistics((sql or "").lower()):
        return False
    for alias in _RE_COST_AS_AMOUNT.findall(sql or ""):
        if _RE_AMOUNT_CLAIM.search(alias) and not _RE_COST_CLAIM.search(alias):
            return True
    return False


def krw_notice(sql: str = "", question: str = "") -> str:
    """한화 환산을 요구받았거나, 물류비가 금액 행세를 하면 **표보다 먼저** 말한다.

    ⛔ 값을 고치지 않는다 — 사실을 적고 판단을 사람에게 넘긴다.
    ⚠️ 조건이 좁아 평소에는 뜨지 않는다. 매번 뜨는 경고는 곧 아무도 안 읽는다.
    """
    low = (sql or "").lower()
    if not _touches_logistics(low):
        return ""
    mislabelled = cost_labelled_as_amount(sql)
    # ⛔ 실제로 환산했으면 "못 했습니다" 라고 말하면 안 된다 — 공시는
    #    `logistics_fx.notice()` 가 기준(월말환율)을 밝히는 쪽으로 넘어간다.
    #    2026-09-06 사내 환율표가 올라와 환산이 가능해졌다 (그전엔 늘 못 했다).
    from app.core.logistics_fx import converts_to_krw
    asked = wants_krw_conversion(question) and not converts_to_krw(sql)
    if not (mislabelled or asked):
        return ""
    nl = chr(10)
    parts = []
    if mislabelled:
        logger.warning("logistics_cost_labelled_as_amount", sql=(sql or "")[:400])
        parts.append(
            "> ⛔ **아래 「원(KRW)」 값은 수출 금액이 아닙니다 — 물류비입니다.**" + nl
            + "> 운임·관세·통관 수수료 등을 합한 값이라 수출 금액(인보이스 금액)과 "
            + "다르고, **전체 건 중 일부에만 기록돼 있습니다**(2026년 8월 기준 "
            + "385건 중 14건). 비어 있는 칸은 「금액이 0원」이 아니라 "
            + "**물류비가 아직 입력되지 않은 것**입니다." + nl + nl)
    if asked:
        logger.info("logistics_krw_conversion_declined")
        parts.append(
            "> ⚠️ **금액을 한화로 환산해 드리지 못했습니다.**" + nl
            + "> 수출 건은 통화가 섞여 있는데(USD·EUR·JPY·CNY·KRW, 통화가 비어 있는 "
            + "건도 있습니다) **거래 시점 환율을 가지고 있지 않아** 원화로 바꾸면 "
            + "그럴듯하게 틀린 값이 됩니다. 아래 금액은 **각 건에 기재된 통화의 "
            + "원값**이니 「통화」 칸과 함께 보셔야 합니다." + nl
            + "> 원화로 보고 싶으시면 「통화가 KRW 인 건만」이라고 물어봐 주세요." + nl + nl)
    return "".join(parts)
