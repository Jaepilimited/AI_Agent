# 라우터 1단계 "소스가 필요한가?" 판정 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사내 자료가 필요 없는 질문에 사내 자료를 뒤지지 않게 하고, 사용자가
직전 답변을 부정하면 경로를 다시 잡는다.

**Architecture:** 판정 로직은 `app/core/route_intent.py` 순수 함수로 두고,
`orchestrator.py` 는 그것을 호출만 한다. 기존 키워드 관문은 **하나도 걷어내지
않는다** — 확신이 없을 때만 도는 층을 하나 더 얹는다.

**Tech Stack:** Python 3.11 · FastAPI · pytest · Gemini Flash(`get_flash_client`)

**Spec:** `docs/superpowers/specs/2026-09-07-router-source-gate-design.md`

> ⚠️ **Task 3 은 취소되었다** (착수 전 실측). 실행 순서는
> **T1 → T2 → T4 → T5 → T6 → T7** 이다.

## Global Constraints

- **스트리밍·비스트리밍 두 경로에 함께 걸어야 한다.** `route_and_execute` 와
  `route_and_stream` 에 각각 라우팅이 있다. 한쪽만 고치면 경로에 따라 답이 갈린다.
- **사내 데이터 우선.** 판정이 애매하거나 실패하면 `NEED`(뒤진다) 로 떨어진다.
- **되묻기는 `bare` 에서만.** SELF 판정 전반에 걸지 않는다.
- 새 판정 함수는 **LLM 을 부르지 않는다** (2단계 게이트 호출부 제외).
- 테스트 실행: `python -m pytest <path> -q` (⚠️ 이 저장소에 `pytest --timeout` 은 없다).
- 커밋은 **경로를 지목해서** 한다 (`git add <경로>`). ⛔ `git add -A`·`-u`·`.` 금지 —
  다른 세션이 같은 작업트리를 쓴다.
- 전체 테스트는 `python -m pytest tests/ -q --ignore=tests/frontend`.

---

### Task 1: 부정 발화 판정 (`rejection_kind`)

사용자가 직전 답변을 부정했는지, 부정했다면 **고쳐 말한 정보가 있는지** 가른다.
실사용 60일 1,393건 실측: 첫머리 부정 20건 · 그중 정보 없음 2건 · 문장 중간
수정어 26건(건드리면 안 됨).

**Files:**
- Create: `app/core/route_intent.py`
- Test: `tests/test_route_intent.py`

**Interfaces:**
- Consumes: 없음 (순수 함수)
- Produces: `rejection_kind(text: str) -> str` — `"none"` | `"informative"` | `"bare"`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_route_intent.py`:

```python
# -*- coding: utf-8 -*-
"""직전 답변을 부정하는 발화 판정 — 실사용 60일 표본으로 고정.

⛔ 문장 **중간**의 `말고` 는 부정이 아니다. 실측 26건이 전부 정상적인 좁혀
   묻기였다("상위 10개 말고 전체로 줘"). 여기 걸리면 멀쩡한 대화가 끊긴다.
"""

import pytest

from app.core.route_intent import rejection_kind


# 실사용에서 그대로 가져온 것들 (2026-09-07, 첫머리 부정 20건 중)
INFORMATIVE = [
    "아니 ;; 내 개인 업무 노션 ;;",
    "아니 ㅠㅠㅠㅠㅠㅠ!!!!! 나 개인 업무 노션 페이지 구성할건데 제안해달라고!!!!",
    "아니 톤브라이트닝 미스트 월별 매출액 및 판매량",
    "아니 라인이 랩인네이처인 제품들 제품별 성장성 매트릭스 보여줘",
    "아니 ㅡㅡ 주간보고를 작성해달라고",
    "아니 너가 되게 해보라고 !!!!!!!!!!",
    "뭔 소리야 틀렸음; 환각을 정답처럼 말하지 마셈",
    "뭔 소리하는거야. 리센느 멤버 5명을 내부 데이터베이스에서 가져왔다고?",
]

BARE = [
    "아니...",
    "아니 ;;",
]

# 부정이 아니다 — 정상적인 좁혀 묻기
NONE = [
    "상위 10개 말고 25년도 출시된 품목 전체로 줘",
    "막대표 말고 각각 국가별 비중값 볼 수 있게 원형 그래프로 줄 수 있어?",
    "오 좋아 근데 분리만 다시해보자. 앰플, 크림, 토너 각각 따로",
    "기타국가는 뭐야? 누적 매출 비중이 가장 큰 개별 국가부터 보여주고",
    "2026년 8월 일본 매출 알려줘",
    "센텔라 앰플 특징 알려줘",
    "",
]


@pytest.mark.parametrize("text", INFORMATIVE)
def test_부정하면서_고쳐_말한_것은_informative(text):
    assert rejection_kind(text) == "informative", text


@pytest.mark.parametrize("text", BARE)
def test_부정만_하고_정보가_없으면_bare(text):
    assert rejection_kind(text) == "bare", text


