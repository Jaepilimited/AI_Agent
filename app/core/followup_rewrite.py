"""후속 질문을 앞 대화로 채워 '혼자 서는 질문' 으로 바꾼다 (2026-10-06).

⛔ **왜 만들었나 — 프로덕션 실측.** 맥락은 SQL 경로에만 단단했다. 직전 SQL 원문과
   조회 상태(`turn_state`)가 SQL 생성기에는 들어가지만, 라우터와 문서·제품·메일
   검색은 **현재 문장만** 보고 일했다. 그래서 이렇게 끊겼다:

       사용자: 회사 창립일 언제야?       → 2014년 8월입니다 …
       사용자: 정확한 일자 알려줘         → "어떤 항목의 일자를 찾으시는지 …"
                                           + 무관한 콘텐츠 업로드 일정 목록  (붐따 #181)

   후속 질문이 어디로 가든 같은 질문을 받게 하는 것이 해법이다. 대화 서비스들이
   쓰는 표준 방법(질문 재작성 · condense question)을 쓴다 — 짧은 후속 질문을
   앞 대화로 채운 **독립 질문** 으로 바꾼 뒤, 그 질문으로 라우팅·검색·조회를 한다.

⛔ **재작성은 확률이고, 그래서 세 겹으로 막는다.**
   1. 고르는 단계 — 후속 질문처럼 생긴 것만 재작성한다 (`needs_rewrite`).
      긴 독립 질문은 건드리지 않는다
   2. 검사 단계 — 원문과 앞 대화에 **없는 숫자** 가 생기면 버린다. 기간·수량을
      지어내는 재작성이 가장 그럴듯하게 틀리기 때문이다. 너무 길어져도 버린다
   3. 공시 단계 — 바꿨으면 답변 맨 앞에 "이렇게 이해했습니다" 를 적는다
      (`interpretation_note`). 잘못 이해했으면 사용자가 바로 보고 고칠 수 있다

⛔ **시각화·표 요청은 재작성하지 않는다** (`is_presentation_followup`). "시각화 해줘"
   를 재작성하면 SQL 을 새로 만들어 **숫자가 바뀐다** — 실측: 같은 대화에서
   2.6억 → 1.9억. 직전 SQL 을 그대로 다시 돌려 그림만 붙인다 (오케스트레이터).

💡 **재작성한 질문은 저장한다** (`query_rewrites`). 다음 후속 질문이 앞 턴을 읽을 때
   "인도네시아는?" 이 아니라 "2026년 7월 인도네시아 매출" 로 읽게 해, 후속의
   후속(그럼 일본은?)까지 이어진다. 화면·내보내기에는 섞지 않는다 (답변 본문에
   숨은 표식을 넣으면 복사·잔디·노션으로 샌다).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Rewrite:
    original: str
    standalone: str
    changed: bool
    reason: str = ""


# ── 1. 고르기 ─────────────────────────────────────────────────────────────
# 앞 대화를 가리키는 말
# ⚠️ 접속어(그럼·그리고…)는 **문장 첫머리**일 때만 앞 대화를 잇는다 — 긴 독립
#    질문 한가운데의 "그리고" 를 후속 신호로 읽으면 멀쩡한 질문을 재작성한다.
_LEAD = re.compile(r"^\s*(그럼|그러면|그리고|그래서|아니면|그런데|근데)")
_REF = re.compile(
    r"(그거|그것|그건|이거|이것|이건|저거|거기|아까|방금|"
    r"위에|위의|위에서|앞에|앞서|이전|해당|그\s*(제품|품목|기간|국가|팀|채널|라인|문서)|"
    r"같은\s*(기간|조건|기준)|정확한|자세히|더\s*알려)")
# 명사구 + 조사로 끝나는 꼬리 ("인도네시아는?", "센텔라 앰플은?")
_TAIL = re.compile(
    r"(은|는|도|만|랑|이랑|하고|말고|빼고|대신|기준으로|별로|으로|로|의)\s*[?？]?\s*$")
# 앞 조회에 무엇을 더하라는 말
_ADD = re.compile(r"(도\s*(같이|함께)|같이\s*(알려|보여)|추가로|함께\s*(알려|보여)|하면\s*[?？]?\s*$)")
# 시각화·표 — 재작성하지 않고 직전 결과를 다시 그린다
_PRESENT = re.compile(r"(시각화|차트|그래프|그려|도표|표로|표\s*형태|plot|chart|graph)", re.I)
# 이 낱말이 있으면 축이 바뀐 것이다 — 그림만 다시 그리는 게 아니라 새 조회다
_NEW_AXIS = re.compile(r"(별로|기준|국가|팀|월별|분기|주별|일별|제품|품목|채널|추가|비중|대비|증감|"
                       r"상위|하위|top|순위|년|월\b|\d)", re.I)

_SHORT = 25        # 이보다 짧으면 대개 앞 대화 없이는 뜻이 없다
_TAIL_MAX = 45     # 꼬리 규칙은 이 길이까지만 (긴 문장은 꼬리가 우연이다)
_REF_MAX = 70      # 지시어가 있어도 이보다 길면 대개 이미 혼자 선다

_REPORT_MARKER = "report-clarification-v1"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _prior_assistant(messages: List[Dict]) -> str:
    for m in reversed(messages[:-1] if messages else []):
        if m.get("role") in ("assistant", "model"):
            return _text(m.get("content", ""))
    return ""


def is_presentation_followup(query: str) -> bool:
    """"시각화 해줘"·"차트로 보여줘"·"표로" — 직전 결과를 **다시 그리기만** 하라는 말."""
    q = (query or "").strip()
    if not q or len(q) > 30 or not _PRESENT.search(q):
        return False
    stripped = _PRESENT.sub("", q)
    return not _NEW_AXIS.search(stripped)


def needs_rewrite(query: str, messages: Optional[List[Dict]]) -> bool:
    q = (query or "").strip()
    if not q or not messages or len(messages) < 3:
        return False
    if "@@" in q or q.startswith("/"):
        return False
    prior = _prior_assistant(messages)
    if not prior.strip():
        return False
    # 보고서 확인 질문에 대한 답은 보고서 흐름이 원문으로 이어받는다
    if _REPORT_MARKER in prior:
        return False
    if is_presentation_followup(q):
        return False
    n = len(q)
    if n <= _SHORT:
        return True
    if n <= _TAIL_MAX and (_TAIL.search(q) or _ADD.search(q)):
        return True
    if n <= _REF_MAX and (_REF.search(q) or _LEAD.search(q)):
        return True
    return False


# ── 2. 재작성 ─────────────────────────────────────────────────────────────
_NOISE = [
    (re.compile(r"<details[\s\S]*?</details>"), " "),
    (re.compile(r"```[\s\S]*?```"), " "),
    (re.compile(r"<!--[\s\S]*?-->"), " "),
    (re.compile(r"^>.*$", re.M), " "),
    (re.compile(r"\|[^\n]*\|"), " "),
    (re.compile(r"[#*`]+"), ""),
    (re.compile(r"\s+"), " "),
]


def _clean(text: str, cap: int) -> str:
    for pat, rep in _NOISE:
        text = pat.sub(rep, text)
    text = text.strip()
    return text[:cap] + ("…" if len(text) > cap else "")


def build_history(messages: List[Dict], stored: Optional[Dict[str, str]] = None,
                  turns: int = 3) -> str:
    """최근 몇 턴을 재작성용으로 짧게 — 사용자 질문은 저장된 해석을 함께 싣는다."""
    stored = stored or {}
    lines: List[str] = []
    pairs = 0
    for m in reversed(messages[:-1]):
        role = m.get("role")
        raw = _text(m.get("content", ""))
        if not raw.strip():
            continue
        if role == "user":
            q = raw.strip()[:200]
            seen = stored.get(_hash(q))
            lines.append(f"사용자: {q}" + (f"  (해석: {seen})" if seen and seen != q else ""))
            pairs += 1
            if pairs >= turns:
                break
        elif role in ("assistant", "model"):
            lines.append(f"AI: {_clean(raw, 500)}")
    return "\n".join(reversed(lines))


_PROMPT = """너는 사내 데이터 어시스턴트의 '질문 정리기'다. 사용자의 마지막 질문이 앞 대화에 기대고 있으면,
앞 대화에서 빠진 대상·기간·조건을 채워 **그 문장만 읽어도 뜻이 통하는 질문 한 문장** 으로 바꾼다.

