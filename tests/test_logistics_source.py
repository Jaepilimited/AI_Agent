# -*- coding: utf-8 -*-
"""수출 물류(LOG · `Export_control.export_logistics`) 연동 회귀 — 2026-09-03.

**왜 이 파일이 있나**: 새 데이터소스는 하나만 빠뜨려도 **에러가 아니라 반쪽만
동작한다** (CLAUDE.md "새 데이터소스를 붙일 때 건드릴 7곳"). 실제로 이 테이블은
반쪽 동작이 특히 잘 숨는다:

  - `order_team` 이 **세 번째 값 체계**다 — `order_team='B2B1'` 은 2건을 준다
    (실제 1,262건). 0건이 아니라 **그럴듯한 소수**라 티가 안 난다
  - `amount` 가 **통화 혼재**다 (USD·EUR·KRW·JPY·CNY + 통화 미상 384건).
    다 더하면 숫자는 나오는데 뜻이 없다
  - 옛 컬럼 사본이 20개 있고 **값이 전 행 동일**하다 — 잘못 골라도 결과가 나온다

수치는 전부 2026-09-03 프로덕션 BigQuery 실측이다 (2,527행).
"""
import re
from pathlib import Path
from tests._answer_paths import assert_every_answer_path_has

import pytest

ROOT = Path(__file__).resolve().parent.parent
TABLE = "skin1004-319714.Export_control.export_logistics"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── 1) `order_team` 표기 흩어짐 — 코드가 보증한다 ───────────────────────────

# 2026-09-03 실측 DISTINCT `order_team` 전체(30종). 팀에 귀속되는 값은 전부
# `_LOG_TEAM_VARIANTS` 에 있어야 한다 — 하나라도 빠지면 그 팀 실적이 조용히 준다.
MEASURED_TEAM_VALUES = {
    "영업1": 934, "영업2": 819, "영업1팀": 328, "영업2팀": 201,
    "GMWM": 65, "JBT": 27, "GMWE": 25, "CBT": 21,
    "서구권이커머스팀": 19, "일본사업팀": 13, "서구권마케팅팀": 11,
    "GME2": 10, "동남아시아1팀": 10, "동남아시아2팀": 9,
    "GM WEST MKT": 5, "중국사업팀": 5, "GM EAST2": 4,
    "GM JBT": 3, "브랜드커뮤니케이션팀_플래그십": 2, "B2B1": 2,
    "BCM_플래그십 파트": 1, "BCM_플래그십": 1, "GM EAST1": 1, "BCM_플": 1,
    "GM KBT": 1, "GM WEST ECOMM": 1, "GME1": 1, "GM CBT": 1,
}
# 어느 한 팀으로 귀속시킬 수 없는 값 — CASE 의 `미분류` 로 보여야 한다
UNRESOLVABLE = {"서구권이커머스팀, 서구권마케팅팀", ""}


def test_every_measured_team_spelling_is_mapped():
    """실측 표기가 하나라도 빠지면 그 팀 건수가 조용히 준다."""
    from app.agents.sql_agent import _LOG_TEAM_VARIANTS

    mapped = {v for vals in _LOG_TEAM_VARIANTS.values() for v in vals}
    missing = sorted(set(MEASURED_TEAM_VALUES) - mapped)
    assert not missing, f"매핑에 없는 실측 표기: {missing}"


def test_team_code_alone_would_lose_almost_everything():
    """⛔ 이것이 이 파일의 존재 이유다 — `= 'B2B1'` 은 2건, 실제는 1,262건."""
    from app.agents.sql_agent import _LOG_TEAM_VARIANTS

    naive = MEASURED_TEAM_VALUES.get("B2B1", 0)
    expanded = sum(MEASURED_TEAM_VALUES.get(v, 0)
                   for v in _LOG_TEAM_VARIANTS["B2B1"])
    assert naive == 2 and expanded == 1264, (naive, expanded)


