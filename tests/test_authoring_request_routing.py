# -*- coding: utf-8 -*-
"""만들어 달라는 요청은 문서 검색이 아니다 — 붐따 #163 · #164.

2026-09-04 프로덕션. 같은 분이 30분 사이에 두 번 붐따를 눌렀고, 원인이 같다:
**질문에 「노션」이 들어 있다는 이유로 사내 문서 검색으로 갔다.**

    #163 "나 개인 업무 노션 페이지 구성할건데 제안해달라고!!!!!!!!"
         → CS 티트리카 문서 → 영업2팀 신규 입사자 문서 → ERP 기안 가이드
         (세 번 연속 헛다리. "너 진자 붐따임" 을 듣고서야 바로 답했다)

    #164 "여기 Today 에 뜨는 내용을 노션에 자동화해서 뜨게 할 수 없나?"
         → "[영업2팀] 타 팀 협업 요청" 문서. 셀라 **자기 기능**을 물은 것인데도.

⛔ 대응은 「노션」을 키워드에서 빼는 게 아니다 — "노션 사용법 알려줘" 는 계속
   문서여야 한다. **질문의 모양**으로 가른다: 찾아 달라는 것인가, 만들어 달라는 것인가.
"""

import pytest

from app.agents.orchestrator import OrchestratorAgent


@pytest.fixture(scope="module")
def route():
    agent = OrchestratorAgent.__new__(OrchestratorAgent)

    def _route(question: str):
        return OrchestratorAgent._keyword_classify_ex(agent, question)

    return _route


# ── 만들어 달라는 요청 → direct ────────────────────────────────────────────
AUTHORING = [
    "나 개인 업무 노션 페이지 구성할건데 제안해달라고",
    "개인 업무 노션 페이지 구성안 제안해줘",
    "주간 회의록 템플릿 좀 짜줘",
    "팀 소개 페이지 초안 작성해줘",
    "노션 대시보드 구조 추천해줘",
    "온보딩 체크리스트 만들어줘",
]


@pytest.mark.parametrize("question", AUTHORING)
def test_만들어달라는_요청은_문서검색이_아니다(question, route):
    got, confident = route(question)
    assert got == "direct", f"{question!r} → {got} (문서 검색으로 샜다)"
    assert confident


# ── 자기 기능을 묻는 부정형 질문 → direct ──────────────────────────────────
CAPABILITY = [
    "여기 Today 에 뜨는 내용을 노션에 자동화해서 뜨게 할 수 없나?",
    "브리핑을 노션으로 보낼 수 없을까?",
    "이거 엑셀로 받을 수 없나요",
]


@pytest.mark.parametrize("question", CAPABILITY)
def test_할_수_없나_도_기능_질문이다(question, route):
    """`수 있어` 만 등록돼 있어 **부정형**이 통째로 빠져 있었다."""
    got, confident = route(question)
    assert got == "direct", f"{question!r} → {got}"
    assert confident


# ── 반대 방향: 이것들은 계속 문서·조회여야 한다 ────────────────────────────
KEEP = [
    ("노션 사용법 알려줘", "notion"),
    ("틱톡샵 접속 방법 알려줘", "notion"),
    ("반품 정책 문서 찾아줘", "notion"),
    ("인플루언서 시딩 가이드라인 알려줘", "notion"),
    ("데이터 분석 파트 자료 어디 있어?", "notion"),
    ("계약서 작성 절차 알려줘", "notion"),
    ("출장 보고서 양식 어디 있어?", "notion"),
    ("2026년 일본 매출 보고서 만들어줘", "bigquery"),
    ("2026년 8월 매출 알려줘", "bigquery"),
    ("작년 대비 올해 매출 추이 정리해줘", "bigquery"),
]


@pytest.mark.parametrize("question,expected", KEEP)
def test_찾아달라는_요청과_조회는_그대로다(question, expected, route):
    got, confident = route(question)
    assert got == expected, f"{question!r} → {got} (기대 {expected})"
    assert confident


