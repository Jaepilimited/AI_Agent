# -*- coding: utf-8 -*-
"""수출 물류 금액 = 유상 + 무상 (붐따 #159, 전휘빈 제보).

⛔ 여기서 가장 조심할 것은 **고쳐 쓰는 범위**다. 집계 밖의 `amount` 에 별칭을
   붙이면 `GROUP BY`·`ORDER BY` 에서 SQL 이 깨지는데, 이 프로젝트에는 SQL 파서가
   없어 그것을 잡을 방법이 없다 — 그래서 확실히 안전한 자리에서만 고친다.
"""
from app.core import logistics_amount as LA

T = "`skin1004-319714.Export_control.export_logistics`"
EXPECTED = "IFNULL(total_amount, amount + IFNULL(free_amount, 0))"


# ── 고쳐 쓰는 자리 (집계 안) ────────────────────────────────────────────

def test_sum_amount_becomes_paid_plus_free():
    sql = f"SELECT unit, SUM(amount) AS amount FROM {T} WHERE NOT is_deleted GROUP BY unit"
    out = LA.fix_sql(sql)
    assert f"SUM({EXPECTED})" in out
    assert "AS amount" in out              # 바깥 별칭은 그대로 살아 있다


def test_other_aggregates_are_fixed_too():
    for fn in ("AVG", "MAX", "MIN"):
        out = LA.fix_sql(f"SELECT {fn}(amount) FROM {T}")
        assert f"{fn}({EXPECTED})" in out


def test_whitespace_inside_the_aggregate_is_tolerated():
    assert EXPECTED in LA.fix_sql(f"SELECT SUM( amount ) FROM {T}")


# ── 건드리면 안 되는 자리 ───────────────────────────────────────────────

def test_free_and_total_amount_are_never_touched():
    """⛔ `\\bamount\\b` 가 `free_amount`·`total_amount` 에 매치되지 않는 성질에
    기대고 있다 — 정규식을 손보면 여기서 걸린다."""
    sql = f"SELECT SUM(free_amount), SUM(total_amount) FROM {T}"
    assert LA.fix_sql(sql) == sql


def test_a_query_that_already_uses_total_amount_is_left_alone():
    """이미 총액을 다루고 있다 — 손대면 두 번 더하거나 뜻이 겹친다."""
    sql = f"SELECT SUM(total_amount), SUM(amount) FROM {T}"
    assert LA.fix_sql(sql) == sql


def test_a_bare_select_item_is_not_rewritten():
    """⛔ 별칭 없이 바꾸면 컬럼 이름이 사라지고, 별칭을 붙이면 GROUP BY 에서
    SQL 이 깨진다. 여기서는 고치지 않고 `notice()` 가 말한다."""
    sql = f"SELECT order_number, amount FROM {T}"
    assert LA.fix_sql(sql) == sql


def test_group_by_and_order_by_survive_untouched():
    sql = f"SELECT unit, amount FROM {T} GROUP BY unit, amount ORDER BY amount DESC"
    assert LA.fix_sql(sql) == sql


def test_other_tables_are_untouched():
    sql = "SELECT SUM(amount) FROM `p.d.some_other_table`"
    assert LA.fix_sql(sql) == sql


# ── 고치지 못한 경우에는 말한다 ─────────────────────────────────────────

def test_a_paid_only_detail_query_is_announced():
    """#159 그 질문 — 주문 한 건의 상세라 집계가 없다."""
    text = LA.notice(f"SELECT order_number, amount FROM {T} WHERE order_number = '202606010105'")
    assert "유상분만" in text
    assert "무상" in text


def test_no_notice_once_the_total_is_used():
    """고쳐 썼거나 LLM 이 제대로 썼으면 공시할 것이 없다."""
    fixed = LA.fix_sql(f"SELECT SUM(amount) AS amount FROM {T}")
    assert LA.notice(fixed) == ""


def test_no_notice_when_amount_was_not_asked():
    assert LA.notice(f"SELECT order_number, country, quantity_ea FROM {T}") == ""


def test_no_notice_for_other_tables():
    assert LA.notice("SELECT SUM(amount) FROM `p.d.other`") == ""


# ── 값 정의는 한 곳에만 ─────────────────────────────────────────────────

def test_the_expression_matches_the_prompt():
    """⛔ 프롬프트와 코드에 각자 적으면 사본이 갈린다 — 같은 식이어야 한다."""
    with open("prompts/sql_generator.txt", encoding="utf-8") as fh:
        prompt = fh.read()
    assert LA.TOTAL_EXPR in prompt


def test_the_prompt_no_longer_bans_total_amount():
    """⛔ 프롬프트가 `total_amount` 를 옛 사본으로 오인해 금지하고 있었다 —
    그게 이 붐따의 뿌리다."""
    with open("prompts/sql_generator.txt", encoding="utf-8") as fh:
        prompt = fh.read()
    assert "`total_amount` 도 쓰지 마라" not in prompt