@pytest.mark.parametrize("sql,expect_in", [
    ("SELECT * FROM t WHERE order_team = 'B2B1'", ["'영업1'", "'영업1팀'", "'B2B1'"]),
    ("SELECT * FROM t WHERE order_team='영업1팀'", ["'영업1'", "'B2B1'"]),
    ("SELECT * FROM t WHERE order_team = '영업1'", ["'영업1팀'"]),
    ("SELECT * FROM t WHERE order_team IN ('서구권마케팅팀')", ["'GMWM'", "'GM WEST MKT'"]),
    ("SELECT * FROM t WHERE order_team = '일본사업팀'", ["'JBT'", "'GM JBT'"]),
])
def test_localizer_expands_team_literals(sql, expect_in):
    from app.agents.sql_agent import _localize_logistics_literals as F

    out = F(sql)
    for frag in expect_in:
        assert frag in out, (frag, out)


def test_localizer_keeps_sql_valid_without_spaces():
    """`order_team='영업1팀'` 은 등호에 공백이 없다 — 이어 붙이면 SQL 이 깨진다."""
    from app.agents.sql_agent import _localize_logistics_literals as F

    out = F("SELECT * FROM t WHERE order_team='영업1팀' AND x=1")
    assert "order_teamIN" not in out
    assert re.search(r"order_team\s+IN\s*\(", out)


def test_localizer_negation_becomes_not_in():
    from app.agents.sql_agent import _localize_logistics_literals as F

    out = F("SELECT * FROM t WHERE order_team != '영업2팀'")
    assert "NOT IN (" in out and "'영업2'" in out


def test_localizer_leaves_unknown_and_other_columns_alone():
    """⚠️ 모르는 값을 지어내지 않는다. 국가는 매출과 **같은 한글명**이라 손대지 않는다."""
    from app.agents.sql_agent import _localize_logistics_literals as F

    for sql in ["SELECT * FROM t WHERE order_team = '알수없는팀'",
                "SELECT * FROM t WHERE country = '미국'",
                "SELECT order_team, COUNT(*) FROM t GROUP BY order_team"]:
        assert F(sql) == sql


def test_team_case_keeps_an_else_bucket():
    """⛔ `ELSE '미분류'` 를 지우면 새 표기·두 팀이 함께 적힌 값이 조용히 사라진다."""
    from app.agents.sql_agent import build_logistics_team_section

    section = build_logistics_team_section()
    assert "ELSE '미분류'" in section
    for code in ("B2B1", "WEST_Ecomm", "BCM"):
        assert code in section


def test_team_section_is_generated_not_hand_written():
    """프롬프트가 같은 표를 또 적으면 두 벌이 갈린다 — 자리표시자여야 한다."""
    prompt = _read("prompts/sql_generator.txt")
    assert "{{LOG_TEAM_SECTION}}" in prompt
    assert prompt.count("{{LOG_TEAM_SECTION}}") == 1
    # 코드가 만든 CASE 를 프롬프트에도 손으로 적어두지 않았는가
    assert "WHEN order_team IN ('GMWM'" not in prompt


# ── 2) 7곳 배선 ─────────────────────────────────────────────────────────────

def test_table_is_whitelisted():
    """⚠️ 빠지면 `@@물류` 로는 되는데 **일반 질문만** '허용되지 않은 테이블' 로 막힌다."""
    from app.config import get_settings

    assert TABLE in get_settings().allowed_tables


def test_source_map_and_scoping():
    from app.agents.sql_agent import _allowed_tables_from_sources, _source_table_map
    from app.config import get_settings

    from app.core.logistics_fx import FX_TABLE

    # ⚠️ `@@물류` 는 환율표를 함께 연다 — 한화 환산 SQL 이 그것을 조인하기 때문이다.
    #    빠지면 "허용되지 않은 테이블" 로 막히는데, 사용자에게는 조회 실패로 보인다.
    assert _source_table_map(get_settings())["물류"] == [TABLE, FX_TABLE]
    assert _allowed_tables_from_sources(["물류"]) == {TABLE, FX_TABLE}


def test_lazy_schema_keywords_registered():
    from app.agents.sql_agent import MARKETING_TABLES

    entry = next((t for t in MARKETING_TABLES if t[0] == TABLE), None)
    assert entry, "MARKETING_TABLES 에 없으면 스키마가 안 실려 LLM 이 컬럼을 지어낸다"
    flat = [k for k in entry[2] if isinstance(k, str)]
    assert "물류" in flat and "포워더" in flat