@pytest.mark.parametrize("text", NONE)
def test_좁혀_묻기는_부정이_아니다(text):
    assert rejection_kind(text) == "none", text
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_route_intent.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.route_intent'`

- [ ] **Step 3: 최소 구현**

`app/core/route_intent.py`:

```python
# -*- coding: utf-8 -*-
"""라우터 1단계 판정 — **순수 함수만** 둔다.

⛔ `orchestrator.py` 는 이미 4,282줄이다. 판정 규칙을 거기 더 쌓지 않는다
   (`textmatch.py`·`query_keywords.py` 와 같은 방식).

설계: `docs/superpowers/specs/2026-09-07-router-source-gate-design.md`
"""

from __future__ import annotations

import re

# 발화 **첫머리**의 부정. 실사용 60일 1,393건에서 20건이 걸렸고 전부 진짜
# 실패였다 (그중 붐따가 눌린 것은 0건 — 붐따와 겹치지 않는 신호다).
# ⛔ `말고` 를 여기 넣지 마라. 문장 중간의 `말고` 26건은 전부 정상적인 좁혀
#    묻기였다 ("상위 10개 말고 전체로 줘"). 걸면 멀쩡한 대화가 끊긴다.
_HEAD_REJECT = re.compile(
    r"^\s*(아니요|아니오|아뇨|아니|그게\s*아니|이게\s*아니|"
    r"틀렸|잘못|뭔\s*소리|엉뚱)", re.IGNORECASE)

# 부정을 걷어낸 뒤 남는 글자를 셀 때 지우는 것 — 공백·문장부호·감정 낱자
_FILLER = re.compile(r"[\s.!?~,;:·…\-()ㅠㅜㅋㅎ]+")

# 이보다 적게 남으면 「고쳐 말한 정보가 없다」로 본다
_BARE_MAX_CHARS = 4


def rejection_kind(text: str) -> str:
    """직전 답변을 부정하는 발화인가, 부정이라면 정보가 있는가.

    Returns:
        ``"none"``        — 부정이 아니다 (정상 라우팅)
        ``"informative"`` — 부정 + 고쳐 말한 내용이 있다 → 그 문장으로 다시 분류
        ``"bare"``        — 부정만 있다 → 되묻는다
    """
    body = " ".join((text or "").split())
    match = _HEAD_REJECT.match(body)
    if not match:
        return "none"
    rest = body[match.end():]
    return "bare" if len(_FILLER.sub("", rest)) <= _BARE_MAX_CHARS else "informative"
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_route_intent.py -q`
Expected: PASS (17 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/core/route_intent.py tests/test_route_intent.py
git commit -m "feat(routing): 직전 답변을 부정하는 발화를 판정한다"
```

---

### Task 2: 「뒤져야 하나」 게이트 프롬프트와 파서

2단계가 LLM 에 물을 문안과, 그 응답을 읽는 파서. 실측으로 검증된 문안이므로
**문구를 바꾸지 말 것** (14/16, 6지선다는 같은 문항에서 #164 를 틀렸다).

**Files:**
- Modify: `app/core/route_intent.py`
- Test: `tests/test_route_intent.py`

**Interfaces:**
- Consumes: 없음
- Produces: `SOURCE_GATE_PROMPT: str` · `parse_gate(raw: str) -> str` (`"NEED"` | `"SELF"`)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_route_intent.py` 끝에 이어 붙인다:

```python
from app.core.route_intent import SOURCE_GATE_PROMPT, parse_gate


@pytest.mark.parametrize("raw,want", [
    ("SELF", "SELF"),
    ("self", "SELF"),
    ("  SELF  ", "SELF"),
    ("SELF — 만들어 달라는 요청입니다", "SELF"),
    ("NEED", "NEED"),
    ("need", "NEED"),
])
def test_판정_문자열을_읽는다(raw, want):
    assert parse_gate(raw) == want


@pytest.mark.parametrize("raw", ["", None, "   ", "글쎄요", "NEED 또는 SELF"])
def test_모르면_뒤지는_쪽이다(raw):
    """⛔ 사내 데이터 우선 — 읽을 수 없으면 NEED 다. 안전한 쪽 실패."""
    assert parse_gate(raw) == "NEED"


def test_프롬프트가_사내데이터_우선을_못박는다():
    """이 줄이 「애매하면 뒤진다」를 보증하는 유일한 자리다."""
    assert "애매하면 NEED" in SOURCE_GATE_PROMPT
    assert "SELF" in SOURCE_GATE_PROMPT and "NEED" in SOURCE_GATE_PROMPT
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_route_intent.py -q`
Expected: FAIL — `ImportError: cannot import name 'SOURCE_GATE_PROMPT'`

- [ ] **Step 3: 최소 구현**

`app/core/route_intent.py` 끝에 이어 붙인다:

```python
# ⛔ 이 문안은 **실측으로 고른 것**이다 (2026-09-07, 16문항 14/16).
#    같은 모델에게 "어느 소스냐"(6지선다)를 물으면 `노션` 낱말에 끌려
#    "Today 를 노션에 자동화할 수 없나?" 를 notion 으로 보낸다(붐따 #164).
#    예/아니오로 바꾸면 그 전제가 사라진다. **문구를 임의로 줄이지 마라.**
SOURCE_GATE_PROMPT = """당신은 사내 AI 어시스턴트의 1단계 판정기입니다.
질문 하나를 보고 **사내 자료(매출 데이터·사내 문서·제품 Q&A·개인 메일/일정)를
실제로 뒤져야 답할 수 있는지**만 판정하세요.

NEED = 사내 자료를 뒤져야 답할 수 있다 (수치·기록·사내 규정·내 메일 등)
SELF = 뒤질 필요가 없다. 다음 중 하나다:
       - 만들어 달라는 요청 (초안·구성안·아이디어·번역·코드·문장 다듬기)
       - 이 어시스턴트 자신의 기능·사용법·가능 여부를 묻는 질문
       - 용어의 뜻, 일반 상식, 잡담

주의 1. 질문에 사내 도구 이름(노션·잔디·틱톡샵)이 들어 있다는 이유만으로 NEED 로
        판정하지 마세요. **그 도구에 대해 묻는 것**과 **그 도구의 자료를 찾는 것**은
        다릅니다.
주의 2. 애매하면 NEED 를 고르세요. 뒤져 보고 없다고 말하는 편이, 뒤지지 않고
        지어내는 것보다 낫습니다.

NEED 또는 SELF 한 단어만 출력하세요."""


def parse_gate(raw) -> str:
    """게이트 응답을 읽는다. ⛔ 읽을 수 없으면 `NEED` — 안전한 쪽으로 실패한다.

    ⚠️ 한 단어만 요구했어도 모델이 문장을 붙일 수 있다. 둘 다 들어 있거나
       어느 쪽도 없으면 뒤지는 쪽을 고른다.
    """
    up = str(raw or "").upper()
    has_self, has_need = "SELF" in up, "NEED" in up
    return "SELF" if (has_self and not has_need) else "NEED"
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_route_intent.py -q`
Expected: PASS (29 passed — Task 1 의 17 + 12)

- [ ] **Step 5: 커밋**

```bash
git add app/core/route_intent.py tests/test_route_intent.py
git commit -m "feat(routing): 「사내 자료를 뒤져야 하나」 게이트 문안과 파서"
```

---

### Task 3: ~~부정하면 경로 상속을 끊는다~~ — **취소됨 (YAGNI)**

> ⛔ **이 태스크는 실행하지 않는다.** 착수 전 실측(2026-09-07): 실사용 60일에서
> 뽑은 첫머리 부정 **15건 중 상속이 실제로 걸리는 것이 0건**이다. 그 발화들은
> `_is_followup_utterance()` 가 False 라 애초에 상속되지 않는다.
>
> 스펙 §3.3 의 인과 서술(「김미이 님이 세 번 연속 받은 이유는 상속」)이 틀렸고
> 스펙을 정정했다. 아래 테스트도 둘 다 결함이었다 — 하나는 이미 통과해 아무것도
> 증명하지 않고, 하나는 거짓을 단정한다(`"막대표 말고 원형 그래프로"` 는 실제로
> 상속되지 않는데 `notion` 을 기대).
>
> `rejection_kind()`(Task 1)는 **Task 4 가 쓰므로 그대로 만든다.**
> 아래 원문은 기록으로 남긴다.

<details><summary>취소된 원안</summary>

김미이 님이 notion 을 세 번 연속 받은 원인. `_inherit_route_for_followup()` 이
후속 발화에 직전 경로를 그대로 물려주는데, **부정하는 발화에도** 물려준다.

⚠️ 이 함수는 스트리밍·비스트리밍 **두 곳에서 모두** 호출된다. 함수 안에서 막으면
   한 번의 수정으로 두 경로가 함께 고쳐진다.

**Files:**
- Modify: `app/agents/orchestrator.py` (`_inherit_route_for_followup`, 약 L530)
- Test: `tests/test_route_intent.py`

**Interfaces:**
- Consumes: `route_intent.rejection_kind` (Task 1)
- Produces: 없음 (동작 변경)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_route_intent.py` 끝에 이어 붙인다:

```python
from app.agents.orchestrator import _inherit_route_for_followup

# 직전 턴이 notion 이었다는 맥락 (`_previous_route` 가 마지막 "AI:" 블록을 본다)
CTX_NOTION = "사용자: 우리팀 노션\nAI: [Notion 사내 문서 검색] 관련 문서입니다."


def test_부정하면_직전_경로를_물려받지_않는다():
    """김미이 님이 notion 을 세 번 연속 받은 원인 (붐따 #163)."""
    assert _inherit_route_for_followup("아니 ;; 내 개인 업무 노션 ;;",
                                       CTX_NOTION) is None


def test_부정이_아닌_후속_발화는_종전대로_물려받는다():
    """상속 자체를 없애면 안 된다 — 축만 바꾸는 발화는 이어져야 한다."""
    assert _inherit_route_for_followup("센텔라 앰플은?", CTX_NOTION) == "notion"


def test_좁혀_묻기는_상속을_끊지_않는다():
    """`말고` 가 중간에 있는 것은 부정이 아니다."""
    assert _inherit_route_for_followup("막대표 말고 원형 그래프로",
                                       CTX_NOTION) == "notion"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_route_intent.py -k 부정 -q`
Expected: FAIL — `test_부정하면_직전_경로를_물려받지_않는다` 가 `'notion' is not None`

- [ ] **Step 3: 최소 구현**

`app/agents/orchestrator.py` 의 `_inherit_route_for_followup()` 본문 **맨 앞**에
넣는다 (`viz_only = ...` 줄 바로 위):

```python
    # ⛔ **부정하는 발화에는 물려주지 않는다.** 직전 답변이 틀렸다고 말하는
    #    사람에게 같은 경로로 또 답하면 같은 실패가 반복된다 — 실제로 그랬다:
    #      "셀라야 … 개인노션 만들고야"      → notion
    #      "아니 ;; 내 개인 업무 노션 ;;"   → notion 상속  ← 부정했는데 그대로
    #      "아니 ㅠㅠ … 제안해달라고!!!"     → notion 상속  ← 또
    #    (붐따 #163, 2026-09-04. 세 번째에 "너 진자 붐따임" 을 들었다)
    # ⚠️ 문장 **중간**의 `말고` 는 부정이 아니다 — "상위 10개 말고 전체로" 는
    #    정상적인 좁혀 묻기다 (실측 26건). `rejection_kind` 가 첫머리만 본다.
    from app.core.route_intent import rejection_kind
    if rejection_kind(query) != "none":
        logger.info("route_inherit_broken_by_rejection", query=(query or "")[:60])
        return None

```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_route_intent.py tests/test_followup_route_inheritance.py -q`
Expected: PASS (기존 상속 테스트도 함께 통과해야 한다)

- [ ] **Step 5: 전체 테스트**

Run: `python -m pytest tests/ -q --ignore=tests/frontend`
Expected: PASS (2,362 + 신규)

- [ ] **Step 6: 커밋**

```bash
git add app/agents/orchestrator.py tests/test_route_intent.py
git commit -m "fix(routing): 직전 답변을 부정하면 경로 상속을 끊는다 — 붐따 #163"
```

</details>

---

### Task 4: 부정인데 정보가 없으면 되묻는다

실측 **2건 / 1,393건 (0.14%)**. ⛔ SELF 판정 전반에 걸지 않는다 — "파이썬 코드
짜줘" 에 되물으면 그 자체가 불편이다.

**Files:**
- Modify: `app/core/route_intent.py` (`clarify_message`)
- Modify: `app/agents/orchestrator.py` — 비스트리밍 약 L1163 · 스트리밍 약 L1437
  (둘 다 `answer_team_country_scope` fast path 바로 뒤)
- Test: `tests/test_route_intent.py`

**Interfaces:**
- Consumes: `rejection_kind` (Task 1)
- Produces: `clarify_message(prev_route: str) -> str` — 빈 문자열이면 되묻지 않는다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
from app.core.route_intent import clarify_message


def test_되묻는_문장은_직전에_무엇을_했는지_밝힌다():
    """무엇을 고쳐 말해야 할지 알려면 직전 경로를 알아야 한다."""
    msg = clarify_message("notion")
    assert "사내 문서" in msg
    assert "?" in msg or "까요" in msg


def test_직전_경로를_모르면_되묻지_않는다():
    """고칠 대상이 없다 — 그냥 정상 라우팅한다."""
    assert clarify_message("") == ""
    assert clarify_message(None) == ""


@pytest.mark.parametrize("route,label", [
    ("bigquery", "사내 데이터"),
    ("notion", "사내 문서"),
    ("cs", "제품 Q&A"),
    ("gws", "메일"),
])
def test_경로마다_사람이_읽는_이름이_있다(route, label):
    assert label in clarify_message(route)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_route_intent.py -k clarify -q`
Expected: FAIL — `ImportError: cannot import name 'clarify_message'`

- [ ] **Step 3: 최소 구현**

`app/core/route_intent.py` 끝에 이어 붙인다:

```python
# 사람이 읽는 경로 이름. ⛔ 내부 경로명("bigquery")을 그대로 보여주지 않는다
_ROUTE_LABEL = {
    "bigquery": "사내 데이터",
    "notion": "사내 문서",
    "cs": "제품 Q&A",
    "gws": "내 메일·드라이브·캘린더",
    "multi": "복합 분석",
    "direct": "일반 답변",
}


def clarify_message(prev_route) -> str:
    """부정만 하고 정보가 없을 때 되묻는 **고정 문구**.

    ⛔ LLM 을 부르지 않는다 (보증은 코드다).
    ⛔ 직전에 무엇을 했는지 **밝힌 뒤** 묻는다. 그냥 "무엇을 원하세요?" 라고만
       하면 사용자는 무엇을 고쳐 말해야 할지 모른다.
    """
    label = _ROUTE_LABEL.get(str(prev_route or ""), "")
    if not label:
        return ""
    return (f"방금은 **{label}** 에서 찾아 답했습니다. "
            f"어떤 걸 원하셨는지 한 줄만 더 알려주시겠어요?")
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_route_intent.py -q`
Expected: PASS

- [ ] **Step 5: 비스트리밍 경로에 배선**

`app/agents/orchestrator.py`, `answer_team_country_scope` fast path 바로 뒤
(약 L1167, `return {"source": "direct", "answer": _org_answer}` 다음 줄)에 넣는다:

```python
            # 부정만 하고 정보가 없으면 되묻는다 (실측 1,393건 중 2건).
            # ⛔ SELF 판정 전반에 걸지 마라 — "파이썬 코드 짜줘" 에 되물으면
            #    그 자체가 불편이다. `bare` 에서만 뜬다.
            from app.core.route_intent import clarify_message, rejection_kind
            if rejection_kind(query) == "bare":
                _ask = clarify_message(_previous_route(conversation_context))
                if _ask:
                    logger.info("clarify_asked_bare_rejection", path="route_and_execute")
                    return {"source": "direct", "answer": _ask}
```

- [ ] **Step 6: 스트리밍 경로에 배선**

같은 파일, `_org_answer` 스트리밍 fast path 바로 뒤 (약 L1437, `return` 다음 줄):

```python
            # ⚠️ 비스트리밍과 **같은 관문** — 한쪽만 달면 경로에 따라 답이 갈린다.
            from app.core.route_intent import clarify_message, rejection_kind
            if rejection_kind(query) == "bare":
                _ask = clarify_message(_previous_route(conversation_context))
                if _ask:
                    logger.info("clarify_asked_bare_rejection", path="route_and_stream")
                    yield ("source", "direct")
                    yield ("done", _ask)
                    return
```

- [ ] **Step 7: 배선 회귀를 쓴다**

```python
def test_되묻기가_두_경로에_모두_걸려_있다():
    """⛔ 한쪽만 달면 스트리밍이냐에 따라 답이 갈린다 (이 저장소의 단골 사고)."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert src.count("clarify_asked_bare_rejection") == 2, "두 경로에 걸려야 한다"
    assert 'path="route_and_execute"' in src
    assert 'path="route_and_stream"' in src
```

- [ ] **Step 8: 전체 테스트**

Run: `python -m pytest tests/ -q --ignore=tests/frontend`
Expected: PASS

- [ ] **Step 9: 커밋**

```bash
git add app/core/route_intent.py app/agents/orchestrator.py tests/test_route_intent.py
git commit -m "feat(routing): 부정만 하고 정보가 없으면 되묻는다"
```

---

### Task 5: 2단계 게이트 배선 — 확신이 없으면 「뒤져야 하나」를 먼저 묻는다

지금은 확신이 없으면 곧바로 "어느 소스냐"(6지선다) 를 묻는데, 그 질문 자체가
"소스 중 하나여야 한다" 를 전제한다. 예/아니오를 먼저 끼운다.

**Files:**
- Modify: `app/agents/orchestrator.py` — `_needs_source()` 신설 + 비스트리밍
  약 L1307 · 스트리밍 약 L1676 두 곳
- Test: `tests/test_route_source_gate.py` (신규)

**Interfaces:**
- Consumes: `route_intent.SOURCE_GATE_PROMPT`, `parse_gate` (Task 2)
- Produces: `OrchestratorAgent._needs_source(query, flash) -> bool`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_route_source_gate.py`:

```python
# -*- coding: utf-8 -*-
"""2단계 게이트 배선 — LLM 을 **언제 부르는지**가 핵심이다."""

import asyncio

import pytest

from app.agents.orchestrator import OrchestratorAgent


class FakeFlash:
    def __init__(self, answer="SELF"):
        self.answer, self.calls = answer, 0

    def generate(self, prompt, temperature=0.0):
        self.calls += 1
        self.prompt = prompt
        return self.answer


@pytest.fixture
def agent():
    return OrchestratorAgent.__new__(OrchestratorAgent)


def test_게이트가_SELF_면_소스를_쓰지_않는다(agent):
    flash = FakeFlash("SELF")
    got = asyncio.run(agent._needs_source("파이썬으로 csv 읽는 코드 짜줘", flash))
    assert got is False
    assert flash.calls == 1


def test_게이트가_NEED_면_소스를_쓴다(agent):
    flash = FakeFlash("NEED")
    assert asyncio.run(agent._needs_source("2026년 8월 매출 알려줘", flash)) is True


def test_게이트가_터지면_뒤지는_쪽이다(agent):
    """⛔ 사내 데이터 우선 — 실패는 안전한 쪽으로 떨어진다."""
    class Boom:
        def generate(self, *a, **k):
            raise RuntimeError("timeout")

    assert asyncio.run(agent._needs_source("아무 질문", Boom())) is True


def test_질문이_프롬프트에_실린다(agent):
    flash = FakeFlash("SELF")
    asyncio.run(agent._needs_source("주간 회의록 템플릿 좀 짜줘", flash))
    assert "주간 회의록 템플릿 좀 짜줘" in flash.prompt
    assert "애매하면 NEED" in flash.prompt


def test_게이트가_두_경로에_모두_걸려_있다():
    """⛔ 한쪽만 달면 스트리밍이냐에 따라 답이 갈린다."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert src.count("_needs_source(") >= 3   # 정의 1 + 호출 2
    assert src.count("source_gate_self") == 2
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_route_source_gate.py -q`
Expected: FAIL — `AttributeError: '_needs_source'`

- [ ] **Step 3: 판정 메서드를 만든다**

`app/agents/orchestrator.py` 의 `_classify_with_llm` **바로 앞**에 넣는다:

```python
    async def _needs_source(self, query: str, llm) -> bool:
        """이 질문은 사내 자료를 뒤져야 답할 수 있는가.

        ⛔ "어느 소스냐"(6지선다)를 묻기 **전에** 이것을 먼저 묻는다. 6지선다는
           "소스 중 하나여야 한다"를 전제하고 있어서, 소스가 필요 없는 질문에
           답이 없다 — `노션` 낱말 하나에 끌려 문서 검색으로 갔다 (붐따 #164).
           실측(2026-09-07, 16문항): 6지선다는 #164 오답, 예/아니오는 14/16.
        ⛔ 실패하면 **True(뒤진다)** 다. 사내 데이터 우선 — 뒤져 보고 없다고
           말하는 편이, 뒤지지 않고 지어내는 것보다 낫다.
        """
        from app.core.route_intent import SOURCE_GATE_PROMPT, parse_gate
        try:
            raw = await asyncio.to_thread(
                llm.generate, f"{SOURCE_GATE_PROMPT}\n\n질문: {query}",
                temperature=0.0)
        except Exception as exc:                      # noqa: BLE001
            logger.warning("source_gate_failed", error_type=type(exc).__name__)
            return True
        return parse_gate(raw) == "NEED"
```

- [ ] **Step 4: 비스트리밍에 배선**

`route_and_execute` 의 `if not _confident and not is_system_task and not _is_direct_locked:`
블록 안을 다음으로 바꾼다 (약 L1307):

```python
            if not _confident and not is_system_task and not _is_direct_locked:
                if len(query.strip()) <= 300:
                    flash = get_flash_client()
                    # 2단계: **소스가 필요한가**를 먼저 묻는다 (예/아니오)
                    if await self._needs_source(query, flash):
                        route = await self._classify_with_llm(
                            query, conversation_context, flash)
                    else:
                        logger.info("source_gate_self", path="route_and_execute",
                                    query=query[:80])
                        route = "direct"
```

- [ ] **Step 5: 스트리밍에 배선**

`route_and_stream` 의 같은 블록을 바꾼다 (약 L1676):

```python
            if not _confident and not is_system_task and not _is_direct_locked:
                if len(query.strip()) <= 300:
                    flash = get_flash_client()
                    if await self._needs_source(query, flash):
                        new_route = await self._classify_with_llm(
                            query, conversation_context, flash)
                    else:
                        logger.info("source_gate_self", path="route_and_stream",
                                    query=query[:80])
                        new_route = "direct"
                    if new_route != route:
                        route = new_route
                        yield ("source", route)
```

- [ ] **Step 6: 통과를 확인한다**

Run: `python -m pytest tests/test_route_source_gate.py -q`
Expected: PASS

- [ ] **Step 7: 1·2단계 상호 보호를 고정한다**

`tests/test_route_source_gate.py` 에 이어 붙인다:

```python
def test_노션_사용법은_1단계가_확신으로_잡는다(agent):
    """⚠️ 2단계 게이트는 이 질문을 SELF 로 **틀린다**(실측). 1단계가 잡아
       LLM 에 가지 않는 것이 유일한 보호막이다 — 깨지면 여기서 먼저 걸린다."""
    route, confident = OrchestratorAgent._keyword_classify_ex(agent, "노션 사용법 알려줘")
    assert (route, confident) == ("notion", True)
```

- [ ] **Step 8: 전체 테스트**

Run: `python -m pytest tests/ -q --ignore=tests/frontend`
Expected: PASS

- [ ] **Step 9: 커밋**

```bash
git add app/agents/orchestrator.py tests/test_route_source_gate.py
git commit -m "feat(routing): 확신이 없으면 「소스가 필요한가」를 먼저 묻는다"
```

---

### Task 6: 실사용 리플레이로 경로 이동을 확인하고 배포한다

⛔ **배포 전에 반드시 돌린다.** 오늘 세 번의 수정 모두 이 방식으로 부수 이동이
없음을 확인했다 (매번 「의도한 N건만 이동」).

**Files:**
- Create: `scripts/replay_routes.py`
- Test: 없음 (검증 도구)

**Interfaces:**
- Consumes: `OrchestratorAgent._keyword_classify_ex`
- Produces: 경로 분포 출력

- [ ] **Step 1: 리플레이 도구를 만든다**

`scripts/replay_routes.py`:

```python
# -*- coding: utf-8 -*-
"""실사용 질문을 라우터에 다시 통과시켜 **경로 분포**를 찍는다.

usage: python scripts/replay_routes.py questions.json

⚠️ `_keyword_classify_ex` 는 현재 문장만 받는다 — 대화 맥락을 물려받는 후속
   발화는 재현되지 않는다. 의심 건은 **프로덕션 답변 원문**으로 확인할 것
   (2026-09-07 에 이 방법으로 오판 하나를 잡았다).
"""
import io
import json
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.agents.orchestrator import OrchestratorAgent

agent = OrchestratorAgent.__new__(OrchestratorAgent)
questions = json.load(open(sys.argv[1], encoding="utf-8"))

routes, conf = Counter(), Counter()
for item in questions:
    try:
        route, confident = OrchestratorAgent._keyword_classify_ex(agent, item["q"])
    except Exception:
        continue
    routes[route] += 1
    conf[bool(confident)] += 1

total = sum(routes.values())
print(f"질문 {total}건")
print("경로 :", dict(routes))
print(f"확신 : {conf[True]}건 ({conf[True] / total * 100:.1f}%) · "
      f"LLM 위임 {conf[False]}건")
```

- [ ] **Step 2: 기준선을 뜬다 (구현 전 상태와 비교할 값)**

프로덕션에서 최근 60일 질문을 뽑아 `questions.json` 으로 내려받은 뒤:

Run: `PYTHONPATH=. python scripts/replay_routes.py questions.json`

Expected (2026-09-07 기준선):
```
경로 : {'bigquery': 682, 'direct': 376, 'notion': 64, 'cs': 124, 'gws': 45, 'multi': 4}
확신 : 1002건 (77.4%)
```

- [ ] **Step 3: 변화를 확인한다**

Task 1~5 적용 후 다시 돌린다. **키워드 층은 손대지 않았으므로 분포가 그대로여야
한다** — 달라졌다면 의도치 않은 변경이 섞인 것이니 원인을 찾고 나서 배포한다.

- [ ] **Step 4: 배포**

```bash
CRAVER_SSH_PW='<노션 AI Craver 페이지>' ./sshenv/Scripts/python scripts/deploy_new_server.py was
```
Expected: `활성: active` · `/health HTTP 200` · `기동 에러: 0건`

- [ ] **Step 5: 프로덕션에서 판정을 확인한다**

WAS 에서 `_needs_source` 를 실제 Flash 로 6문항 돌려 `SELF/NEED` 가 기대대로
나오는지 본다 (⚠️ 스크립트에 프록시 환경을 줘야 한다:
`export HTTPS_PROXY=http://10.1.50.2:3128 HTTP_PROXY=$HTTPS_PROXY`).

- [ ] **Step 6: 커밋**

```bash
git add scripts/replay_routes.py
git commit -m "chore(routing): 실사용 질문 리플레이 도구"
```

---

## 배포 후 지켜볼 것

로그 세 줄로 본다 (⚠️ 프로덕션은 앱 INFO 를 버린다 — 필요하면 WARNING 으로 올릴 것):

| 로그 | 뜻 | 정상 범위 |
|---|---|---|
| `source_gate_self` | 2단계가 「소스 불필요」로 판정 | 확신 없는 22.6% 중 일부 |
| `route_inherit_broken_by_rejection` | 3단계 발동 | 60일 20건 수준 |
| `clarify_asked_bare_rejection` | 4단계 발동 | **하루 0~1건**. 이보다 잦으면 `_BARE_MAX_CHARS` 가 넓다 |

⛔ `source_gate_self` 로 뽑힌 질문은 **사람이 한 번 읽는다.** 여기서 틀리면
사내 데이터를 안 쓰고 답한 것이고, 그건 조용한 실패다.

**되돌리기**: Task 5(2단계)만 되돌리면 예전 동작이 된다. Task 3·4 는 서로 독립이라
각각 되돌릴 수 있다.

## 미결 (스펙 §3.5)

SELF 경로가 지금의 큰 direct 프롬프트(회사 소개 + 제품 카탈로그 + 대시보드
카탈로그)를 그대로 쓸지, 가벼운 프롬프트를 따로 둘지는 **측정 후 결정**한다
(2026-09-07 사용자 판단 보류). 이 계획은 `route = "direct"` 로 두어 **지금 것을
그대로 쓴다** — 나중에 이 한 줄에서 갈라진다.

---

### Task 7: SELF 로 답했으면 「사내 데이터로 확인해 드릴까요?」 한 줄을 붙인다

스펙 §3.5. 되묻지 않는 대신, 사내 데이터가 필요했던 사람에게 **돌아갈 문**을 준다.

⛔ 프롬프트로 시키지 않는다 — 이 저장소의 원칙대로 **코드가 붙인다**
   (`gws_agent._with_notices` 와 같은 사상: *"정리 LLM 에게 맡기면 지워진다.
   실제로 지웠다"*).

**Files:**
- Modify: `app/core/route_intent.py` (`source_free_footer`)
- Modify: `app/agents/orchestrator.py` — 비스트리밍 direct 반환부 · 스트리밍
  direct 청크 루프 끝(약 L1756 `yield ("done", "")` 바로 앞)
- Modify: `data/golden_set.json`
- Test: `tests/test_route_source_gate.py`

**Interfaces:**
- Consumes: 없음
- Produces: `source_free_footer() -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_route_source_gate.py` 에 이어 붙인다:

```python
def test_사내데이터로_확인해_드릴까요_한_줄():
    from app.core.route_intent import source_free_footer
    footer = source_free_footer()
    assert "사내 데이터" in footer
    assert footer.startswith("\n")          # 본문과 붙지 않는다


def test_한_줄이_두_경로에_모두_걸려_있다():
    """⛔ 한쪽만 달면 스트리밍이냐에 따라 답이 갈린다."""
    src = open("app/agents/orchestrator.py", encoding="utf-8").read()
    assert src.count("source_free_footer()") == 2
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_route_source_gate.py -k 한_줄 -q`
Expected: FAIL — `ImportError: cannot import name 'source_free_footer'`

- [ ] **Step 3: 최소 구현**

`app/core/route_intent.py` 끝에 이어 붙인다:

```python
def source_free_footer() -> str:
    """사내 자료를 뒤지지 않고 답했을 때 붙이는 한 줄.

    ⛔ 되묻지 않는 대신 **돌아갈 문**을 준다. 사용자는 기다리지 않고 답을 받고,
       사내 데이터가 필요했다면 한 번 눌러서 간다.
    ⛔ 프롬프트로 시키지 않는다 — 정리 LLM 에게 맡기면 지워진다.
    """
    return "\n\n> 💡 사내 데이터로 확인해 드릴까요? 그렇게 말씀해 주시면 조회해 드립니다."
```

- [ ] **Step 4: 게이트가 SELF 였다는 사실을 나른다**

Task 5 에서 만든 두 배선의 `route = "direct"` / `new_route = "direct"` 줄
**바로 위**에 `_source_free = True` 를 넣고, 각 함수 앞쪽(라우팅 시작 전)에
`_source_free = False` 를 초기화한다.

- [ ] **Step 5: 비스트리밍에 붙인다**

`route_and_execute` 의 direct 결과를 돌려주는 자리에서:

```python
        if _source_free and isinstance(result, dict) and result.get("answer"):
            from app.core.route_intent import source_free_footer
            result["answer"] = result["answer"] + source_free_footer()
```

- [ ] **Step 6: 스트리밍에 붙인다**

direct 청크 루프가 끝난 뒤, `yield ("done", "")` **바로 앞** (약 L1756):

```python
            if _source_free:
                from app.core.route_intent import source_free_footer
                yield ("chunk", source_free_footer())
```

- [ ] **Step 7: 골든셋 문항을 더한다 (스펙 §5-7)**

`data/golden_set.json` 에 **라우팅만 보는** 문항 2개를 넣는다. ⛔ 판정 임계는
골든셋이 볼 수 없다 — 기대어는 **그 답변에서만 나올 값**으로 쓴다.

```json
{
  "id": "route_source_free_authoring",
  "question": "주간 회의록 노션 페이지 구성안을 제안해줘",
  "cadence": "daily",
  "not_contains": ["사내 문서 검색", "검색된 문서", "Notion 사내 문서"],
  "note": "붐따 #163 — 만들어 달라는 요청이 문서 검색으로 새면 안 된다"
},
{
  "id": "route_source_needed_still_works",
  "question": "앨리비 계약서 검토 의뢰 절차 노션 문서 찾아줘",
  "cadence": "daily",
  "contains": ["앨리비"],
  "note": "반대 방향 — 문서를 달라는 요청은 그대로 문서를 뒤진다"
}
```

- [ ] **Step 8: 전체 테스트**

Run: `python -m pytest tests/ -q --ignore=tests/frontend`
Expected: PASS

- [ ] **Step 9: 커밋**

```bash
git add app/core/route_intent.py app/agents/orchestrator.py \
        tests/test_route_source_gate.py data/golden_set.json
git commit -m "feat(routing): 사내 자료 없이 답했으면 돌아갈 문을 한 줄 준다"
```
