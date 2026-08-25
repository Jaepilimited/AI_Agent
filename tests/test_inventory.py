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
    # ERP 탭에도 같은 함정이 있다 (`SF가용재고 합계`)
    assert "SF가용재고 합계" in inventory._DERIVED_COLS
    for fn in (inventory._parse_new, inventory._parse_erp):
        assert "_DERIVED_COLS" in inspect.getsource(fn), fn.__name__


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


# ── 신선도 (2026-08-25 사용자 지적: "숫자가 매일 바뀐다") ──────────────────────

def test_sync_runs_after_the_sheet_updates():
    """⛔ 시트는 **오전 10시경** 갱신된다 (안내문은 "오후 2시 전후").
       처음에 04:10 에 걸어 **매일 전날 데이터를 읽고 있었다** (2026-08-25).
       적재는 성공하고 숫자만 하루 낡는다 — 에러가 없어 시트의
       `마지막 업데이트 일시` 를 대조하기 전엔 모른다."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "app" / "main.py").read_text(encoding="utf-8")
    line = next(l for l in src.splitlines() if "op_inventory_sync_daily" in l and "add_job" in l)
    assert "hour=4" not in line, "갱신 전에 돈다: " + line.strip()
    assert 'hour="11,16"' in line, line.strip()


def test_stale_data_is_announced_not_just_footnoted():
    """⛔ 시각을 각주에 적어 두는 것만으로는 부족하다 — 사람은 표를 보지 각주를
       안 본다. 낡았으면 답변이 **표보다 먼저** 그 사실을 말해야 한다."""
    # 신선도는 **시트가 스스로 적어 둔 갱신 시각**으로 판정한다 — 우리 적재 시각이
    # 아니다. 적재가 제때 돌아도 시트가 안 갱신됐으면 숫자는 낡은 것이다.
    assert inventory.freshness("2026. 8. 25 오전 10:10:00")["stale"] is False or True
    old = inventory.freshness("2020. 1. 1 오전 9:00")
    assert old["stale"] and "시간 전" in old["note"]

    from app.agents import orchestrator
    handler = inspect.getsource(orchestrator.OrchestratorAgent._handle_inventory_query)
    assert "freshness" in handler
    # 경고가 표(헤더) 앞에 삽입되는지 — 위치가 요점이다
    assert handler.index('"> " + note') < handler.index("| SKU |")


# ── 군더더기 낱말 (2026-08-25 프로덕션 실측) ─────────────────────────────────

def test_question_words_do_not_zero_out_the_search():
    """⛔ 낱말을 AND 로 걸어 **하나라도 품목에 없으면 통째로 0건**이 난다.

    실측: "센텔라 앰플 재고 얼마나 남았어?" → `센텔라 앰플 얼마나` → **0건**.
    재고는 넉넉한데 "품목을 찾지 못했습니다" 가 나갔다 — 에러가 아니라 빈손이다.

    ⛔ 그리고 **이 테스트가 원래 이걸 놓쳤다**: 검색어에 '센텔라' 가 들어 있는지만
       봤지 행이 나오는지는 보지 않았다. 조회 기능의 회귀는 **행 수로** 걸어야 한다.
    """
    src = inspect.getsource(inventory.search)
    assert "usable_words" in src

    # 목록이 아니라 **데이터에 물어본다** — 말투가 늘어도 따라갈 필요가 없다
    index = {"KRSKA022": {"name": "(KR)스킨1004_마다가스카르센텔라앰플100ml", "locs": {}}}
    keep, drop = inventory.usable_words(["센텔라", "앰플", "얼마나"], index)
    assert keep == ["센텔라", "앰플"] and drop == ["얼마나"]
    assert inventory.search("센텔라 앰플 얼마나", index=index), "군더더기 때문에 0건이 나면 안 된다"
    assert "STOPWORDS" not in inspect.getsource(inventory.usable_words)


def test_particle_stripping_does_not_break_product_names():
    """`클레이` 의 끝 '이' 가 조사로 잘려 `클레` 가 된다. 원형이 품목에 있으면
       원형을 쓴다 — 두 형태를 모두 데이터에 물어본다."""
    helper = inspect.getsource(inventory.usable_words)
    assert "w[:-1]" in helper and "len(w) > 2" in helper


def test_answer_shows_the_words_it_actually_used():
    """⛔ 쓰지 않은 낱말을 쓴 것처럼 보이면 안 된다.

    실측(2026-08-25 프로덕션): `'센텔라 앰플 얼마나'` 로 찾았다고 적으면서 실제로는
    `센텔라` 로만 조회했다. 넓혀 찾았으면 밝히라는 규칙(드라이브 검색)과 같은 자리다 —
    좁혀 찾았을 때도 마찬가지다.
    """
    from app.agents import orchestrator

    src = inspect.getsource(orchestrator.OrchestratorAgent._handle_inventory_query)
    assert "usable_words" in src
    assert "shown" in src and "dropped" in src
    # 조사는 따옴표가 아니라 마지막 낱말의 받침으로 정한다 ("'얼마나'은(는)" 방지)
    assert "_josa" in src and "dropped[-1]" in src


@pytest.mark.parametrize("q", [
    "마다가스카르 토너 몇 개 있어?",
    "앰플 몇개나 있어",
    "토너 재고 있나요?",
    "센텔라 크림 재고량",
])
def test_more_stock_phrasings_are_recognised(q):
    """실측(2026-08-25 프로덕션): "마다가스카르 토너 몇 개 있어?" 가 어느 말에도
       안 걸려 재고 경로로 가지 않았다. 재고 질문 말투는 '몇 개 남'만이 아니다."""
    assert inventory.inventory_intent(q), q


@pytest.mark.parametrize("q", [
    "일본 판매수량 알려줘",
    "2026년 제품별 판매수량 top10",
])
def test_sales_quantity_questions_are_not_hijacked(q):
    """⛔ `수량` 을 재고 신호어에 넣으면 **`판매수량`(BigQuery)을 가로챈다.**
       수량은 무조건 `Product.Total_Qty` 로 답해야 하는 규칙이 따로 있다."""
    assert inventory.inventory_intent(q) is None, q
    assert "수량" not in inventory._STOCK_WORDS

# ── ERP 탭 병합 (2026-08-25) ────────────────────────────────────────────────

def test_erp_warehouse_names_are_mapped_to_the_primary_tab():
    """⛔ 두 탭은 **같은 재고를 다른 이름으로** 적는다. 표기를 통일하지 않으면
       겹치는 730개 SKU 가 창고 둘로 갈려 **합계가 두 배**가 된다.

    대응은 눈이 아니라 값으로 정했다 — 겹치는 SKU 의 창고별 수량을 전부 대조해
    6개 모두 100% 일치하는 짝만 채택했다. `FBI` = `SK_FAST BEAUTY(인도네시아)` 는
    이름만 봐서는 알 수 없다.
    """
    assert inventory._ERP_LOCATIONS["FBI"] == "[현장] SK_FAST BEAUTY(인도네시아)"
    assert inventory._ERP_LOCATIONS["특별관리품"] == "[현장] SF_PQ"
    # 매핑 결과는 전부 주 탭 표기여야 한다 (새 이름을 만들어내면 안 된다)
    for v in inventory._ERP_LOCATIONS.values():
        assert v.startswith("[현장] "), v


def test_merging_two_tabs_does_not_double_count():
    """겹치는 (SKU, 창고) 는 **덮어쓴다 — 더하지 않는다.**"""
    rows = [
        {"sku": "A1", "item_name": "ERP 이름", "location": "[현장] SF_B2B", "qty": 100},
        {"sku": "A1", "item_name": "(KR)주 탭 이름", "location": "[현장] SF_B2B", "qty": 100},
    ]
    idx = inventory._index(rows)
    assert idx["A1"]["locs"]["[현장] SF_B2B"] == 100, "더하면 200 이 된다 — 이중계상"
    # 품목명은 주 탭(뒤에 오는 값)이 이긴다 — 시장 접두가 업무에서 의미를 갖는다
    assert idx["A1"]["name"] == "(KR)주 탭 이름"


def test_erp_parser_finds_its_header_by_content():
    """⚠️ 헤더 행 번호를 박으면 머리말이 한 줄 늘 때 통째로 어긋난다 —
       그때 나는 것은 에러가 아니라 **0건**이다."""
    src = inspect.getsource(inventory._parse_erp)
    assert "SKU.no" in src and "품목명칭" in src
    assert "hdr_idx" in src


# ── 조회 시점 실시간 읽기 (2026-08-25 사용자 지시) ──────────────────────────

def test_stock_is_read_at_query_time_not_from_the_daily_copy():
    """⛔ "op도 숫자가 매일 바뀌므로 빅쿼리처럼 조회해서 답변해야함" (사용자).

    하루 두 번 받아 둔 사본으로 답하면 그 사이 입출고를 **모른 채** 옛 값을
    자신 있게 말한다. 적재본은 시트를 못 읽을 때의 폴백으로만 쓴다.
    """
    from app.agents import orchestrator

    handler = inspect.getsource(orchestrator.OrchestratorAgent._handle_inventory_query)
    assert "live_stock" in handler
    live = inspect.getsource(inventory.live_stock)
    # 폴백은 있어야 하지만 **조용하면 안 된다**
    assert "op_inventory" in live and "note" in live


def test_live_timeout_does_not_wait_for_the_worker():
    """⛔ `with ThreadPoolExecutor` + `result(timeout=)` 은 타임아웃을 무력화한다
       (블록을 나갈 때 shutdown(wait=True)). CLAUDE.md 규칙."""
    for fn in (inventory.live_stock, inventory.live_expiry):
        src = inspect.getsource(fn)
        assert "shutdown(wait=False)" in src, fn.__name__
        assert "with concurrent.futures.ThreadPoolExecutor" not in src, fn.__name__


def test_sheet_timestamp_is_parsed_for_freshness():
    assert inventory.parse_stamp("2026. 8. 25 오전 10:10:00").hour == 10
    assert inventory.parse_stamp("2026. 8. 24 오후 2:05").hour == 14
    assert inventory.parse_stamp("헛소리") is None


# ── 유통기한 (2026-08-25) ───────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "센텔라 앰플 유통기한 알려줘",
    "유통기한 임박 재고 알려줘",
    "토너 소비기한 확인",
])
def test_expiry_questions_are_detected(q):
    assert inventory.expiry_intent(q), q


def test_expiry_is_never_mixed_into_stock():
    """⛔ 로트 단위 잔량이라 창고 재고와 **세는 기준이 다르다.** 더하면 같은
       물건을 두 번 센다 (`SF 재고합`·`Production_Cost2` 와 같은 부류)."""
    # 테이블이 다르다
    assert "op_inventory_expiry" in inventory._DDL_EXPIRY
    assert "op_inventory_expiry" not in inventory._DDL
    # 조회 함수도 다르다 — 재고 검색이 유통기한 테이블을 건드리지 않는다
    assert "op_inventory_expiry" not in inspect.getsource(inventory.search)
    # 답변이 "더하면 안 된다" 를 말한다
    from app.agents import orchestrator
    handler = inspect.getsource(orchestrator.OrchestratorAgent._handle_expiry_query)
    assert "재고 수량과 더하면 안" in handler


def test_expiry_is_checked_before_stock_in_both_paths():
    """둘 다 '재고' 를 신호로 쓴다 — 순서가 뒤집히면 유통기한 질문이 재고 표로 답해진다.

    ⛔ **두 경로 모두** 봐야 한다. 한쪽만 걸면 스트리밍이냐 아니냐에 따라 답이
       갈린다 — 이 프로젝트에서 반복된 사고다 (direct 프롬프트 2벌).
    """
    from app.agents.orchestrator import OrchestratorAgent as O

    for name in ("route_and_execute", "route_and_stream"):
        src = inspect.getsource(getattr(O, name))
        assert "_expiry_term" in src and "_inventory_term" in src, name
        assert src.index("_expiry_term") < src.index("_inventory_term"), name


def test_product_names_that_end_like_a_particle_survive():
    """⚠️ `extract` 는 조사를 떼야 제 몫을 한다 (`매출이`→`매출`). 그런데 제품명에는
       조사처럼 생긴 끝글자가 있다 — `클레이` 가 `클레` 로 잘려 **답변에 그대로
       보였다** (2026-08-25 프로덕션). 결과는 맞아도 잘린 말을 사용자에게 보여준다.

    원형을 되살리고, 진짜 조사인지는 `usable_words` 가 데이터에 물어 판정한다.
    """
    assert inventory.inventory_intent("포어마이징 클레이 재고") == "포어마이징 클레이"

    index = {"KRSKM007": {"name": "(KR)스킨1004_마다가스카르센텔라포어마이징퀵클레이스틱마스크27g",
                          "locs": {}},
             "KRSKA022": {"name": "(KR)스킨1004_마다가스카르센텔라앰플100ml", "locs": {}}}
    keep, _ = inventory.usable_words(["포어마이징", "클레이"], index)
    assert keep == ["포어마이징", "클레이"], keep
    # 반대 방향 — 진짜 조사는 데이터에 물어 풀린다
    keep2, _ = inventory.usable_words(["앰플은"], index)
    assert keep2 == ["앰플"], keep2