def test_eta_etd_are_not_bare_keywords():
    """⛔ `eta` 는 **`meta`·`retail` 안에 들어 있다.** 낱말 경계 방어가 라틴 문자에는
    듣지 않으므로(`textmatch.standalone` 은 앞 글자가 한글일 때만 본다) 맨 낱말로
    두면 메타 광고 질문마다 이 스키마가 함께 실린다."""
    from app.agents.orchestrator import OrchestratorAgent
    from app.agents.sql_agent import MARKETING_TABLES

    entry = next(t for t in MARKETING_TABLES if t[0] == TABLE)
    flat = [k for k in entry[2] if isinstance(k, str)]
    assert "eta" not in flat and "etd" not in flat
    assert "eta" not in OrchestratorAgent._DATA_KEYWORDS
    assert "etd" not in OrchestratorAgent._DATA_KEYWORDS


def test_prompt_section_exists_and_sits_before_fi():
    """⚠️ FI 섹션 **뒤**에 두면 FI 마스킹 정규식에 함께 잘려 나간다."""
    prompt = _read("prompts/sql_generator.txt")
    i = prompt.find("## 테이블 16: export_logistics")
    j = prompt.find("## 테이블 14: FI_LLM_Flat")
    assert i > 0 and j > 0 and i < j


def test_fi_masking_keeps_the_logistics_section():
    from app.agents.sql_agent import _load_prompt

    masked = _load_prompt("sql_generator.txt", can_view_fi=False)
    assert "테이블 16: export_logistics" in masked
    assert "FI_LLM_Flat" not in masked
    assert "{{LOG_TEAM_SECTION}}" not in masked, "자리표시자가 그대로 나가면 안 된다"


def test_system_status_and_freshness_watch():
    """System Status 카드와 신선도 감시는 **각각** 등록해야 한다."""
    from app.core.safety import _MONITORED_TABLES

    assert _MONITORED_TABLES["물류"] == ("Export_control", "export_logistics")
    safety_src = _read("app/core/safety.py")
    assert '"물류": "수출 물류' in safety_src, "_mkt_tables 에 없으면 화면에 안 뜬다"


def test_schema_watch_covers_the_dataset():
    from app.core.schema_watch import WATCHED_DATASETS

    assert "Export_control" in WATCHED_DATASETS


def test_registry_entry_and_aliases():
    from app.agents.orchestrator import OrchestratorAgent as O

    entry = next(e for e in O._DB_REGISTRY if e["key"] == "물류")
    assert entry["route"] == "bigquery" and entry["group"] == "물류 데이터"
    for q in ("@@물류 건수", "@@LOG 건수", "@@선적 건수", "@@logistics 건수", "@@출고 건수"):
        got, clean = O.parse_db_prefix(q)
        assert got and got["key"] == "물류", q
        assert clean.strip() == "건수", (q, clean)


def test_frontend_knows_the_new_group():
    """⛔ 서버에 새 그룹을 만들고 `GROUP_BY_NAME` 에 안 넣으면 그 그룹의 소스가
    **화면에서 통째로 사라진다** (에러 없이)."""
    js = _read("app/frontend/chat.js")
    assert '"물류 데이터": "logistics"' in js
    assert 'id: "logistics"' in js
    assert '"물류":' in js, "SERVICE_ICONS 에 없으면 칩 아이콘이 빈다"


def test_at_source_parity_still_holds():
    from app.core.static_checks import at_source_parity

    ok, msg = at_source_parity()
    assert ok, msg


# ── 3) 조용한 오답을 부르는 데이터 사실이 프롬프트에 있는가 ──────────────────

def test_prompt_no_longer_claims_logistics_data_is_missing():
    """예전 프롬프트는 '물류/발주 데이터는 어떤 테이블에도 없다' 고 단언했다."""
    prompt = _read("prompts/sql_generator.txt")
    assert "재고/현재고/입출고/창고/물류/발주 데이터는 어떤 테이블에도 없다" not in prompt
    assert "현재고" in prompt, "창고 재고가 BigQuery 에 없다는 사실은 남아 있어야 한다"


