# -*- coding: utf-8 -*-
"""질문 이력에서 "내가 자주 묻는 것" 을 뽑는 규칙.

⛔ 여기서 뽑은 값은 첫 화면의 제안이 되고, 제안은 사람이 그대로 누른다.
   그래서 **LLM 을 쓰지 않는다** — 왜 그게 떴는지 설명할 수 있어야 한다.
⛔ 그리고 **자동으로 필터를 끼우지 않는다.** 이 모듈은 "이 사람은 인도네시아를 자주
   본다" 까지만 말한다. 묻지 않은 조건이 조용히 붙는 것이 이 시스템에서 가장 위험한
   실패다 (기간을 말없이 자르던 사고와 같은 부류).
"""
import inspect
from pathlib import Path

from app.core import query_profile as qp

ROOT = Path(__file__).resolve().parent.parent


def test_same_question_different_wording_is_one():
    """⚠️ "이번 달" 과 "이번달", 끝의 "알려줘" 차이로 반복이 반복으로 안 보이면
       `sql_cache` 가 문자열 완전 일치라 거의 안 걸리던 것과 같은 함정이다."""
    assert qp.signature("쇼피 인도네시아 이번 달 매출 알려줘") == \
           qp.signature("쇼피 인도네시아 이번달 매출")
    assert qp.signature("국가별 매출 순위 Top 10 알려줘") == \
           qp.signature("국가별 매출 순위 Top 10 보여줘")
    assert qp.signature("일본 매출") != qp.signature("미국 매출")


def test_chitchat_never_becomes_a_suggestion():
    """⛔ 실측(2026-08-27): 반복이 없는 사람의 상위 제안이 **"1+1+(1*5) 는 뭐임?"** 이 됐다.
       첫 화면 칩은 업무를 다시 하게 만드는 자리지 지난 잡담을 보여주는 자리가 아니다.
       `direct` 경로(인사말·잡담·기능 질문)는 후보에서 뺀다."""
    assert "direct" not in qp._WORK_ROUTES
    src = inspect.getsource(qp.rebuild)
    assert "_WORK_ROUTES" in src


def test_follow_up_questions_are_not_offered():
    """⛔ "도넛 차트로 랜더링 해줘" · "TOP10이 아니라 전체 SKU로" 를 칩으로 만들면
       눌러도 **앞 대화가 없어 엉뚱한 답**이 나간다 — 제안이 함정이 된다.

    `context_len` 이 0 이면 그 질문이 대화의 첫 마디였다는 뜻이고, 그 값은 이미
    기록하고 있었다 (새로 심을 필요가 없었다).
    """
    src = inspect.getsource(qp.rebuild)
    assert "context_len" in src
    assert 'row.get("ctx")' in src


def test_long_or_multiline_questions_are_skipped():
    """여러 줄짜리 보고서 요청(수백 자)이 그대로 칩이 되면 화면이 무너진다."""
    src = inspect.getsource(qp.rebuild)
    assert "MAX_CHIP_LEN" in src and "chr(10)" in src
    assert qp.MAX_CHIP_LEN <= 60


def test_axes_reuse_the_report_extractor():
    """⛔ 같은 규칙을 두 곳에서 따로 구현하면 언젠가 서로 다른 말을 한다 —
       국가·팀·브랜드 추출은 보고서가 이미 쓰는 것을 그대로 쓴다."""
    src = inspect.getsource(qp.rebuild)
    assert "from app.reports.registry import extract_filters" in src


def test_ties_are_broken_by_recency():
    """⚠️ 동점을 가나다순으로 두면 반복이 없는 사람에게 **이름순으로** 아무 질문이나
       올라온다 (실측에서 그렇게 나왔다)."""
    src = inspect.getsource(qp.rebuild)
    order = src.split("ordered = sorted(", 1)[1].split("for sig, item in ordered", 1)[0]
    assert "timestamp()" in order, order.strip()[:120]


def test_a_profile_is_never_read_for_someone_else():
    """⚠️ 질문에는 담당 거래처·미출시 제품처럼 남이 보면 안 되는 말이 섞인다.
       막는 자리는 **호출부가 아니라 여기**다 — 호출부는 언젠가 하나를 빠뜨린다."""
    src = inspect.getsource(qp.for_user)
    assert "user_email = %s" in src
    assert "_is_person" in src

    api = (ROOT / "app" / "api" / "personal_profile_api.py").read_text(encoding="utf-8")
    # ⛔ 이메일을 요청에서 받으면 남의 이력을 조회할 입구가 생긴다 — JWT 에서만 꺼낸다
    assert "Depends(get_current_user)" in api
    assert "user.email" in api
    assert "user_email:" not in api and "request." not in api


def test_bots_are_not_profiled():
    """골든봇·카나리는 사람이 아니다 — 프로필에 섞이면 남의 습관이 내 화면에 뜬다."""
    assert not qp._is_person("golden-bot@system")
    assert qp._is_person("jeffrey@skin1004korea.com")


def test_chips_are_bound_by_delegation():
    """⛔ 칩마다 직접 걸면 **나중에 추가한 칩은 눌러도 아무 일이 없다** — 개인 제안은
       로그인 뒤에 붙으므로 그때 걸린 칩이 없다. 에러도 안 난다."""
    js = (ROOT / "app" / "frontend" / "chat.js").read_text(encoding="utf-8")
    assert 'document.querySelectorAll(".suggestion-chip").forEach' not in js
    assert 'closest(".suggestion-chip")' in js


def test_default_chips_survive_for_newcomers():
    """⛔ 기본 칩을 지우면 이력이 없는 사람(신규 입사자)이 **빈 화면**을 본다."""
    js = (ROOT / "app" / "frontend" / "chat.js").read_text(encoding="utf-8")
    fn = js.split("function loadMySuggestions", 1)[1].split("\n  }", 1)[0]
    assert "insertBefore" in fn, "기본 칩 앞에 넣어야 한다 (지우면 안 된다)"
    assert "innerHTML" not in fn, "칩 영역을 통째로 갈아치우고 있다"
    assert "chip.title" in fn, "무엇을 보내는지 모르고 누르게 하면 안 된다"


def test_the_job_is_watched():
    from app.core.self_check import EXPECTED_JOBS

    assert "query_profile_daily" in EXPECTED_JOBS
