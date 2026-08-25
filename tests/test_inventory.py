# -*- coding: utf-8 -*-
"""OP 재고 — 시트를 **표로** 적재하고 조회로 답한다 (2026-08-25).

사용자 요청은 "재고 시트를 벡터로 추가"였는데, 시트를 열어 보니 `SKU | 품목명 |
창고별 수량` 표였다. 벡터로 넣으면 안 되는 이유를 확인하고 표 조회로 갔다:
  · 숫자는 임베딩에 거의 담기지 않는다 (`104` 와 `1,040` 이 구분되지 않는다)
  · 한 청크에 SKU 수십 개가 섞여 어느 숫자가 어느 제품 것인지 뒤섞인다
  · 매일 바뀌는 값이라 하루만 지나도 **틀린 재고를 자신 있게** 답한다
재고는 틀리게 답하는 것이 못 답하는 것보다 나쁘다 — 성분(`ingredients.py`)과 같은 사상.
"""
import inspect

import pytest

from app.core import inventory


# ── 질문 판정 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,expected_has", [
    ("센텔라 앰플 재고 얼마야?", "센텔라"),
    ("포어마이징 클레이 재고 확인해줘", "포어마이징"),
    ("히알루시카 선세럼 몇 개 남았어?", "히알루시카"),
    ("AASKA010 재고", "AASKA010"),
])
def test_stock_questions_are_detected(q, expected_has):
    term = inventory.inventory_intent(q)
    assert term, q
    assert expected_has in term, (q, term)


@pytest.mark.parametrize("q", [
    "일본 매출 알려줘",
    "센텔라 앰플 사용법 알려줘",
    "재고 관리 방법 알려줘",      # 규정 질문 — 조회가 아니다
    "재고 시트 어디 있어",        # 위치 질문 — 조회가 아니다
])
def test_non_stock_questions_are_ignored(q):
    assert inventory.inventory_intent(q) is None, q


def test_explicit_source_does_not_need_the_word():
    """`@@OP` 로 지정하면 '재고' 라는 말이 없어도 재고 질문이다."""
    assert inventory.inventory_intent("센텔라 앰플") is None
    assert inventory.inventory_intent("센텔라 앰플", explicit=True) == "센텔라 앰플"


def test_term_extraction_does_not_cut_inside_words():
    """⛔ 직접 정규식을 짰다가 `포어마이징` 의 `이` 를 낱말 **안에서** 잘라
       `포어마 징` 이 됐다 (2026-08-25). `라인` ⊂ `가이드라인` 과 같은 함정이다.
       추출은 `query_keywords.extract` 한 곳에서만 한다."""
    term = inventory.inventory_intent("포어마이징 클레이 재고 확인해줘")
    assert "포어마이징" in term, term
    src = inspect.getsource(inventory.inventory_intent)
    assert "query_keywords" in src, "검색어 추출을 직접 구현하지 마라"


# ── 적재 규칙 ────────────────────────────────────────────────────────────────

def test_derived_total_column_is_not_stored():
    """⛔ `SF 재고합` 은 SF 계열만 더한 **부분합**이다. 적재하면 `SUM(qty)` 에 함께
       더해져 이중계상이 난다 (`Production_Cost2` 가 FOC 를 이미 포함하던 것과 같은 함정).
       실측: `AASKA019` 는 CG ETC 3,200 인데 `SF 재고합` 은 0 이다."""
    assert "SF 재고합" in inventory._DERIVED_COLS
    src = inspect.getsource(inventory._read_sheet)
    assert "_DERIVED_COLS" in src


def test_sync_drops_microseconds_before_comparing():
    """⛔ MariaDB DATETIME 은 초 단위다. 마이크로초가 남은 값으로 `synced_at < now` 를
       비교하면 **방금 넣은 행이 전부 걸린다** — 12,166행을 넣고 12,166행을 지워
       테이블이 빈 채로 '성공' 했다 (2026-08-25 실측). 에러가 나지 않는 고장이다."""
    src = inspect.getsource(inventory.sync_inventory)
    assert "microsecond=0" in src


def test_cleanup_refuses_when_it_would_delete_everything():
    """정리 대상이 이번 적재분 이상이면 지우지 않는다 — 재고가 통째로 비면
       앱은 에러 없이 '재고 없음' 을 답한다."""
    src = inspect.getsource(inventory.sync_inventory)
    assert "cleanup_refused" in src


def test_search_splits_words():
    """품목명이 `마다가스카르센텔라앰플100ml` 처럼 붙어 있다 — 검색어를 통째로
       LIKE 하면 0건이 난다 ("센텔라 앰플" → 0건, 2026-08-25 실측)."""
    src = inspect.getsource(inventory.search)
    assert "raw.split()" in src and " AND " in src


# ── 배선 ─────────────────────────────────────────────────────────────────────

def test_registered_as_at_source_with_table_route():
    from app.agents.orchestrator import OrchestratorAgent as O

    entry = next((e for e in O._DB_REGISTRY if e["key"] == "OP"), None)
    assert entry, "@@OP 가 등록되지 않았다"
    # ⛔ 벡터(notion)가 아니라 표 조회여야 한다
    assert entry["route"] == "inventory", entry
    parsed, clean = O.parse_db_prefix("@@OP 센텔라 앰플 재고")
    assert parsed and parsed["key"] == "OP"
    assert clean.strip() == "센텔라 앰플 재고"


def test_wired_into_both_answer_paths():
    """⛔ 한 경로만 걸면 스트리밍/비스트리밍에 따라 답이 갈린다 — 이 프로젝트에서
       반복된 사고다 (direct 프롬프트 2벌, 팀 값 오기 4곳 중 1곳)."""
    from app.agents import orchestrator

    src = inspect.getsource(orchestrator)
    assert src.count("_inventory_term(") >= 3      # 정의 1 + 두 경로
    assert src.count("_handle_inventory_query(") >= 3


def test_daily_job_is_watched():
    """적재가 멈추면 재고가 조용히 낡는다 — 자가 점검이 실행 기록을 봐야 한다."""
    from app.core.self_check import EXPECTED_JOBS

    assert "op_inventory_sync_daily" in EXPECTED_JOBS


def test_status_exposes_sheet_timestamp():
    """⚠️ 매일 바뀌는 값이라 **언제 것인가**가 숫자만큼 중요하다."""
    src = inspect.getsource(inventory.status)
    assert "sheet_updated_at" in src
    from app.core import safety
    assert 'services["OP"]' in inspect.getsource(safety.get_safety_status)


def test_at_source_prefix_is_stripped_before_search():
    """⛔ `@@OP` 접두사가 검색어에 남으면 0건이 난다.

    실측 (2026-08-25 프로덕션): `@@OP 포어마이징 클레이` 가 `'OP 포어마이징 클레'` 로
    검색돼 "품목을 찾지 못했습니다" 가 나갔다. `@@Google Workspace` 가 질문에
    "Workspace" 를 남기던 것과 같은 부류다 — 관문에는 **접두사가 걷힌 문장**을 준다.
    """
    from app.agents.orchestrator import OrchestratorAgent as O

    o = O.__new__(O)
    entry, clean = O.parse_db_prefix("@@OP 포어마이징 클레이")
    term = o._inventory_term("@@OP 포어마이징 클레이", clean, entry, None)
    assert term and "OP " not in term, term
    assert "포어마이징" in term, term