@pytest.mark.parametrize("must_contain", [
    "WHERE NOT is_deleted",          # 삭제분 40건
    "GROUP BY unit",                 # 통화 혼재 — 다 더하면 뜻이 없다
    "cost_total_krw",                # 부분합과 안 맞는다
    "combined_shipment_id",          # 합적 — 행 수 ≠ 선적 건수
    "local_warehouse_received_date", # 4.4% — 모수를 밝혀야 한다
    "op_manager",                    # 같은 사람이 두 표기
    "unit_price",                    # 이름과 달리 통화 코드다
])
def test_prompt_documents_the_silent_traps(must_contain):
    assert must_contain in _read("prompts/sql_generator.txt")


def test_prompt_lists_the_mirror_columns_to_avoid():
    """옛 사본 컬럼은 값이 같아 **잘못 골라도 결과가 나온다** — 그래서 더 위험하다."""
    prompt = _read("prompts/sql_generator.txt")
    section = prompt[prompt.find("## 테이블 16: export_logistics"):
                     prompt.find("## 테이블 14: FI_LLM_Flat")]
    for mirror in ("sb_no", "order_no", "cnee", "bl_no", "log_owner", "op_owner",
                   "sales_team", "transport_method", "plt", "carton",
                   "departure_date", "work_complete_date", "unit_price"):
        assert mirror in section, mirror


def test_value_lists_are_measured_not_handwritten():
    """⛔ 손으로 적은 값 목록은 반드시 낡고, 낡으면 **에러가 아니라 0건**이다."""
    from app.core.value_lists import REGISTRY

    for name in ("LogTeam", "LogIncoterms", "LogTransport"):
        assert name in REGISTRY
        assert REGISTRY[name][0] == TABLE
    prompt = _read("prompts/sql_generator.txt")
    for name in ("LogTeam", "LogIncoterms", "LogTransport"):
        assert "{{VALUES:%s}}" % name in prompt
    # 고카디널리티 컬럼(포워더 390종·거래처 536곳)은 넣지 않는다
    assert not any(REGISTRY[n][1] in ("forwarder", "consignee") for n in REGISTRY)


# ── 4) 라우팅 — 물류 질문이 direct 로 새면 "그런 데이터 없다" 가 나간다 ──────

@pytest.mark.parametrize("question", [
    "이번달 국가별 수출 물류 건수 알려줘",
    "포워더별 선적 건수 top 10",
    "해상운송 리드타임 평균 얼마야",
    "8월 출고일 기준 국가별 팔레트 수",
])
def test_logistics_questions_reach_bigquery(question):
    from app.agents.orchestrator import OrchestratorAgent

    o = OrchestratorAgent.__new__(OrchestratorAgent)
    route, _ = OrchestratorAgent._keyword_classify_ex(o, question)
    assert route == "bigquery", (question, route)


# ⚠️ 반대 방향 — 물류 낱말을 넣었다고 OP 재고 경로를 뺏으면 안 된다.
#    재고 판정은 라우터가 아니라 `inventory_intent()` 가 먼저 한다.
@pytest.mark.parametrize("question", [
    "센텔라 앰플 재고 알려줘",
    "재고 얼마나 남았어",
])
def test_inventory_intent_still_fires_for_stock_questions(question):
    from app.core.inventory import inventory_intent

    assert inventory_intent(question), question


@pytest.mark.parametrize("question", [
    "포워더별 선적 건수 top 10",
    "8월 출고일 기준 국가별 팔레트 수",
    "해상운송 리드타임 평균 얼마야",
])
def test_logistics_questions_do_not_hijack_the_inventory_route(question):
    """⛔ 두 도메인이 '출고'·'물류' 를 함께 쓴다 — 물류 질문이 OP 재고로 새면
    창고 수량 표가 나가고 **질문에 답하지 않은 답변**이 된다."""
    from app.core.inventory import inventory_intent

    assert inventory_intent(question) is None, question


# ── 5) 통화 혼재 — 코드가 합계를 막는다 (2026-09-03 프로덕션 실측 사고) ──────

