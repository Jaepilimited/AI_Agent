"""Google Workspace Sub Agent (v4.3 — per-user OAuth2 + timeout + recursion limit).

Replaces MCP-based single-user approach with individual OAuth2 authentication.
Each user authenticates with their own Google account to access Gmail/Drive/Calendar.
Uses Gemini Flash as ReAct agent with bound API tools.

v4.1: Added 120s timeout for ReAct agent to prevent 300s+ hangs on complex searches.
v4.2: Added recursion_limit=10 to cap tool call iterations (~4-5 tool calls max).
v4.3: Switched from Claude Sonnet to Gemini Flash (Sonnet was the only live
consumer of AgentModel.GWS_AGENT — no other user-facing model selection
exists in this app, so there's no reason to keep two model providers here).
"""

import asyncio
import re
from datetime import datetime, timedelta
from typing import List
from zoneinfo import ZoneInfo

import structlog

logger = structlog.get_logger(__name__)

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.tools import tool
    from langgraph.errors import GraphRecursionError
    from langgraph.prebuilt import create_react_agent
    _LANGCHAIN_AVAILABLE = True
except Exception as e:
    logger.warning("gws_agent_langchain_import_failed", error_type=type(e).__name__)
    _LANGCHAIN_AVAILABLE = False

from app.config import get_settings
from app.core.google_auth import GoogleAuthManager
from app.core import query_keywords
from app.core.google_workspace import list_calendar_events, search_drive, search_gmail
from app.core.query_keywords import BASE_STOP
from app.core.textmatch import strip_particle

_auth_manager = None
_SEOUL = ZoneInfo("Asia/Seoul")


def _current_question(query: str) -> str:
    """Remove conversation history before using text as an API search query."""
    marker = "[현재 질문]"
    if marker in (query or ""):
        return query.rsplit(marker, 1)[1].strip()
    return (query or "").strip()


_GMAIL_OPERATOR_RE = re.compile(
    r'(?<![\w-])-?(?:from|to|cc|bcc|subject|label|in|is|has|filename|'
    r'after|before|older|newer|newer_than|older_than):(?:"[^"]*"|\S+)',
    re.IGNORECASE,
)

# "Christopher 로부터" · "김대리에게서" · "Chris가 보낸" → `from:` (붐따 #148·#149)
# ⛔ 사람 이름을 **본문 검색어**로 넣으면 못 찾는다. 이름은 보낸사람 헤더에 있지
#    메일 본문에 있는 게 아니다. 게다가 AND 라 다른 낱말까지 같이 죽는다.
# ⚠️ 잘못 잡을 수 있다("고객사로부터"). 그래서 `from:` 은 **되돌릴 수 있는 층**으로
#    둔다 — 0건이면 좁혀 찾기 사다리(`run_gmail_search`)가 이 조건도 떼고 다시 본다.
#: ⚠️ 존칭이 붙으면 `from:이해인님` 이 된다 — Gmail 이 못 찾는다. 정규식에서
#:    한 번 받아 주고(`(?:님|씨)?`) 잡힌 값에서도 한 번 더 뗀다(`_strip_honorific`).
#: ⚠️ `발송한`·`보내신` 도 같은 뜻이다 — 동사가 하나뿐이면 "대표님이 발송한 메일" 이
#:    보낸사람으로 안 잡히고 `in:sent`(내 보낸편지함)로 샌다.
_SENDER_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9._'-]+|[가-힣]{2,4})\s*(?:님|씨)?\s*"
    r"(?:으로부터|로부터|에게서|한테서"
    r"|(?:가|이|께서)\s*(?:보낸|보내신|보내준|보내주신|발송한|발신한))"
)


#: 이름을 `from:` 으로 뺀 뒤 남는 존칭 조각 — 검색어가 되면 AND 로 걸려 0건이 된다.
_HONORIFIC_ONLY = re.compile(r"^(?:님|씨)(?:이|가|께서|은|는|의|들)?$")


def _strip_honorific(name: str) -> str:
    """`이해인님` → `이해인`. ⚠️ 두 글자 미만으로 줄면 떼지 않는다 (조사 규칙과 같다)."""
    for suffix in ("님", "씨"):
        if name.endswith(suffix) and len(name) - len(suffix) >= 2:
            return name[: -len(suffix)]
    return name

# "과거 1년 이내" · "최근 3개월" · "지난 2주" → newer_than: (붐따 #149)
# ⛔ "약 1년 **전**" 은 여기 걸리면 안 된다 — 1년 안쪽이 아니라 그 무렵이라, 기간을
#    걸면 정작 찾던 그 메일이 빠진다. 연산자를 안 붙이는 편이 옳다.
_REL_PERIOD_RE = re.compile(
    r"(?:(최근|지난|과거)\s*)?(\d+)\s*(년|개월|달|주|일)\s*(이내|이후|동안|간|내)?(?!\s*전)"
)
_PERIOD_UNIT = {"년": "y", "개월": "m", "달": "m", "일": "d"}