def test_보고서_생성은_이_관문이_가로채지_않는다():
    """⚠️ 보고서는 **라우팅보다 먼저** 판정된다 (`wants_report`).

    그래서 route 가 direct 인지로 검사하면 아무것도 지키지 못한다 — 실제로
    "일본 시장 리포트 작성해줘" 는 이 수정 **전에도** direct 였고, 그럼에도
    보고서는 정상으로 만들어지고 있었다. 지켜야 할 것은 `wants_report` 다.
    """
    from app.reports import registry

    for q in ("2026년 상반기 B2B 매출 보고서 만들어줘",
              "일본 시장 리포트 작성해줘"):
        assert registry.wants_report(q), f"{q!r} 가 보고서 경로를 잃었다"


def test_생성_관문이_보고서_문구를_삼키지_않는다(route):
    """`_is_authoring_request` 자체가 보고서 요청을 가로채면 안 된다."""
    from app.agents.orchestrator import OrchestratorAgent

    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    for q in ("2026년 상반기 B2B 매출 보고서 만들어줘",
              "일본 시장 리포트 작성해줘"):
        assert not OrchestratorAgent._is_authoring_request(agent, q), q


def test_출근브리핑이_기능_목록에_있다():
    """#164 는 경로만 고쳐선 부족하다 — 목록에 없으면 "없다"고 답한다.

    프롬프트에 "여기 적힌 기능을 없다고 답하지 마세요" 라고 못 박아 두었는데,
    정작 `Today`(출근 브리핑)가 목록에 빠져 있었다.
    """
    from app.agents.orchestrator import OrchestratorAgent

    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    prompt = OrchestratorAgent._build_direct_system_prompt(agent)
    for word in ("출근 브리핑", "Today", "잔디로 받기"):
        assert word in prompt, f"기능 목록에 {word!r} 가 없다"
    # 없는 연동을 있다고 답하지 않도록 한계도 함께 적혀 있어야 한다.
    # ⛔ "노션·슬랙 등 …은 없습니다" 처럼 강한 부정으로 못 박지 않는다 —
    # 다른 팀이 노션 연동을 만들고 있어 며칠 안에 거짓말이 된다 (대표 제품
    # 목록과 같은 함정). 지금 되는 것(잔디)과 "목록에 없으면 지어내지 말라"는
    # 가드만 확인한다.
    assert "잔디" in prompt
    assert "목록에 없는 연동" in prompt


# ── 「낱말이 없다」는 확신의 근거가 아니다 ─────────────────────────────────
# 실측(2026-09-07, 실사용 60일 1,295건): `노션` + 데이터 낱말 **부재** 만으로
# confident=True 를 주던 가지가 결정한 것은 9건뿐이었고 그중 7건이 오분류였다.
# 제대로 맞힌 1건은 위쪽 `_DOC_WORD` 관문이 이미 잡는다.
AMBIGUOUS = [
    "셀라야 지금까지는 우리팀 노션이엇구, 이제 내 개인노션 만들고야",
    "연동된 Notion 페이지 리스트 모두 알려줘",
    "그 내 일간 업무 지메일 연동해서 정리하는거 노션으로 바로 쏠수있어?",
]


@pytest.mark.parametrize("question", AMBIGUOUS)
def test_부재를_근거로_확신하지_않는다(question, route):
    """확신을 뺀다 = LLM 이 다시 본다. 값은 notion 그대로라 실패해도 안전하다."""
    _, confident = route(question)
    assert not confident, f"{question!r} 를 확신으로 끝냈다 (LLM 재판정을 건너뛴다)"


def test_진짜_문서_요청은_여전히_확신한다(route):
    """확신을 뺀 대가로 잃는 것이 없어야 한다 — `_DOC_WORD` 가 위에서 잡는다."""
    for q in ("SKIN1004의 PETA 인증 명칭을 사내 문서에서 찾아줘",
              "반품 정책 문서 찾아줘",
              "출장 보고서 양식 어디 있어?"):
        got, confident = route(q)
        assert (got, confident) == ("notion", True), f"{q!r} → {got}/{confident}"
