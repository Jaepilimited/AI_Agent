# -*- coding: utf-8 -*-
"""이미지를 붙이면 데이터 조회를 안 한다 — 그 사실을 말한다 (붐따 #161).

셀라는 실제로 있는 것을 **없다고 단정**했다:
    "신고번호 → 주문번호 매핑 테이블 | 연결된 데이터소스에 수출신고필증/면장
     정보가 없습니다"
    "OP담당자 정보 | 담당자 배정 데이터가 조회 대상에 포함되어 있지 않습니다"
둘 다 `export_logistics` 에 있다 (`export_declaration_number` 89.7% · `op_manager`).
원인은 이미지가 붙으면 vision 경로로 강제되어 **BigQuery 스키마를 아예 안 받는
것**이다. 모르는 것이 당연한데, 모른다고 하지 않고 없다고 했다.
"""
from app.core import image_route_notice as IRN


# ── 붙어야 하는 경우 ────────────────────────────────────────────────────

def test_an_image_on_a_data_question_is_announced():
    text = IRN.notice("면장번호에 맞는 주문번호와 OP담당자 매칭해줘",
                      has_image=True, is_data_question=True)
    assert "데이터 조회를 하지 않습니다" in text


def test_the_notice_reinterprets_any_absence_claim_above_it():
    """⛔ 핵심은 이것이다 — 위에 「없습니다」가 적혀 있어도 그건 「조회하지
    않았다」는 뜻임을 밝혀야, 사용자가 있는 데이터를 포기하지 않는다."""
    text = IRN.notice("수출신고번호 매칭", has_image=True, is_data_question=True)
    assert "조회하지 않았다" in text
    assert "데이터가 없다" in text


def test_the_notice_gives_the_next_step():
    """경고만 하고 할 일을 안 주면 사용자는 그대로 막힌다."""
    text = IRN.notice("매출 얼마야", has_image=True, is_data_question=True)
    assert "이미지 없이" in text


# ── 붙으면 안 되는 경우 ─────────────────────────────────────────────────

def test_a_plain_image_question_gets_no_notice():
    """⛔ "이 사진 설명해줘" 에 데이터 안내가 붙으면 소음이다 — 소음이 되면
    정작 필요할 때도 안 읽힌다."""
    assert IRN.notice("이 사진 설명해줘", has_image=True, is_data_question=False) == ""


def test_no_image_means_no_notice():
    assert IRN.notice("9월 수출건 알려줘", has_image=False, is_data_question=True) == ""


# ── 프롬프트 규칙 ───────────────────────────────────────────────────────

def test_the_prompt_rule_forbids_claiming_absence():
    rule = IRN.PROMPT_RULE
    assert "없다고 단정하지 마라" in rule
    # 실제로 나갔던 문구를 그대로 집어 준다
    assert "연결된 데이터소스에" in rule


# ── 배선 ────────────────────────────────────────────────────────────────

def _orch_src():
    with open("app/agents/orchestrator.py", encoding="utf-8") as fh:
        return fh.read()


def test_both_image_paths_publish_the_notice():
    """⛔ 스트리밍·비스트리밍 양쪽 — 채팅은 스트리밍으로 나간다."""
    src = _orch_src()
    assert src.count("self._image_data_notice(query)") == 2


def test_the_vision_prompt_carries_the_rule():
    src = _orch_src()
    assert "from app.core.image_route_notice import PROMPT_RULE" in src
    assert "_vision_system" in src


def test_the_notice_only_fires_on_data_questions():
    """판정은 라우터가 이미 쓰는 목록을 재사용한다 — 사본을 만들지 않는다."""
    assert "self._DATA_KEYWORDS" in _orch_src()


def test_logistics_words_people_actually_use_are_routed():
    """`면장`·`세일즈운영팀` 으로 물어도 물류 데이터로 가야 한다."""
    src = _orch_src()
    for word in ('"면장"', '"수출신고필증"', '"세일즈운영팀"'):
        assert word in src, word
    # ⛔ `운영팀` 단독은 넣지 않는다 — `@@OP`(재고) 별칭과 겹친다
    assert '\n        "운영팀",' not in src