# 메일 요청에만 나오는 말 — 뜻을 좁히지 못한다. 나머지 일반 불용어는 공용
# `query_keywords.BASE_STOP` 이 맡는다 (⛔ 여기에 사본을 만들지 말 것)
_MAIL_STOP = {
    "로부터", "으로부터", "에게서", "한테서", "보낸", "왔는데", "왔어", "왔습니다",
    "왔다", "온", "참고해서", "참고", "이내", "이후", "동안", "모두", "전부",
    "과거", "지난", "최근", "약",
}


def build_gmail_query(query: str, now: datetime | None = None) -> str:
    """Translate a natural Korean mail request into Gmail search syntax.

    Gmail's API does not understand instructions such as ``오늘 메일 요약``.
    Relative dates and common mail states are converted deterministically,
    while explicit Gmail operators supplied by the user are preserved.
    """
    question = _current_question(query)
    lowered = question.lower()
    current = now or datetime.now(_SEOUL)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_SEOUL)
    current = current.astimezone(_SEOUL)
    today = current.date()

    explicit_operators = _GMAIL_OPERATOR_RE.findall(question)
    operators_lower = " ".join(explicit_operators).lower()
    has_date_operator = any(
        f"{name}:" in operators_lower
        for name in ("after", "before", "older", "newer", "newer_than", "older_than")
    )

    # 보낸사람 — 이미 `from:`/`to:` 를 직접 쓴 질문은 건드리지 않는다
    sender = ""
    sender_tokens: set = set()
    if "from:" not in operators_lower:
        found = _SENDER_RE.search(_GMAIL_OPERATOR_RE.sub(" ", question))
        if found:
            raw = found.group(1)
            sender = _strip_honorific(raw)
            # 원문 표기도 함께 기억한다 — 검색어로 다시 새어 나가면 안 된다
            sender_tokens.update({raw.lower(), sender.lower()})

    generated: List[str] = []
    if not has_date_operator:
        start = end = None
        window = _relative_window(lowered)
        if window:
            generated.append(window)
        elif any(word in lowered for word in ("어제", "yesterday")):
            start, end = today - timedelta(days=1), today
        elif any(word in lowered for word in ("오늘", "today")):
            start, end = today, today + timedelta(days=1)
        elif any(word in lowered for word in ("그제", "그저께")):
            start, end = today - timedelta(days=2), today - timedelta(days=1)
        elif any(word in lowered for word in ("지난주", "지난 주", "last week")):
            this_monday = today - timedelta(days=today.weekday())
            start, end = this_monday - timedelta(days=7), this_monday
        elif any(word in lowered for word in ("이번주", "이번 주", "this week")):
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=7)
        elif any(word in lowered for word in ("지난달", "지난 달", "last month")):
            end = today.replace(day=1)
            start = (end - timedelta(days=1)).replace(day=1)
        elif any(word in lowered for word in ("이번달", "이번 달", "this month")):
            start = today.replace(day=1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        elif any(word in lowered for word in ("방금", "just arrived", "just received")):
            generated.append("newer_than:1d")
        # ⚠️ `최신` 이 빠져 있어 "최신메일이 뭐지?" 가 **빈 질의**가 됐다
        #    (2026-09-07 실측). 빈 질의는 아무것도 안 보내므로 0건이다.
        elif any(word in lowered for word in ("최근", "최신", "latest", "recent")):
            generated.append("newer_than:7d")

        if start is not None and end is not None:
            generated.extend([
                f"after:{start:%Y/%m/%d}",
                f"before:{end:%Y/%m/%d}",
            ])

    if any(word in lowered for word in ("읽지 않은", "안 읽은", "미열람", "unread")):
        if "is:" not in operators_lower:
            generated.append("is:unread")
    if any(word in lowered for word in ("첨부파일", "첨부 파일", "attachment")):
        if "has:" not in operators_lower:
            generated.append("has:attachment")

    sent = any(word in lowered for word in ("보낸 메일", "발송한", "발신한", "sent mail"))
    received = any(word in lowered for word in (
        "받은 메일", "수신 메일", "수신한", "수신 받은", "들어온 메일",
        "도착한 메일", "온 메일", "received",
    ))
    # ⛔ **보낸사람이 지목되면 `in:sent` 가 아니다** (2026-09-09 스윕 실측 사고).
    #    "이해인님이 보낸 메일 찾아줘" 가 `in:sent from:이해인님` 이 됐다 —
    #    내 보낸편지함에는 내가 보낸 것만 있으므로 **구조적으로 항상 0건**이고,
    #    그래서 좁혀찾기 사다리가 `from:` 을 떼어 **내가 보낸 메일 12건**을
    #    답으로 내놨다. 넓혔다고 밝히긴 했지만 밝힌 내용이 이미 다른 질문이다
    #    (드라이브 규칙: 잡음은 답처럼 보여서 0건보다 나쁘다).
    #    `내가/제가 보낸` 은 이름이 안 잡히므로 종전대로 `in:sent` 다.
    if sent and not sender and "in:" not in operators_lower:
        generated.append("in:sent")
    elif received and not any(key in operators_lower for key in ("from:", "in:")):
        generated.append("-from:me")

    residual = _GMAIL_OPERATOR_RE.sub(" ", question.lower())
    stop_phrases = (
        "요약해주세요", "정리해주세요", "검색해주세요", "확인해주세요",
        "요약해줘", "정리해줘", "검색해줘", "찾아줘", "보여줘", "알려줘", "확인해줘",
        "무엇인가요", "무엇이야", "이뭐예요", "이뭐에요", "이뭔가요",
        "이뭐야", "이머야", "이뭔데", "이뭐임", "이뭐냐",
        "뭐예요", "뭐에요", "뭔가요", "뭐야", "머야", "뭔데", "뭐임", "뭐냐",
        "읽지 않은", "안 읽은", "첨부 파일", "받은 메일", "보낸 메일", "수신 받은",
        "들어온 메일", "도착한 메일", "수신 메일", "온 메일",
        "지난 주", "이번 주", "지난 달", "이번 달", "last week", "this week",
        "last month", "this month", "sent mail",
        "yesterday", "today", "latest", "recent", "received", "attachment", "unread",
        "그저께", "지난주", "이번주", "지난달", "이번달", "어제", "오늘", "그제", "최근",
        "최신", "방금",
        "발송한", "발신한", "수신한", "미열람", "첨부파일",
        "이메일", "gmail", "메일", "내용", "본문", "요약", "정리", "검색", "확인",
        "내", "나의", "좀", "관련", "대해서", "해줘", "해주세요", "줘", "주세요",
    )
    # ⛔ **부분 문자열로 지우면 오타 꼬리가 검색어로 남는다** (2026-08-14 사용자 제보).
    #    `오늘 메일 요야ㅐㄱ` → 검색어 `요야` → **0건**. 바로 다시 친 `오늘 메일 요약` 은
    #    정상 동작했다. 같은 부류가 여럿이다: `요약해조` → `해조`, `정리해줭` → `해줭`.
    #    Gmail 은 에러를 내지 않으므로 "오늘 메일이 없다" 와 구분이 안 된다 —
    #    드라이브 0건 사고와 같은 종류다.
    #    ⛔ **대응은 불용어를 더 쌓는 것이 아니다.** 오타는 목록으로 끝이 없고,
    #    사용자가 이미 그 방식의 위험을 지적했다. **어절 단위로** 보면 목록을 늘리지
    #    않고 잡힌다 — 불용어를 떼어 낸 자리에 짧은 꼬리만 남으면 그건 낱말이 아니다.
    # ⚠️ 띄어쓰기를 품은 불용어("받은 메일"·"안 읽은")는 **문장 전체에서 먼저** 지운다.
    #    어절 단위로만 보면 걸리지 않아 `받은`·`들어온` 이 검색어로 남는다 (회귀로 확인)
    multi = sorted((p for p in stop_phrases if " " in p), key=len, reverse=True)
    single = sorted((p for p in stop_phrases if " " not in p), key=len, reverse=True)
    for phrase in multi:
        residual = residual.replace(phrase, " ")

    keywords: List[str] = []
    for word in residual.split():
        cleaned = word
        for phrase in single:
            cleaned = cleaned.replace(phrase, " ")
        stripped = bool(cleaned.strip() != word.strip())
        for token in re.sub(r"[^0-9a-zA-Z가-힣@._+-]+", " ", cleaned).split():
            # ⚠️ **조사를 떼고 나서** 불용어를 본다 — 순서를 바꾸면 `이메일이`·`구독을`
            #    이 그대로 남는다. 규칙은 공용 추출기(`query_keywords`)의 것을 쓴다:
            #    메일만 따로 갖고 있다가 문장 전체가 검색어가 됐다 (붐따 #148·#149)
            # ⚠️ 문장부호가 붙은 채로는 불용어에 걸리지 않는다 — `왔어.` 가 그렇게
            #    살아남아 검색어가 됐다. 안쪽 점은 남긴다 (`report.pdf`·`a.com`)
            token = strip_particle(token.strip("._-"))
            low = token.lower()
            if len(token) < 2 or low in sender_tokens:
                continue
            # ⚠️ 이름을 `from:` 으로 뺐으면 **그 이름이 붙은 조각도 검색어가 아니다.**
            #    실측: `이해인님으로부터 온 메일` → `from:이해인 이해인님으로` 가 되어
            #    본문에 그 글자가 없으면 0건이 됐다. `haein 님이` 의 `님이` 도 같다.
            if sender_tokens and (any(low.startswith(s) for s in sender_tokens if s)
                                  or _HONORIFIC_ONLY.match(token)):
                continue
            # ⚠️ **불용어를 뗀 자리에 2글자 이하가 남으면 어미 오타다** (`해조`·`해줭`).
            #    뗀 것이 없는 어절은 그대로 둔다 — `환율`·`면세` 처럼 짧아도 뜻이 있다
            if stripped and len(token) <= 2:
                continue
            if low in _MAIL_STOP or low in BASE_STOP:
                continue
            # "1년"·"3개월" 은 기간이지 내용이 아니다. 연산자로 번역됐거나
            # (`newer_than:`) 번역할 수 없었거나, 어느 쪽이든 본문엔 없다
            # ⛔ `주일`·`주간` 이 빠져 있어 **`1주일` 이 검색어로 남았다**
            #    (2026-09-07 실측). 게다가 숫자가 섞여 `_rank_keywords` 가
            #    고유명사로 보고 **맨 앞에 세워서**, 좁혀 찾기 사다리가
            #    정작 뜻이 있는 `미팅` 을 먼저 떼고 `1주일` 을 끝까지 남겼다.
            #    ⚠️ 긴 것부터 적는다 — `주` 가 먼저 맞으면 `주일` 의 `일` 이 남는다
            if re.fullmatch(r"\d+\s*(년|개월|달|주일|주간|주|일)", token):
                continue
            if low not in {k.lower() for k in keywords}:
                keywords.append(token)

    # 한글 낱자(ㄱ~ㅎ·ㅏ~ㅣ)가 섞인 어절은 **오타가 확실하다.** 완성형이 아니다
    if re.search(r"[ㄱ-ㆎ]", question):
        keywords = [k for k in keywords
                    if not re.search(r"[ㄱ-ㆎ]", _jamo_source(question, k))]

    # ⛔ **문장을 통째로 AND 하지 않는다.** Gmail 은 낱말을 AND 로 묶으므로 낱말이
    #    늘수록 0건에 가까워진다. 신호가 센 것부터 남긴다 — 0건일 때 좁혀 찾기
    #    사다리가 앞에서부터 잘라 쓰기 때문에, 이 순서가 곧 검색 품질이다.
    keywords = _rank_keywords(keywords)[:_MAX_KEYWORDS]

    if sender:
        generated.append(f"from:{sender}")
    return " ".join([*explicit_operators, *generated, *keywords]).strip()


_MAX_KEYWORDS = 4


def _rank_keywords(keywords: List[str]) -> List[str]:
    """고유명사(영문·숫자)를 앞으로, 그다음 긴 낱말 순.

    `Exolyt` 처럼 영문/숫자가 섞인 말은 그 메일에만 있는 이름일 확률이 높다.
    반대로 `구독`·`갱신` 같은 한국어 일반명사는 **영문 메일 본문에 아예 없다** —
    이런 말이 AND 에 남아 있으면 찾을 수 있는 메일도 못 찾는다.
    """
    return sorted(keywords,
                  key=lambda t: (0 if re.search(r"[A-Za-z0-9]", t) else 1, -len(t)))


def _relative_window(lowered: str) -> str | None:
    """"과거 1년 이내"·"최근 3개월" → `newer_than:` (없으면 None).

    ⛔ "약 1년 **전**" 은 기간이 아니다. 1년 안쪽으로 자르면 정작 찾던 그 메일이
       빠진다 — 연산자를 안 붙이고 전체에서 찾는 편이 옳다.
    """
    found = _REL_PERIOD_RE.search(lowered or "")
    if not found:
        return None
    prefix, count, unit, suffix = found.groups()
    if not prefix and not suffix:
        return None            # 맨 숫자 — 기간을 뜻한다고 볼 근거가 없다
    number = int(count)
    if number <= 0:
        return None
    if unit == "주":
        return f"newer_than:{number * 7}d"
    return f"newer_than:{number}{_PERIOD_UNIT[unit]}"


def _jamo_source(question: str, token: str) -> str:
    """`token` 이 나온 원래 어절을 돌려준다 (낱자는 정규식에서 이미 잘려 나간다).

    `요야ㅐㄱ` 은 정리 후 `요야` 로 남아 겉보기엔 멀쩡하다. 원 어절을 봐야
    낱자가 섞였다는 사실이 보인다.
    """
    for word in (question or "").split():
        if token in word:
            return word
    return token


def run_gmail_search(creds, question: str, max_results: int):
    """Gmail 조회 — **0건이면 좁혀 가며 다시 본다.** (메일, 넓혔는지 여부)

    ⛔ Gmail 은 낱말을 **AND** 로 묶는다. 질문이 길수록 0건에 가까워지고, 그 0건은
       "메일이 없다" 와 똑같이 생겼다 (붐따 #148·#149 — 세 번 물어 세 번 다 0건).
       그래서 한 번에 포기하지 않고 **신호가 약한 낱말부터 떼며** 다시 본다:

           from:Christopher newer_than:1y exolyt 구독 갱신   ← 질문 그대로
           from:Christopher newer_than:1y exolyt 구독
           from:Christopher newer_than:1y exolyt            ← 보통 여기서 찾는다
           from:Christopher newer_than:1y                   ← 그 사람 메일 전부
           newer_than:1y exolyt                             ← from 을 잘못 잡았을 때

    ⚠️ 마지막 층(연산자만)은 예전부터 있던 완화다 — 완성형 오타(`요약`→`유약`)로
       검색어 하나가 통째로 어긋난 경우를 위한 것이다.
    ⚠️ 드라이브에서는 같은 완화를 **하지 않았다** — 거기서 넓히면 관련 없는 파일이
       답처럼 보인다. 메일은 다르다: 넓힌 결과가 "그 기간 메일 전부" 라 사용자가
       무엇을 보고 있는지 안다. 대신 **넓혔다는 사실을 반드시 밝힌다.**
    ⛔ 빈 검색어는 보내지 않는다 — 메일함 전체가 답처럼 보인다.
    """
    q = build_gmail_query(question)
    terms = q.split()
    sender = [t for t in terms if t.lower().startswith("from:")]
    hard_ops = [t for t in terms if ":" in t and t not in sender]
    keywords = [t for t in terms if ":" not in t]

    ladder: List[List[str]] = []
    for cut in range(len(keywords), 0, -1):
        ladder.append([*hard_ops, *sender, *keywords[:cut]])
    ladder.append([*hard_ops, *sender])
    if sender:
        # `from:` 은 우리가 문장에서 추측한 것이라 틀릴 수 있다 ("고객사로부터").
        # 마지막에는 그 추측을 걷어내고 가장 센 검색어 하나로 본다
        ladder.append([*hard_ops, *keywords[:1]])
    ladder.append(hard_ops)

    tried: List[str] = []
    for rung in ladder:
        candidate = " ".join(rung).strip()
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        messages = search_gmail(creds, candidate, max_results=max_results)
        if not messages:
            continue
        if candidate == q:
            return messages, ""
        logger.warning("gmail_empty_narrowed", question=(question or "")[:120],
                       gmail_query=q, used=candidate, tried=len(tried))
        return messages, (f"(검색어를 넓혀 찾음: 원래 조건으로는 결과가 없어 "
                          f"'{candidate}' 로 조회했습니다)")

    query_keywords.log_empty("gmail", question, keywords)
    return [], ""


def _with_notices(answer: str, notices: List[str]) -> str:
    """넓혀 찾은 사실을 **답변 맨 앞에 코드가 붙인다.**

    ⛔ 정리 LLM 에게 맡기면 지워진다 — 실제로 지웠다. 넓혀 얻은 결과를 손에 쥔 채
       "검색 결과가 없습니다" 라고 답했고, 사용자는 그걸 세 번 봤다 (붐따 #148).
       프롬프트는 확률이고 보증은 코드다 (FI 마스킹과 같은 사상).
    """
    text = answer or ""
    keep = [n for n in notices if n and n not in text]
    return "\n\n".join([*keep, text]).strip() if keep else text


def gmail_result_limit(query: str) -> int:
    """Return one message when the user explicitly asks for the newest one."""
    normalized = _current_question(query).lower().replace(" ", "")
    newest_markers = (
        "최신메일", "가장최근메일", "마지막메일", "방금온메일",
        "latestmail", "newestmail", "mostrecentmail",
    )
    return 1 if any(marker in normalized for marker in newest_markers) else 10


def _get_auth_manager() -> GoogleAuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = GoogleAuthManager()
    return _auth_manager


def _extract_text(content) -> str:
    """Extract the text block from a LangChain chat model response.

    ``content`` is a plain string for simple replies, but both Gemini and
    Claude can return a list of content blocks instead (e.g. a "thinking"
    block alongside the "text" block) for more complex generations — this
    pulls out just the text.
    """
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


class GWSAgent:
    """Google Workspace agent with per-user OAuth2 authentication."""

    def __init__(self):
        # LangChain(ChatGoogleGenerativeAI + create_react_agent)은 더 이상 쓰지 않는다.
        # 그 래퍼가 **프록시를 통과하지 못해** 호출이 통째로 멈췄다 —
        # 실측: 기본/transport='rest' 모두 45초 무응답, 결국 300초 타임아웃까지 매달렸다.
        # 답변 정리는 앱이 다른 곳에서 쓰는 REST 클라이언트(get_flash_client, 실측 2.4초)로 한다.
        pass

    async def run(self, query: str, user_email: str = "") -> str:
        """Search Google Workspace for relevant info.

        Args:
            query: User question.
            user_email: User's email for OAuth credential lookup.

        Returns:
            Answer text, or auth URL if not authenticated.
        """
        # No user_email → can't authenticate
        if not user_email:
            return (
                "Google Workspace 기능을 사용하려면 로그인이 필요합니다.\n"
                "로그아웃 후 다시 로그인해주세요."
            )

        auth_manager = _get_auth_manager()
        creds = auth_manager.get_credentials(user_email)

        # No valid token → auto-connect prompt with auth URL
        if creds is None:
            return (
                "Google Workspace에 접근하려면 Google 계정 연결이 필요합니다.\n\n"
                "잠시 후 Google 로그인 창이 열립니다. 연결 완료 후 같은 질문을 다시 해주세요.\n\n"
                "<!-- gws-auth:/auth/google/login -->"
            )

        # ── 도구 직접 호출 (ReAct 루프 없음) ──────────────────────────────
        #
        # 원래는 create_react_agent + ChatGoogleGenerativeAI 였는데,
        # 그 LangChain 래퍼가 **프록시를 통과하지 못해** 호출이 통째로 멈췄다.
        # 실측: 기본/transport='rest' 모두 45초 안에 응답 없음 → 300초 타임아웃까지
        # 매달렸고, 사용자에겐 "분석이 오래 걸립니다" 만 보였다 (👎 3건).
        # 반면 앱이 다른 곳에서 쓰는 REST 클라이언트(get_flash_client)는 2.4초에 응답한다.
        #
        # 게다가 시스템 프롬프트가 이미 "도구는 1번만 호출하라"고 요구하고 있었다.
        # 한 번만 부를 거면 ReAct 루프가 필요 없다 — 분류해서 직접 부르고 결과를
        # 정리만 시키는 편이 빠르고 결과도 예측 가능하다.
        tool_type = self._classify_tool(query)
        # A count must use the whole requested period, before any text formatter
        # can mistake the default upcoming-week preview for an annual total.
        from app.core.calendar_stats import answer_statistics, is_statistics_query
        if is_statistics_query(_current_question(query)):
            return await asyncio.to_thread(answer_statistics, creds, _current_question(query))
        results, notices = await asyncio.to_thread(self._collect, creds, query, tool_type)

        if not results.strip():
            return "검색 결과가 없습니다."

        from app.core.llm import get_flash_client

        # 형식 안내는 **실제로 조회한 종류만** 넣는다. 셋을 다 나열하면
        # "메일 및 파일 정보는 포함되어 있지 않아…" 같은 군더더기가 붙는다.
        _fmt = {
            "calendar": "- 날짜별로 묶어 시간·제목·장소를 표로 정리하세요",
            "gmail": "- 제목·보낸사람·날짜·요약을 표로 정리하세요",
            "drive": "- 파일명·유형·수정일·링크를 표로 정리하세요",
        }
        fmt_lines = (
            _fmt.get(tool_type)
            or "- 종류별(일정/메일/파일)로 나눠 표로 정리하세요"
        )
        prompt = (
            "당신은 Craver의 Google Workspace 비서입니다. 아래 **검색 결과만** 사용해 "
            "사용자 질문에 한국어로 답하세요. 결과에 없는 내용을 지어내지 마세요.\n\n"
            f"## 사용자 질문\n{query}\n\n"
            f"## 검색 결과\n{results[:24000]}\n\n"
            "## 형식\n"
            f"{fmt_lines}\n"
            "- 날짜/시간은 한국어로 (예: 2026년 2월 12일 오후 3시)\n"
            "- 질문이 특정 시간대(오전, 11시 등)를 물으면 결과 중 해당 시간대만 골라 답하세요\n"
            "- 결과가 비어 있으면 '검색 결과가 없습니다'라고만 답하세요\n"
            "- **조회하지 않은 종류(메일·파일 등)를 언급하지 마세요.** "
            "사용자가 묻지 않은 것을 '포함되어 있지 않다'고 덧붙이지 말 것"
            # 넓혀 찾았으면 결과가 질문과 딱 맞지 않는다. 그걸 "없음" 으로 뭉개던
            # 것이 붐따 #148 의 세 번째 턴이다 — 있는 것을 보여주고 사실을 밝힌다
            + ("\n- 아래 결과는 **검색 조건을 넓혀** 얻은 것입니다. 질문과 정확히 "
               "일치하지 않더라도 조회된 것을 그대로 정리해 보여주고, 넓혀 찾았다는 "
               "사실을 첫 줄에 적으세요. '검색 결과가 없습니다' 라고 답하지 마세요."
               if notices else "")
        )
        try:
            llm = get_flash_client()
            answer = await asyncio.wait_for(
                asyncio.to_thread(llm.generate, prompt, None, 0.2), timeout=40.0
            )
            return _with_notices(answer or results, notices)
        except asyncio.TimeoutError:
            logger.warning("gws_format_timeout")
            return _with_notices(results, notices)  # 정리에 실패해도 원본은 돌려준다
        except Exception as e:
            logger.error("gws_format_failed", error_type=type(e).__name__)
            return _with_notices(results, notices)

    def _collect(self, creds, query: str, tool_type: str):
        """분류된 도구를 직접 호출해 원본 결과를 모은다 (블로킹 — to_thread 로 부를 것).

        Returns:
            (결과 텍스트, 공지 목록). **공지는 결과와 따로 올린다** — 넓혀 찾은 사실을
            결과 텍스트에만 적으면 정리하는 LLM 이 지운다. 실제로 지웠다 (붐따 #148).
        """
        parts = []
        notices: List[str] = []
        current_query = _current_question(query)

        def _calendar():
            # 시간 표현("오전", "11시")을 검색어로 넣으면 결과가 0건이 된다.
            # 제목 검색이 아니라 기간 조회이므로 query 는 비우고 days_ahead 만 조절한다.
            q = current_query.lower()
            days = 7
            if any(k in q for k in ("오늘", "today")):
                days = 1
            elif any(k in q for k in ("내일", "tomorrow")):
                days = 2
            elif any(k in q for k in ("이번주", "이번 주", "this week")):
                days = 7
            elif any(k in q for k in ("다음주", "다음 주", "next week")):
                days = 14
            elif any(k in q for k in ("이번달", "이번 달", "한달", "this month")):
                days = 31
            try:
                ev = list_calendar_events(creds, query=None, days_ahead=days)
            except Exception as e:
                return f"[캘린더 오류] {str(e)[:200]}"
            if not ev:
                return "[캘린더] 일정이 없습니다."
            lines = ["[캘린더]"]
            for e in ev:
                loc = f" (장소: {e['location']})" if e.get("location") else ""
                lines.append(f"- {e['summary']}: {e['start']} ~ {e['end']}{loc}")
            return "\n".join(lines)

        def _gmail():
            try:
                ms, widened = run_gmail_search(
                    creds, current_query, gmail_result_limit(current_query))
            except Exception as e:
                return f"[메일 오류] {str(e)[:200]}"
            if not ms:
                return "[메일] 검색 결과가 없습니다."
            if widened:
                notices.append(widened)
            lines = [f"[메일] {widened}".rstrip()]
            for m in ms:
                content = m.get("body") or m.get("snippet", "")
                lines.append(
                    f"- {m['subject']} (보낸사람: {m['from']}, 날짜: {m['date']})"
                    f"\n  {content}"
                )
            return "\n".join(lines)

        def _drive():
            # 캘린더와 같은 원리 — 사용자 문장 전체를 검색어로 넣으면 항상 0건이다.
            # ① "사진/영상/PDF" 같은 유형 표현 → mimeType 필터로 변환
            # ② 조사·명령어를 걷어낸 핵심 키워드만 name/fullText 검색에 사용
            #    (키워드가 안 남으면 유형 필터만으로 최근 파일을 보여준다)
            q = current_query.lower()
            mime = None
            for kws, m in (
                (("사진", "이미지", "image", "photo", "jpg", "png"), "image/"),
                (("영상", "동영상", "video", "mp4"), "video/"),
                (("pdf",), "application/pdf"),
                (("스프레드시트", "시트", "spreadsheet", "엑셀"), "spreadsheet"),
                (("슬라이드", "ppt", "발표자료"), "presentation"),
            ):
                if any(k in q for k in kws):
                    mime = m
                    break
            # 검색어 추출은 `app/core/query_keywords.py` 한 곳이다 (위키 검색도 같은 것을 쓴다).
            # ⛔ 예전엔 여기서 자체 불용어로 걸렀고 조사를 떼지 않아 `구글드라이브에서`·
            #    `내가` 가 통째로 남았다 — 질문 문장이 검색어가 돼 **항상 0건**이었다
            from app.core.query_keywords import extract as _extract_kw
            _DRIVE_STOP = {
                "드라이브", "구글드라이브", "구글", "drive", "google",
                "사진", "이미지", "image", "photo", "영상", "동영상", "video", "pdf",
                "스프레드시트", "시트", "엑셀", "슬라이드", "ppt", "발표자료",
                "최근", "최근에", "올린", "저장한", "저장", "업로드", "공유", "받은",
                "작성한", "작성", "만든", "들어간", "담긴",
            }
            kw = " ".join(_extract_kw(q, extra_stop=_DRIVE_STOP))
            widened = ""
            try:
                fs = search_drive(creds, kw, max_results=10, mime_contains=mime)
                # 0건이면 **같은 뜻의 다른 말**로 다시 찾는다. Drive API 는 낱말이 실제로
                # 들어 있어야 찾으므로, 부르는 이름과 파일에 적힌 이름이 다르면 못 찾는다
                # (제미나이는 의미로 찾아서 됐다 — 2026-08-14). 색인을 만들지 않고
                # **검색 시점에** 넓히는 방식이라 권한·신선도 문제가 없다.
                if not fs and kw:
                    from app.core.query_keywords import expand, llm_variants
                    # ① 확실한 표기 변형·사내 사전 → ② 그래도 없으면 LLM 이 대안을 낸다.
                    #    ⛔ 동의어를 손으로 쌓지 않는 이유는 `query_keywords` 주석 참조
                    for alt in expand(kw.split()) + llm_variants(current_query, kw.split()):
                        fs = search_drive(creds, " ".join(alt), max_results=10,
                                          mime_contains=mime)
                        if fs:
                            widened = " ".join(alt)
                            break
                # 키워드+유형 동시 검색이 0건이면 유형만으로 완화 재시도
                if not fs and kw and mime:
                    fs = search_drive(creds, "", max_results=10, mime_contains=mime)
                    if fs:
                        widened = "(유형만)"
            except Exception as e:
                return f"[드라이브 오류] {str(e)[:200]}"
            if not fs:
                # ⛔ "결과 없음" 은 **정말 없을 때와 검색어가 망가졌을 때가 똑같이 생겼다.**
                #    검색어를 남겨야 나중에 구분할 수 있다 (2026-08-14 사고의 교훈)
                from app.core.query_keywords import log_empty
                log_empty("drive", current_query, kw.split(), mime=mime or "")
                return "[드라이브] 검색 결과가 없습니다."
            # ⚠️ 넓혀서 찾았으면 **그 사실을 밝힌다.** 안 밝히면 근사치가 정답처럼 보인다
            _note = (f", 원래 검색어로는 결과가 없어 '{widened}' 로 넓혀 찾음"
                     if widened else "")
            lines = [f"[드라이브] (검색어: {kw or '전체'}"
                     f"{', 유형: ' + mime if mime else ''}{_note})"]
            for f in fs:
                lines.append(f"- {f['name']} ({f['mimeType']}, 수정: {f['modifiedTime']})\n  {f['webViewLink']}")
            return "\n".join(lines)

        picked = {"calendar": [_calendar], "gmail": [_gmail], "drive": [_drive]}.get(
            tool_type, [_calendar, _gmail, _drive]
        )
        for fn in picked:
            try:
                parts.append(fn())
            except Exception as e:
                parts.append(f"[{fn.__name__} 실패] {str(e)[:150]}")
        return "\n\n".join(parts), notices

    @staticmethod
    def _classify_tool(query: str) -> str:
        """Pre-classify query to select the appropriate GWS tool.

        Returns: "calendar", "gmail", "drive", or "all".
        """
        # 공백 차이로 분류가 빗나가지 않게 정규화한다.
        # ("이번 주 일정" 이 cal 키워드 "이번주" 에 안 걸려 all 로 빠지던 문제)
        q = _current_question(query).lower().replace(" ", "")
        cal_explicit_kw = ["캘린더", "calendar", "일정", "schedule", "스케줄",
                           "회의", "미팅", "약속"]
        cal_time_kw = ["내일", "오늘", "이번주", "다음주", "모레", "일주일",
                       "이번달일정", "며칠"]
        mail_kw = ["메일", "mail", "gmail", "편지", "이메일", "받은",
                   "보낸", "inbox", "발송", "수신", "발신", "invoice",
                   "shipping", "메시지"]
        drive_kw = ["드라이브", "drive", "파일", "file", "폴더", "문서",
                    "시트", "sheet", "용량"]

        cal_explicit = any(k in q for k in cal_explicit_kw)
        cal = cal_explicit or any(k in q for k in cal_time_kw)
        mail = any(k in q for k in mail_kw)
        drive = any(k in q for k in drive_kw)

        # "오늘"은 메일의 기간 조건이지 캘린더 요청이 아니다. 반면
        # "오늘 메일과 일정"처럼 일정 자체를 명시하면 여러 도구를 조회한다.
        if mail and not drive and not cal_explicit:
            return "gmail"
        if drive and not mail and not cal_explicit:
            return "drive"

        # Single tool detected
        if cal and not mail and not drive:
            return "calendar"
        if mail and not cal and not drive:
            return "gmail"
        if drive and not cal and not mail:
            return "drive"
        return "all"

    def _build_tools(self, creds) -> List:
        """Build LangChain tools with user credentials bound.

        Args:
            creds: Valid Google OAuth2 Credentials.

        Returns:
            List of LangChain tools.
        """

        @tool
        def gmail_search(query: str) -> str:
            """Gmail에서 메일을 검색합니다. query에 검색어를 입력하세요. 예: 'from:boss', 'subject:보고서', '최근 메일'"""
            try:
                results, widened = run_gmail_search(
                    creds, query, gmail_result_limit(query))
                if not results:
                    return "검색 결과가 없습니다."
                lines = [widened] if widened else []
                for m in results:
                    content = m.get("body") or m.get("snippet", "")
                    lines.append(f"- **{m['subject']}** (보낸사람: {m['from']}, 날짜: {m['date']})\n  {content}")
                return "\n".join(lines)
            except Exception as e:
                return f"Gmail 검색 오류: {str(e)}"

        @tool
        def drive_search(query: str) -> str:
            """Google Drive에서 파일을 검색합니다. query에 검색어를 입력하세요. 예: '보고서', '회의록'"""
            try:
                results = search_drive(creds, query, max_results=10)
                if not results:
                    return "검색 결과가 없습니다."
                lines = []
                for f in results:
                    lines.append(f"- **{f['name']}** ({f['mimeType']}, 수정: {f['modifiedTime']})\n  {f['webViewLink']}")
                return "\n".join(lines)
            except Exception as e:
                return f"Drive 검색 오류: {str(e)}"

        @tool
        def calendar_search(query: str = "", days_ahead: int = 7) -> str:
            """Google Calendar 일정을 조회합니다.

            query: 이벤트 제목/설명에서 텍스트를 검색합니다. 시간 필터링이 아닙니다!
                   "오전", "11시", "오후 3시" 같은 시간 표현을 query에 넣지 마세요 — 결과가 없습니다.
                   시간대별 일정을 찾으려면 query를 비우고("") 전체 일정을 가져온 뒤 시간으로 필터링하세요.
                   제목 검색 예시: query="틱톡", query="타운홀"
            days_ahead: 며칠 후까지 조회할지 (기본 7일)
            """
            try:
                results = list_calendar_events(creds, query=query or None, days_ahead=days_ahead)
                if not results:
                    return "일정이 없습니다."
                lines = []
                for e in results:
                    loc = f" (장소: {e['location']})" if e['location'] else ""
                    lines.append(f"- **{e['summary']}**: {e['start']} ~ {e['end']}{loc}")
                return "\n".join(lines)
            except Exception as e:
                return f"Calendar 조회 오류: {str(e)}"

        return [gmail_search, drive_search, calendar_search]
