"""초상권 경로 진입 판정 — 2026-08-19 프로덕션 오답에서 나온 회귀.

실제 증상 (프로덕션, 09:23~09:25):
    사진 첨부 + "누구지? 언제까지 써야해?" → "확인할 수 없습니다" (일반 비전 답변)
    사진 첨부 + "누구야"                   → "얼굴로 개인을 식별하는 기능은 제공하지 않습니다"
    "Alexa & Wai 정보"                     → 아마존 알렉사·W3C WAI·arXiv 논문을 지어냄

셋 다 원인이 하나다 — `model_rights_intent()` 가 **질문 텍스트의 특정 낱말만** 봤다:
사진이 붙어 있다는 사실도, 초상권 DB 에 있는 모델 이름도 신호로 쓰지 않았다.
게이트가 False 면 `route_and_stream` 이 이미지가 있을 때 **확신을 갖고** direct(vision)
으로 강제하므로 LLM 재판정도 못 탄다.

⛔ 낱말을 늘리는 방식으로 고치지 않는다 — 양방향으로 건다. 초상권으로 가야 할 것과
   가면 안 되는 것(제품 사진 분석·매출 질문)을 함께 지킨다.
"""

from __future__ import annotations

import pytest

from app.core import model_rights as mr


@pytest.fixture(autouse=True)
def _known_models(monkeypatch):
    """이름 목록은 DB 에서 온다 — 테스트에서는 실제 시트 값 일부로 고정한다."""
    monkeypatch.setattr(mr, "_model_names_cached",
                        lambda: ["Alexa & Wai", "라리사", "장민영", "황보경은",
                                 "조", "소리", "안나"])


# ── 사진이 붙었을 때 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "누구지? 언제까지 써야해?",
    "누구야",
    "이 사람 누구야?",
    "이 사진 써도 돼?",
    "이거 지금 사용 가능해?",
    "이 모델 언제까지 쓸 수 있어?",
])
def test_photo_with_person_or_usage_question_goes_to_rights(q):
    assert mr.model_rights_intent(q, has_image=True) is True


@pytest.mark.parametrize("q", [
    "이 제품 전성분 알려줘",
    "이 이미지 설명해줘",
    "이 차트 수치 읽어줘",
    "",
])
def test_photo_without_rights_intent_stays_vision(q):
    """사진만 붙였거나 제품·차트를 물으면 기존 비전 분석이 맞다."""
    assert mr.model_rights_intent(q, has_image=True) is False


# ── 모델 이름 (사진 없이 텍스트만) ────────────────────────────────────────────

def test_model_name_alone_enters_rights():
    """실제 오답: 'Alexa & Wai' 는 초상권 DB 의 모델명인데 웹 지식으로 샜다."""
    assert mr.model_rights_intent("Alexa & Wai 정보") is True
    assert mr.model_rights_intent("장민영 지금 써도 돼?") is True


def test_short_name_does_not_hijack_unrelated_questions():
    """⛔ '조'·'소리'·'안나' 같은 짧은 이름이 단독으로 경로를 가로채면 안 된다."""
    assert mr.model_rights_intent("조 매출 알려줘") is False
    assert mr.model_rights_intent("소리 나는 제품 있어?") is False
    assert mr.model_rights_intent("안나푸르나 트레킹 후기") is False


def test_short_name_with_rights_signal_still_works():
    """짧은 이름도 초상권 신호가 함께 있으면 잡는다."""
    assert mr.model_rights_intent("안나 초상권 언제까지야") is True
    assert mr.model_rights_intent("소리 사진 써도 돼?", has_image=True) is True


def test_name_inside_longer_word_is_not_a_match():
    """낱말 경계 — 보고서 필터의 '요인도 → 인도' 와 같은 부류."""
    assert mr.model_rights_intent("조회 결과 알려줘") is False


# ── 기존 동작 (회귀) ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "라리사 초상권 언제까지 쓸 수 있어?",
    "모델 사진 사용 기한 알려줘",
])
def test_existing_keyword_paths_unchanged(q):
    assert mr.model_rights_intent(q) is True


@pytest.mark.parametrize("q", [
    "모델별 매출 알려줘",
    "올해 국가별 매출 top5",
    "이번주 내 일정",
])
def test_data_questions_never_enter_rights(q):
    assert mr.model_rights_intent(q) is False