## 규칙
- 사용자가 묻지 않은 것을 더하지 마라. 빠진 것을 채우기만 한다.
- 앞 질문의 조건(기간·국가·팀·채널·브랜드·제품)은 마지막 질문이 **바꾸지 않은 것은 모두 유지**한다.
  바꾼 것만 바꾼다. 조건을 하나라도 빠뜨리면 다른 질문이 된다.
- 앞 대화와 무관한 새 질문이거나 이미 혼자 뜻이 통하면 **원문 그대로** 돌려준다.
- 숫자·날짜·기간은 원문이나 앞 대화에 **실제로 나온 것만** 쓴다. 추측해서 만들지 마라.
- 사용자의 말투와 요청 형태(알려줘/보여줘/비교해줘)를 유지한다. 한국어 한 문장.
- 앞 대화의 답변 내용(숫자·결론)을 질문에 옮겨 적지 마라. 질문의 대상·조건만 채운다.
- 마지막 질문이 앞 대화의 대상과 **상관없는 일**(오늘 날짜·시간·인사·감사·이 서비스 기능
  문의·전혀 다른 주제)이면 앞 대화의 낱말을 **붙이지 말고** 원문 그대로 돌려준다.

## 예시
- 앞: "2026년 7월 미국 매출" / 마지막: "인도네시아는?" → "2026년 7월 인도네시아 매출은?"
- 앞: "2025년 3월 일본 B2C 매출" / 마지막: "히알루시카 크림은?" → "2025년 3월 일본 B2C 히알루시카 크림 매출은?"
- 앞: "회사 창립일 언제야?" / 마지막: "정확한 일자 알려줘" → "회사 창립일의 정확한 일자 알려줘"
- 앞: "메타 파트너십 광고 몇 개?" / 마지막: "오늘 날짜만 확인해줘" → "오늘 날짜만 확인해줘" (무관 — 그대로)
- 앞: "센텔라 앰플 사용법" / 마지막: "고마워" → "고마워" (무관 — 그대로)