MIXED_CURRENCY_ROWS = [
    {"currency": "USD", "shipments": 1142, "amount": 90992472.17},
    {"currency": "통화미상", "shipments": 348, "amount": 8556818947.05},
    {"currency": "EUR", "shipments": 69, "amount": 6866880.37},
    {"currency": "KRW", "shipments": 40, "amount": 17449032192.55},
    {"currency": "JPY", "shipments": 17, "amount": 571831029.0},
    {"currency": "CNY", "shipments": 7, "amount": 2291840.0},
]


def test_mixed_currency_axis_is_detected():
    from app.agents.sql_agent import _mixed_currency_axis

    assert _mixed_currency_axis(MIXED_CURRENCY_ROWS) == "currency"


def test_totals_block_refuses_to_add_across_currencies():
    """⛔ 실제로 나간 답변: USD+KRW+JPY 를 더해 **26,677,833,361** 을 찍었고
    요약이 그걸 받아 '총 수출 금액 약 266.8억원' 이라고 썼다."""
    from app.agents.sql_agent import _build_table_totals_markdown

    block = _build_table_totals_markdown(MIXED_CURRENCY_ROWS)
    assert "26,677,833,361" not in block
    assert "통화가 여러 개" in block, "뺐으면 왜 뺐는지 밝혀야 한다 (조용히 사라지면 안 된다)"


def test_amount_note_tells_the_model_not_to_total():
    from app.agents.sql_agent import _amount_note

    note = _amount_note(MIXED_CURRENCY_ROWS)
    assert "합치지 마라" in note and "환율" in note


@pytest.mark.parametrize("rows", [
    # 통화가 하나면 예전처럼 합계를 낸다
    [{"currency": "USD", "amount": 10.0}, {"currency": "USD", "amount": 20.0}],
    # `unit` 이 통화가 아닌 표를 건드리면 안 된다
    [{"unit": "박스", "amount": 100.0}, {"unit": "개", "amount": 200.0}],
    # 평범한 국가별 매출
    [{"country": "미국", "revenue": 100.0}, {"country": "일본", "revenue": 50.0}],
])
def test_normal_tables_still_get_their_totals(rows):
    """⚠️ 과하게 막으면 멀쩡한 표의 합계가 사라진다 — 그것도 조용한 실패다."""
    from app.agents.sql_agent import _build_table_totals_markdown, _mixed_currency_axis

    assert _mixed_currency_axis(rows) is None
    assert "지표 | 합계" in _build_table_totals_markdown(rows)


# ── 6) OP 재고와의 경계 (2026-09-03 사용자 질문: "op 시트와 겹치는 것 같다") ──
#
# 실측 결론: **중복 적재가 아니다.** 조인할 키조차 없다.
#   - `export_logistics` 에 SKU·제품 컬럼이 **0개** (OP 는 SKU 가 키다)
#   - OP 시트 40탭 어디에도 ETD/ETA/BL/인코텀즈/포워더/수출신고/CNEE 가 없다
#   - OP 의 `발주현황/입고예정` 탭은 **제조업체 → 창고(inbound)** 발주고,
#     LOG 의 `order_date` 는 **수출 주문(outbound)** 발주다. 방향이 반대다
# 겹치는 것은 **같은 사람·같은 현지창고**뿐이다 (담당자 5명, 미국·인도네시아 창고).
# ⟶ 그래서 진짜 위험은 중복이 아니라 **질문이 엇갈리는 것**이다. 아래가 그것을 지킨다.

def test_logistics_table_has_no_product_axis():
    """⛔ 제품별 수출 물량을 이 테이블로 답할 수 없다 — SKU 가 없다.
    LLM 이 있는 줄 알고 지어내지 않도록 프롬프트가 못 박고 있어야 한다."""
    prompt = _read("prompts/sql_generator.txt")
    section = prompt[prompt.find("## 테이블 16: export_logistics"):
                     prompt.find("## 테이블 14: FI_LLM_Flat")]
    for absent in ("SKU", "제품별"):
        assert absent in section, f"{absent} 가 없다는 사실을 적어야 한다"


