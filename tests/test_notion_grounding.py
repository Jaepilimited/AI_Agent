"""노션 경로가 문서에 없는 것을 지어내던 것 — 2026-08-19 프로덕션 실측.

증상: "Alexa & Wai 정보"(사내 초상권 DB 의 모델명)를 물었더니 노션 배지를 달고
      아마존 알렉사 · W3C 웹접근성(WAI) · arXiv 논문 · 뉴욕시티발레 무용수를
      지어냈다. 사내 문서에는 그런 내용이 없다.

프롬프트에는 이미 "문서에 없는 내용은 추측하지 마세요"(작성 규칙 1)와
"문서 내용이 질문 주제와 관련이 없으면 절대 그 내용으로 답변하지 마세요"(정합성 8)가
적혀 있었다. **지시는 확률이고 보증은 후처리다** — 읽어온 문서가 질문과 한 낱말도
겹치지 않으면 LLM 을 아예 부르지 않는다.

⛔ 겹침이 0일 때만 막는다. 부분 일치를 요구하면 동의어로 답하는 정상 답변
   (휴가 → 연차 규정 문서)까지 막혀 "0건"이 거짓말이 된다.
"""

from __future__ import annotations

from app.agents.notion_agent import NotionAgent

touches = NotionAgent._content_touches_query


def test_unrelated_content_is_blocked():
    """실제 사고 재현 — 모델명 질문에 사내 문서가 아무 관련이 없다."""
    content = "## 틱톡샵 접속 방법\n계정 관리자에게 권한을 요청한 뒤 파트너센터에 로그인합니다."
    assert touches("Alexa & Wai 정보", content) is False


def test_partial_overlap_passes():
    """한 낱말이라도 겹치면 통과 — 동의어·부분 응답을 막지 않는다."""
    content = "## 연차 규정\n연차 신청은 그룹웨어에서 하며, 절차는 다음과 같습니다."
    assert touches("휴가 신청 절차 알려줘", content) is True


def test_exact_topic_passes():
    content = "## 신규 거래처 온보딩 프로세스\n1. 거래처 등록 요청서 작성"
    assert touches("신규 거래처 온보딩 프로세스", content) is True


def test_conversation_prefix_is_ignored():
    """맥락이 앞에 붙어 와도 판정은 현재 질문으로 한다."""
    q = "[이전 대화]\n반품 절차를 물었다\n\n[현재 질문]\nAlexa & Wai 정보"
    content = "## 반품 절차\n반품은 수령 후 7일 이내에 신청합니다."
    assert touches(q, content) is False


def test_no_keywords_does_not_block():
    """뽑을 낱말이 없으면 판정하지 않는다 (막는 쪽으로 기울지 않는다)."""
    assert touches("?", "아무 내용") is True