## 앞 대화
{history}

## 마지막 질문
{query}

## 출력 (JSON 한 개)
{{"standalone": "정리된 질문", "depends": true 또는 false}}"""

_DIGITS = re.compile(r"\d+")
_MAX_LEN = 300
_TIMEOUT_S = 6.0


def _hash(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()


def _validate(query: str, standalone: str, history: str) -> str:
    """버릴 이유를 돌려준다 (빈 문자열이면 통과)."""
    s = (standalone or "").strip()
    if not s:
        return "empty"
    if len(s) > _MAX_LEN or len(s) > max(len(query) * 6, len(query) + 160):
        return "too_long"
    pool = f"{query}\n{history}"
    for d in _DIGITS.findall(s):
        if d not in pool:
            return f"invented_number:{d}"
    if "\n" in s:
        return "multiline"
    return ""


def _call_llm(prompt: str) -> str:
    from app.core.llm import get_flash_client
    return get_flash_client().generate_json(prompt, temperature=0.0, max_output_tokens=512)


async def rewrite(query: str, messages: List[Dict], user_email: str = "") -> Rewrite:
    """필요하면 재작성한다. 실패·의심이면 **원문** 을 돌려준다 (안전한 쪽으로 실패)."""
    q = (query or "").strip()
    if not needs_rewrite(q, messages):
        return Rewrite(q, q, False, "not_needed")
    stored = await asyncio.to_thread(lookup, user_email, [
        _text(m.get("content", "")).strip()[:200]
        for m in messages[:-1] if m.get("role") == "user"]) if user_email else {}
    history = build_history(messages, stored)
    prompt = _PROMPT.format(history=history, query=q)
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_call_llm, prompt), _TIMEOUT_S)
        from app.core.llm import repair_json
        data = json.loads(repair_json(raw))
        standalone = str(data.get("standalone", "")).strip()
    except Exception as e:  # 타임아웃·파싱 실패 — 원문으로 간다
        logger.warning("followup_rewrite_failed", error=str(e)[:160], query=q[:80])
        return Rewrite(q, q, False, "llm_failed")
    bad = _validate(q, standalone, history)
    if bad:
        logger.warning("followup_rewrite_rejected", reason=bad, query=q[:80],
                       standalone=standalone[:160])
        return Rewrite(q, q, False, bad)
    if _norm(standalone) == _norm(q):
        return Rewrite(q, q, False, "unchanged")
    logger.warning("followup_rewritten", query=q[:80], standalone=standalone[:200])
    rw = Rewrite(q, standalone, True, "rewritten")
    if user_email:
        await asyncio.to_thread(save, user_email, rw)
    return rw


def _norm(s: str) -> str:
    return re.sub(r"[\s?？.!]+", "", s or "")


def interpretation_note(rw: Rewrite) -> str:
    """답변 맨 앞 한 줄 — 잘못 이해했으면 사용자가 바로 고칠 수 있게."""
    if not rw.changed:
        return ""
    return f"> 💬 앞 대화를 이어 **「{rw.standalone}」** 로 이해했습니다."


# ── 3. 저장 ───────────────────────────────────────────────────────────────
_TABLE_READY = False
_DDL = """CREATE TABLE IF NOT EXISTS query_rewrites (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL,
    original_hash CHAR(40) NOT NULL,
    original TEXT NOT NULL,
    standalone TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_user_hash (user_email, original_hash)
) DEFAULT CHARSET=utf8mb4"""


def _ensure_table() -> bool:
    global _TABLE_READY
    if _TABLE_READY:
        return True
    try:
        from app.db.mariadb import execute
        execute(_DDL)
        _TABLE_READY = True
    except Exception as e:
        logger.warning("query_rewrites_table_failed", error=str(e)[:160])
    return _TABLE_READY


def save(user_email: str, rw: Rewrite) -> None:
    if not (user_email and rw.changed and _ensure_table()):
        return
    try:
        from app.db.mariadb import execute
        execute("INSERT INTO query_rewrites (user_email, original_hash, original, standalone) "
                "VALUES (%s, %s, %s, %s)",
                (user_email, _hash(rw.original[:200]), rw.original[:2000], rw.standalone[:2000]))
    except Exception as e:
        logger.warning("query_rewrite_save_failed", error=str(e)[:160])


def lookup(user_email: str, originals: List[str]) -> Dict[str, str]:
    """{원문 해시: 가장 최근 해석}. 실패하면 빈 사전 — 저장은 보조이지 관문이 아니다."""
    hashes = list({_hash(o) for o in originals if o})
    if not (user_email and hashes and _ensure_table()):
        return {}
    try:
        from app.db.mariadb import fetch_all
        marks = ",".join(["%s"] * len(hashes))
        rows = fetch_all(
            f"SELECT original_hash, standalone FROM query_rewrites "
            f"WHERE user_email=%s AND original_hash IN ({marks}) ORDER BY id",
            (user_email, *hashes))
        return {r["original_hash"]: r["standalone"] for r in rows}
    except Exception as e:
        logger.warning("query_rewrite_lookup_failed", error=str(e)[:160])
        return {}