@pytest.mark.parametrize("question", [
    "센텔라 앰플 재고 얼마나 남았어",
    "인도네시아 창고에 재고 얼마나 있어",
])
def test_stock_questions_go_to_op_not_logistics(question):
    from app.core.inventory import inventory_intent

    assert inventory_intent(question), question


def test_expiry_questions_still_go_to_op():
    """⚠️ 유통기한은 재고와 신호어가 겹쳐 `expiry_intent` 가 **먼저** 판정한다."""
    from app.core.inventory import expiry_intent

    assert expiry_intent("유통기한 임박한 품목 알려줘")


@pytest.mark.parametrize("question", [
    "인도네시아로 나간 수출 물류 건수 알려줘",
    "미국 현지창고 입고완료일 기준 건수 알려줘",
    "포워더별 선적 건수 top 10",
])
def test_shipment_questions_do_not_go_to_op(question):
    """⚠️ 두 도메인이 '출고'·'물류'·'입고' 를 함께 쓴다. 수출 질문이 OP 로 새면
    **창고 수량 표**가 나가고 질문에 답하지 않은 답변이 된다."""
    from app.core.inventory import inventory_intent

    assert inventory_intent(question) is None, question


# ── 7) 개인 작업지는 학습하지 않는다 (2026-09-03 사용자 지시) ────────────────
#
# OP 시트에는 탭이 40개 있고 상당수가 담당자 개인 작업지다. 거기에 수출 발주
# 계산(발주수량·박스수량·예상무게·HSCODE)이 있어 `@@물류` 와 이어 보고 싶어지지만,
# **적재하지 않는다** — `제품정보` 탭에 "사용 후 꼭 지워주세요!!" 라고 적혀 있는
# 임시 칸이고, 탭마다 형식이 다르다. 적재하면 조용히 낡거나 조용히 비는 데이터가 된다.

def test_only_three_op_tabs_are_ingested():
    from app.core.inventory import ALLOWED_TABS, SHEET_TAB, TAB_ERP, TAB_EXPIRY

    assert ALLOWED_TABS == frozenset({SHEET_TAB, TAB_ERP, TAB_EXPIRY})
    assert len(ALLOWED_TABS) == 3


@pytest.mark.parametrize("tab", [
    "나영", "나영(ETC)", "나영(FBI)", "민재", "민재(US)", "훈", "Yoona",
    "어진", "다운", "윤아_재고 확인용", "제품정보", "발주현황/입고예정",
    "미결수량/2개월치 물량", "SKU 변환기", "바코드-ERP",
])
def test_personal_worksheet_tabs_are_never_read(tab):
    """⛔ 코드에 이름조차 두지 않는다 — 있으면 다음 사람이 '읽어도 되나 보다' 한다."""
    from app.core.inventory import ALLOWED_TABS

    assert tab not in ALLOWED_TABS
    src = _read("app/core/inventory.py")
    body = src[src.index("ALLOWED_TABS = frozenset"):]
    assert f'"{tab}"' not in body, f"{tab} 을 읽는 코드가 생겼다"


def test_fetch_refuses_a_tab_outside_the_allowlist():
    """⚠️ `_RANGES[tab]` 의 KeyError 에 기대지 않는다 — **왜** 막혔는지가 보여야 한다."""
    from app.core.inventory import _fetch

    with pytest.raises(ValueError, match="개인 작업지"):
        _fetch(None, "민재(US)")


# ── 8) 물류 수량 ↔ 판매수량 ↔ OP 재고 — 숫자가 겹치는 지점 (2026-09-03 실측) ──
#
# 사용자 질문("숫자는 겹치지 않아?")에 실측으로 답한 결과, 겹치는 곳이 둘 있었다:
#
#  1. **같은 물건을 두 번 센다 (흐름 vs 잔량)** — 인도네시아로 2026년에
#     6,001,001ea 를 실어 보냈고(물류), 그중 1,674,503ea 가 아직 현지창고에
#     남아 있다(OP). 더하면 이중계상이다. 다만 흐름+잔량은 애초에 더할 수 없는
#     연산이고, 두 데이터는 저장소(BigQuery ↔ MariaDB)도 경로도 달라
#     **한 SQL 로 섞일 수 없다.** 위험은 답변 서술에 있다
#  2. **수량 이름이 겹친다** — 물류 `quantity_ea`(선적 수량)와
#     `Product.Total_Qty`(판매수량)는 같은 나라·같은 해에도 값이 다르다
#     (2026 인도네시아 6,001,001 vs 6,545,865). "수량" 을 어느 쪽으로 읽느냐로
#     답이 갈린다 — 그래서 프롬프트가 못 박는다