# ── 배선 ────────────────────────────────────────────────────────────────

def _agent_src():
    with open("app/agents/sql_agent.py", encoding="utf-8") as fh:
        return fh.read()


def test_the_fix_runs_in_both_sql_pipelines():
    """생성 경로와 파티션 재생성 경로 양쪽 — 한쪽만 걸면 경로에 따라 갈린다."""
    assert _agent_src().count("_fix_logistics_amount(") == 3   # 정의 1 + 호출 2


def test_both_answer_paths_publish_the_notice():
    src = _agent_src()
    assert src.count("from app.core.logistics_amount import notice as _log_amt_notice") == 2
    assert src.count("_log_amt_notice(sql)") == 2


# ── 한화 환산 (붐따 #162) ────────────────────────────────────────────────
#
# ⛔ 이 붐따의 정체는 "환산이 안 됐다" 가 아니라 **물류비가 한화 수출금액 행세를
#    한 것**이다. 프롬프트에는 이미 "환율 환산도 하지 마라" 가 있었고, LLM 은 그
#    말을 지키면서 `SUM(cost_total_krw) AS total_export_amount_krw` 로 우회했다.

LOG_SQL = f"SELECT SUM(cost_total_krw) AS %s FROM {T} WHERE NOT is_deleted"


def test_a_krw_conversion_request_is_recognised():
    assert LA.wants_krw_conversion("금액은 한화로 다 바꿔줘")
    assert LA.wants_krw_conversion("원화로 환산해줘")
    assert LA.wants_krw_conversion("KRW 로 통일해서 보여줘")


def test_merely_mentioning_the_currency_is_not_a_conversion_request():
    """⚠️ 통화어만으로 켜면 평범한 조회마다 경고가 뜬다 — 그러면 아무도 안 읽는다."""
    assert not LA.wants_krw_conversion("통화가 KRW 인 건만 보여줘")
    assert not LA.wants_krw_conversion("원화로 결제된 수출 건수는?")
    assert not LA.wants_krw_conversion("8월 수출 금액 알려줘")


def test_a_conversion_request_is_answered_with_why_it_cannot_be_done():
    out = LA.krw_notice(LOG_SQL % "amount_krw", "금액은 한화로 다 바꿔줘")
    assert "환산해 드리지 못했습니다" in out
    assert out.startswith(">")            # 표보다 먼저 오는 인용 블록


def test_a_cost_column_wearing_an_amount_alias_is_called_out():
    out = LA.krw_notice(LOG_SQL % "total_export_amount_krw", "")
    assert "수출 금액이 아닙니다" in out
    assert LA.cost_labelled_as_amount(LOG_SQL % "total_export_amount_krw")


def test_an_honest_cost_alias_is_left_alone():
    """물류비를 물류비라고 부르는 것은 정상이다 — 여기서 뜨면 소음이 된다."""
    assert not LA.cost_labelled_as_amount(LOG_SQL % "logistics_cost_krw")
    assert not LA.cost_labelled_as_amount(LOG_SQL % "총물류비")
    assert LA.krw_notice(LOG_SQL % "logistics_cost_krw", "8월 물류비 알려줘") == ""


def test_other_tables_never_get_the_krw_notice():
    other = "SELECT SUM(cost_total_krw) AS amount_krw FROM `p.d.other_table`"
    assert LA.krw_notice(other, "한화로 바꿔줘") == ""


def test_the_regex_has_no_stray_control_characters():
    """⛔ `\b` 가 진짜 백스페이스(0x08)로 들어가면 영영 매치하지 않는다 —
    에러 없이 컴파일되고 편집기에도 안 보인다 (`static_ctrl_chars` 와 같은 규칙)."""
    with open("app/core/logistics_amount.py", encoding="utf-8") as fh:
        assert chr(8) not in fh.read()


def test_the_prompt_names_the_column_that_was_substituted():
    """프롬프트도 함께 못 박는다 — 코드가 보증하고 프롬프트가 확률을 낮춘다."""
    with open("prompts/sql_generator.txt", encoding="utf-8") as fh:
        prompt = fh.read()
    assert "붐따 #162" in prompt
    assert "total_export_amount_krw" in prompt


def test_both_answer_paths_publish_the_krw_notice():
    """⚠️ 한쪽만 걸면 스트리밍이냐 아니냐로 답이 갈린다 (이미 겪은 사고다)."""
    src = _agent_src()
    assert src.count("from app.core.logistics_amount import krw_notice as _log_krw_notice") == 2
    assert src.count("_log_krw_notice(sql, query)") == 2
