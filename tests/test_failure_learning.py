# -*- coding: utf-8 -*-
"""고친 실패가 회귀로 굳는가.

⛔ 실측(2026-08-27): 👎 74건 중 골든셋에 반영된 것이 **0건**. 처리 완료로 닫혔는데
   골든에 없는 것이 40건 — **고친 40가지가 재발해도 아무도 모른다.**
   CLAUDE.md 에 "규칙을 바꿨으면 골든 문항도 같이 추가한다" 고 적혀 있지만
   손으로 하는 일은 결국 안 된다. 그래서 빠진 것을 매일 보이게 만든다.
"""
import inspect
from pathlib import Path

from app.core import failure_learning as fl

ROOT = Path(__file__).resolve().parent.parent


def test_golden_coverage_is_matched_by_signature_not_raw_text():
    """⚠️ "이번 달" 과 "이번달" 이 다른 문항으로 보이면 **있는데도 없다고 센다**
       (`sql_cache` 가 문자열 일치라 안 걸리던 것과 같은 함정)."""
    src = inspect.getsource(fl._golden_signatures)
    assert "signature" in src


def test_comment_less_feedback_is_not_a_candidate():
    """무엇이 틀렸는지 모르면 기대 문구를 쓸 수 없어 문항이 될 수 없다 —
       진단할 수 없는 건을 대기열에 섞지 않는 규칙과 같다."""
    src = inspect.getsource(fl.golden_candidates)
    assert "comment IS NOT NULL" in src and "TRIM(f.comment)" in src


def test_only_fixed_ones_count():
    """아직 안 고친 것은 회귀를 쓸 수 없다 — 무엇이 맞는 답인지 아직 모른다."""
    assert "f.status = 'done'" in inspect.getsource(fl.golden_candidates)


def test_the_alert_looks_at_a_recent_window():
    """⚠️ 오래된 것까지 매일 세면 **첫날부터 40건**이 떠서 곧 무시당한다.
       (`feedback_backlog` 이 유입량을 세다 매일 울리던 것과 같은 실패다.)"""
    assert fl.RECENT_DAYS <= 30
    from app.core import self_check as sc

    src = inspect.getsource(sc._check_fixed_bugs_have_a_regression)
    assert "recent" in src and "total" in src, "밀린 수도 함께 보여야 한다"


def test_drafts_never_guess_the_expected_text():
    """⛔ 지금 답변에서 낱말을 뽑아 `contains` 를 채우면 **지금 답이 맞다**는 전제가
       된다 — 그 전제가 틀렸을 때 문항이 오답을 굳힌다.
    ⚠️ 그리고 일반어를 넣으면 되묻기 답변에도 들어 있어 **거짓 통과**가 난다."""
    drafts = fl.draft_items([{"feedback_id": 1, "question": "q", "why": "w",
                              "fixed_at": None, "note": ""}])
    assert drafts[0]["contains"] == [], "기대 문구를 자동으로 지어냈다"
    assert drafts[0]["question"] == "q"

    script = (ROOT / "scripts" / "propose_golden_items.py").read_text(encoding="utf-8")
    assert "golden_set.json" in script
    # ⛔ 스크립트가 골든셋을 직접 고치면 안 된다 — 사람이 옮긴다
    assert "open(\"data/golden_set.json\", \"w\"" not in script


def test_the_check_is_registered():
    from app.core.self_check import CHECKS

    assert any(c.id == "fixed_bugs_regression" for c in CHECKS)


def test_the_question_is_the_one_right_before_the_answer():
    """⛔ `u.id < m.id` 로 조인하고 `GROUP BY` 로 묶으면 **직전 질문이 아니라 아무
       질문이나** 딸려 온다 (MySQL 이 조용히 한 행을 고른다).

    실측(2026-08-27): #152 의 이유는 "동남아시아2팀" 인데 질문은 "NAD cream" 으로
    붙어 있었다 — 목록은 그럴듯한데 짝이 틀린, 가장 발견이 늦는 종류의 실패다.
    """
    src = inspect.getsource(fl.golden_candidates)
    # 직전 질문을 **정확히** 집는가
    assert "SELECT MAX(id) FROM messages" in src
    # ⚠️ 느슨한 조인(`u.id < m.id` 를 ON 절에 직접)으로 돌아가면 안 된다.
    #    서브쿼리 안의 `id < m.id` 는 정상이므로 ON 절만 본다.
    on_clause = src.split("JOIN messages u ON", 1)[1].split("WHERE", 1)[0]
    assert "u.id = (" in on_clause, on_clause.strip()[:120]


def test_chitchat_feedback_is_not_a_golden_candidate():
    """잡담에 달린 👎 는 문항이 될 값이 없다 ("웃으라 하지 말아요" 가 후보로 올라왔다).
       ⚠️ 라우트 판정은 `skill_memory` 것을 **그대로** 쓴다 — 사본을 만들지 않는다."""
    assert "direct" not in fl._WORK_ROUTES
    src = inspect.getsource(fl._route_of)
    assert "skill_memory import _infer_route" in src