def test_prompt_separates_shipment_qty_from_sales_qty():
    """⛔ 수량 = 무조건 `Product.Total_Qty` 라는 대원칙이 물류 때문에 흔들리면 안 된다."""
    prompt = _read("prompts/sql_generator.txt")
    section = prompt[prompt.find("## 테이블 16: export_logistics"):
                     prompt.find("## 테이블 14: FI_LLM_Flat")]
    assert "판매수량이 아니다" in section
    assert "Product.Total_Qty" in section
    assert "수출 선적 수량" in section, "답변이 어느 수량인지 밝히게 해야 한다"


def test_prompt_warns_about_the_quantity_outlier():
    prompt = _read("prompts/sql_generator.txt")
    assert "1,332,166" in prompt, "한 선적의 실측 최대를 적어야 판단 기준이 생긴다"
    assert "3,492배" in prompt


def test_quantity_outlier_selfcheck_is_registered():
    """⛔ 수량 칸에 주문번호가 들어간 행이 **매일 보여야** 한다 — 조용하면 또 나간다."""
    from app.core.self_check import CHECKS

    ids = {c.id for c in CHECKS}
    assert "logistics_quantity_outlier" in ids
    c = next(c for c in CHECKS if c.id == "logistics_quantity_outlier")
    assert c.category == "datasource"
    assert c.repair is None, "데이터 수정은 자동화하지 않는다 — 원본(물류관리 시스템)이 고쳐야 한다"


def test_outlier_thresholds_keep_a_wide_margin():
    """⚠️ 경보가 매일 뜨면 아무도 안 읽는다 — 실측 최대에서 크게 떨어뜨렸는지 본다.

    실측: 한 선적 최대 1,332,166ea (임계 1,000만 = 7.5배 여유),
          ea/kg 최대 392 · 중앙값 6.7 (임계 10,000 = 25배 여유).
    """
    from app.core import logistics_quality as LQ

    assert LQ._MAX_PLAUSIBLE_EA == 10_000_000        # 실측 최대 1,332,166 의 7.5배
    assert LQ._MAX_PLAUSIBLE_EA_PER_KG == 10_000     # 실측 최대 392 의 25배


# ── 9) 수량 이상치는 **코드가 공시한다** (2026-09-03 — 프롬프트로는 못 막았다) ──
#
# ⛔ 프롬프트에 "억 단위가 나오면 이상치라고 적어라" 를 넣고 배포한 **뒤에도**
#    프로덕션이 이렇게 답했다:
#      "2026년 코스타리카 대상 수출 물류 수량은 총 202,602,265,193개입니다.
#       압도적인 수출 규모: 주요 수출 대상국으로서의 입지를 …"
#    프롬프트는 확률이고 보증은 코드다.

LOG_QTY_SQL = ("SELECT SUM(quantity_ea) FROM "
               "`skin1004-319714.Export_control.export_logistics` WHERE NOT is_deleted")


def test_notice_fires_only_on_logistics_quantity_queries():
    from app.core import logistics_quality as LQ

    assert LQ.touches_logistics_quantity(LOG_QTY_SQL)
    # 건수만 세는 질의 · 다른 테이블은 건드리지 않는다
    assert not LQ.touches_logistics_quantity(
        "SELECT COUNT(*) FROM `skin1004-319714.Export_control.export_logistics`")
    assert not LQ.touches_logistics_quantity(
        "SELECT SUM(Total_Qty) FROM `skin1004-319714.Sales_Integration.Product`")