def test_db_failure_falls_back_to_keyword_gate(monkeypatch):
    """이름 목록을 못 읽어도 기존 판정은 살아 있어야 한다 (조용히 죽지 않기)."""
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(mr, "_model_names_cached", _boom)
    assert mr.model_rights_intent("라리사 초상권 언제까지야") is True
    assert mr.model_rights_intent("Alexa & Wai 정보") is False


# ── 사진을 붙였는데 인물을 특정하지 못했을 때 ────────────────────────────────

def test_unidentified_photo_does_not_dump_all_models(monkeypatch):
    """⛔ '누구야' 한마디에 모델 전체 + 에이전시 연락처를 쏟아내면 안 된다."""
    rows = [
        {"model_name": "라리사", "product_line": "센텔라", "marked_unusable": 0,
         "online_ok": 1, "offline_ok": 1, "media": "자사몰", "agency": "에이전시 010-0000-0000",
         "sheet_tab": "A"},
        {"model_name": "장민영", "product_line": "히알루시카", "marked_unusable": 0,
         "online_ok": 1, "offline_ok": 0, "media": "자사몰", "agency": "", "sheet_tab": "A"},
    ]
    monkeypatch.setattr(mr, "fetch_all", lambda *a, **k: rows if "model_rights" in a[0] else [])
    # 이름도 라인도 안 걸리는 질문 = 사진으로만 물은 경우
    assert mr.get_rights_context("누구야", fallback_all=False) == ""
    # 반대로 이름을 대면 그대로 답한다
    assert "라리사" in mr.get_rights_context("라리사 써도 돼?", fallback_all=False)
    # 텍스트 질문의 전체 목록 요청은 기존대로 (회귀)
    assert "장민영" in mr.get_rights_context("지금 쓸 수 있는 모델 알려줘")


# ── 시트 표기가 여러 개인 이름 ───────────────────────────────────────────────

def test_multi_form_names_match_each_form(monkeypatch):
    """`김제인 (김정은)` 은 '김제인' 으로도 걸려야 한다.

    2026-08-19 배포 직후 프로덕션 실측: "김제인 관련 사진 다 보여줘" 가 bigquery 로
    샜다. 시트 표기를 통짜로만 비교하면 사람들이 실제로 부르는 이름을 놓친다.
    """
    monkeypatch.setattr(mr, "_model_names_cached",
                        lambda: ["김제인 (김정은)", "YINGXIN (잉씬)", "Alexa & Wai", "야오 조우"])
    assert mr.model_rights_intent("김제인 관련 사진 다 보여줘") is True
    assert mr.model_rights_intent("김정은 초상권") is True
    assert mr.model_rights_intent("잉씬 지금 써도 돼?") is True
    assert mr.model_rights_intent("야오 조우 기한") is True


def test_multi_form_names_do_not_over_trigger(monkeypatch):
    """쪼갠 표기가 흔한 낱말이면 그대로 오폭한다 — 여전히 낱말 경계를 지킨다."""
    monkeypatch.setattr(mr, "_model_names_cached", lambda: ["김제인 (김정은)"])
    assert mr.model_rights_intent("김제인터내셔널 매출") is False
    assert mr.model_rights_intent("올해 국가별 매출") is False


# ── 원본 시트 안내 (2026-08-19 사용자 요청) ──────────────────────────────────

def test_sheet_url_is_exposed_everywhere():
    """판정을 못 하는 건은 사람이 시트를 봐야 한다 — 답변과 System Status 양쪽에 건다."""
    import inspect

    from app.agents import orchestrator as orch
    from app.core import safety

    assert mr.SHEET_URL.startswith("https://docs.google.com/spreadsheets/")
    assert mr.SPREADSHEET_ID in mr.SHEET_URL

    src = inspect.getsource(orch.OrchestratorAgent._handle_model_rights)
    # 특정 실패 · 미적재 · 정상 답변 — 세 경로 모두 시트를 안내한다
    assert src.count("SHEET_URL") >= 3, "초상권 답변 경로에 시트 안내가 빠졌다"
    assert "범위" in src, "'범위 안에 들어가는지' 되묻는 안내가 있어야 한다"

    assert "SHEET_URL" in inspect.getsource(safety.get_safety_status)
