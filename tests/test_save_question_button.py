# -*- coding: utf-8 -*-
"""채팅의 '이 질문 정기적으로 받아보기' 버튼 — 소스 불변식.

브라우저 없이 소스로 지킨다. 여기서 깨지는 것들은 전부 **에러가 아니라 조용한 결함**이라
사람이 눈으로 볼 때까지 드러나지 않는다:

  · 새 대화에만 버튼을 달면 지난 대화를 열었을 때 사라진다
  · `@@` 를 뗀 뒤 저장하면 "@@보고서 일본 매출" 이 그냥 조회로 되살아난다
  · 서버가 거절한 이유를 삼키면 저장된 줄 알게 된다
"""
from __future__ import annotations

import io
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_JS = os.path.join(REPO, "app", "frontend", "chat.js")


def _source() -> str:
    with io.open(CHAT_JS, encoding="utf-8") as handle:
        return handle.read()


def test_button_is_added_on_both_render_paths():
    """⛔ 한쪽에만 달면 지난 대화에서 버튼이 사라진다 — 반복 질문일수록 거기서 저장한다."""
    calls = _source().count("_addSaveQuestionButton(")
    # 정의 1 + 호출 2
    assert calls >= 3, f"_addSaveQuestionButton 호출이 {calls - 1}곳뿐이다 (2곳이어야 한다)"


def test_saved_question_keeps_its_at_source_prefix():
    """⛔ `@@` 를 떼기 **전에** 저장용 질문을 잡아야 한다.

    서버는 `route_and_execute` → `parse_db_prefix` 로 질문 문자열의 `@@` 를 직접
    해석한다. 떼고 저장하면 소스 지정이 조용히 사라진다.
    """
    src = _source()
    capture = src.find("var userQuestionForSave = text;")
    strip = src.find("var _parsed = parseSourceTokens(text);")
    assert capture != -1, "저장용 질문을 잡는 곳이 없다"
    assert strip != -1
    assert capture < strip, "`@@` 를 뗀 뒤에 저장용 질문을 잡고 있다"


def test_server_rejection_is_shown_not_swallowed():
    """⚠️ 5개 상한 등으로 거절되면 이유를 보여준다 — 저장된 줄 알면 더 나쁘다.

    ⛔ **양쪽 계약을 대조한다.** 처음엔 화면에 `data.ok === false` 가 있는지만 봤고,
       그 테스트는 통과했는데 실제로는 저장 실패에 "저장했습니다" 가 떴다 —
       서버가 거절을 `400 + {detail}` 로 주는데 본문에 `ok` 가 없어 그 분기가
       **영영 참이 아니었다** (2026-08-27 브라우저 실측). 한쪽만 보는 검사는
       계약 불일치를 통과시킨다.
    """
    import inspect

    from app.api import saved_questions_api

    api_src = inspect.getsource(saved_questions_api.add_saved_question)
    assert "HTTPException" in api_src and "detail=" in api_src, \
        "서버 거절 방식이 바뀌었다 — 화면의 처리도 함께 고쳐야 한다"

    src = _source()
    modal = src.split("function _openSaveQuestionModal(", 1)[1].split("function _addSaveQuestionButton", 1)[0]
    assert "httpOk" in modal or "r.ok" in modal, \
        "화면이 HTTP 상태를 보지 않는다 — 400 을 성공으로 읽는다"
    assert "data.detail" in modal, \
        "서버는 이유를 `detail` 로 준다. 그 필드를 읽지 않으면 이유가 사라진다"


def test_empty_question_gets_no_button():
    """질문이 없으면 버튼도 없다 — 눌러도 저장할 게 없는 버튼은 소음이다."""
    src = _source()
    body = src.split("function _addSaveQuestionButton(", 1)[1][:400]
    assert "if (!question" in body and "return;" in body


def test_modal_uses_theme_tokens_not_inline_colors():
    """⛔ 테마를 타는 색을 JS 인라인에 두지 마라.

    없는 변수는 에러가 아니라 폴백이라, 라이트 모드에서 어두운 배경에 어두운 글자가
    되어도 아무도 못 잡는다 (2026-08-13 피드백 모달에서 실제로 났다).
    """
    src = _source()
    modal = src.split("function _getSqModal(", 1)[1].split("function _openSaveQuestionModal", 1)[0]
    assert "var(--" not in modal, "모달 마크업에 CSS 변수를 인라인으로 썼다"
    assert not re.search(r"style\s*=\s*[\"'][^\"']*(background|color)\s*:", modal), \
        "모달에 인라인 배경/글자색이 있다"
    # 오버레이는 className 으로, 박스는 마크업으로 붙는다 — 둘 다 style.css 의 클래스다.
    assert 'fb-overlay' in modal and 'class="fb-box"' in modal


def test_cadence_options_match_the_server_contract():
    """화면의 주기가 서버 저장소가 아는 값과 같아야 한다 — 다르면 저장이 조용히 거절된다."""
    from app.core import saved_questions

    src = _source()
    for value in ("daily", "weekly", "monthly"):
        assert f'value="{value}"' in src, f"화면에 {value} 선택지가 없다"

    schema = saved_questions._DDL if hasattr(saved_questions, "_DDL") else ""
    if schema:
        for value in ("daily", "weekly", "monthly"):
            assert value in schema, f"서버 스키마에 {value} 가 없다"


def test_weekly_sends_the_weekday_the_server_requires():
    """⛔ 화면이 서버가 **요구하는 값**을 보내는지 본다 — 값 목록만 대조하면 못 잡는다.

    2026-08-27 브라우저 실측에서 잡혔다: 화면은 `{question, cadence}` 만 보냈고
    `weekly` 는 서버가 요일을 요구해서 **"매주 월요일" 을 고르면 매번 거절**됐다.
    라벨은 이미 월요일이라고 약속하고 있었으니 화면이 그 값을 실어야 한다.
    소스 검사만 하던 회귀가 이 결함을 통과시켰다 — 계약은 값이 아니라 **요구사항**으로 본다.
    """
    src = _source()
    modal = src.split("function _openSaveQuestionModal(", 1)[1].split("function _addSaveQuestionButton", 1)[0]
    assert "weekday" in modal, "weekly 인데 화면이 weekday 를 보내지 않는다"
    assert re.search(r'weekly"?\s*\)?\s*payload\.weekday\s*=\s*\d', modal.replace("\n", " ")) or \
        ("weekly" in modal and "payload.weekday" in modal), \
        "weekly 일 때 weekday 를 싣는 분기가 없다"


def test_weekly_without_weekday_is_rejected_by_the_store():
    """서버 쪽 계약이 실제로 요일을 요구하는지 — 화면 수정의 근거를 못 박는다."""
    from app.core import saved_questions as sq

    out = sq.add.__doc__ or ""
    src = __import__("inspect").getsource(sq.add)
    assert "weekday" in src, "저장소가 weekday 를 보지 않는다면 화면 수정 근거가 사라진다"


def test_assets_carry_no_handwritten_cache_number():
    """`?v=` 는 이제 서빙할 때 **내용 해시**로 붙는다 (2026-08-31).

    손으로 적은 번호가 남아 있으면 다음 사람이 그걸 보고 또 올린다 — 지문이
    덮어쓰므로 아무 효과가 없는데 시간만 든다.
    """
    with io.open(os.path.join(REPO, "app", "frontend", "chat.html"), encoding="utf-8") as fh:
        html = fh.read()
    hand = re.findall(r"/(?:frontend|static)/[\w./-]+\.(?:js|css)\?v=(\d+)", html)
    assert not hand, f"손으로 적은 캐시 번호가 남아 있다: {hand}"