def test_notice_is_silent_when_the_data_is_clean(monkeypatch):
    """⚠️ **스스로 꺼져야 한다** — 원본을 고치면 공시도 사라진다.
    영원히 뜨는 경고는 곧 아무도 안 읽는다."""
    from app.core import logistics_quality as LQ

    monkeypatch.setattr(LQ, "outlier_rows", lambda force=False: [])
    assert LQ.notice_for_sql(LOG_QTY_SQL) == ""


def test_notice_states_the_fact_without_changing_the_number(monkeypatch):
    """⛔ 값을 고치거나 행을 빼지 않는다 — 조용히 바꾸면 무엇이 달라졌는지 모른다."""
    from app.core import logistics_quality as LQ

    monkeypatch.setattr(LQ, "outlier_rows", lambda force=False: [{
        "order_date": "2026-01-14", "country": "코스타리카",
        "order_number": "202601120126", "quantity_ea": 202602190028,
        "weight_kg": 1265.75}])
    note = LQ.notice_for_sql(LOG_QTY_SQL)
    assert "202,602,190,028ea" in note and "코스타리카" in note
    assert "1,332,166" in note, "무엇과 견줘 이상한지 적어야 한다"
    assert "물류관리 시스템" in note, "어디를 고쳐야 하는지 적어야 한다"
    # ⛔ **표보다 먼저 말한다** — 맨 뒤에 붙였더니 후속 질문 제안·실행된 쿼리 뒤로
    #    밀려 정작 숫자를 읽는 사람 눈에 닿지 않았다 (2026-09-03 실측)
    assert note.startswith("> ⚠️"), "답변 앞에 오므로 앞줄 공백으로 시작하면 안 된다"
    assert "아래 수량" in note, "위치가 앞이므로 '위 수량' 이라고 쓰면 거짓말이 된다"


def test_notice_comes_before_the_answer_in_both_paths():
    """⛔ 각주로 달아 두면 아무도 안 읽는다 (OP 재고 신선도 공시와 같은 규칙)."""
    src = _read("app/agents/sql_agent.py")
    # 비스트리밍: 공시가 본문 **앞에** 이어붙는다
    assert "answer = (_log_qty_notice(sql)" in src
    # 스트리밍: 답변 청크 루프보다 **먼저** yield 한다
    # ⚠️ 줄 전체를 박지 마라 — 공시가 하나 늘면(괄호로 감싸이면) 이 검사가
    #    기능과 무관하게 깨진다. 지켜야 하는 것은 **순서**뿐이다
    i = src.index("_qty_notice = ")
    j = src.index("for chunk in _stream_with_table_totals(")
    assert i < j, "공시가 답변 뒤에 나가면 화면에서 맨 아래로 밀린다"


def test_probe_failure_does_not_warn_on_healthy_answers(monkeypatch):
    """⚠️ 조회가 실패했다고 멀쩡한 답변에 경고를 붙이면 안 된다 (안전한 쪽으로 실패)."""
    from app.core import logistics_quality as LQ

    LQ._cache, LQ._cache_at = None, 0.0

    def _boom(*a, **k):
        raise RuntimeError("bq down")

    monkeypatch.setattr("app.core.bigquery.get_bigquery_client", _boom)
    assert LQ.outlier_rows(force=True) == []


def test_notice_is_wired_into_both_answer_paths():
    """⛔ 한쪽만 걸면 **경로에 따라 답이 갈린다** — 이미 겪은 사고다."""
    src = _read("app/agents/sql_agent.py")
    assert_every_answer_path_has(
        "from app.core.logistics_quality import notice_for_sql",
        "세는 단언이었다 — 경로가 늘면 뜻을 안 보고 숫자만 올리게 된다")
    assert "_log_qty_notice(sql)" in src


def test_selfcheck_and_notice_share_one_source():
    """같은 규칙을 두 번 구현하면 화면과 답변이 언젠가 어긋난다."""
    import inspect
    from app.core import self_check

    src = inspect.getsource(self_check._check_logistics_quantity_outlier)
    assert "from app.core.logistics_quality import outlier_rows" in src
    assert "SAFE_DIVIDE" not in src, "임계는 logistics_quality 한 곳에만 있어야 한다"
