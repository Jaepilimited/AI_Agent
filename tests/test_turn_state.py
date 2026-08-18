# -*- coding: utf-8 -*-
"""대화 턴 상태 — 구조화·참조 해석·의미 검증·압축을 지킨다.

⛔ 지금까지 후속 질문이 참조하는 것은 텍스트 뭉치였다:
     - 직전 SQL 앵커 하나 (600자에서 잘림 — 실측 40%가 초과)
     - 답변 원문 (800~3000자에서 잘림, 표·차트는 제거됨)
   그래서 두 턴 전 조건을 이어받지 못했고, 같은 대화 안에서 매출이 300억
   달라진 적이 있다 (붐따 #116).

⚠️ 간격 회귀(0·1·4·8·12턴)가 이 파일의 핵심이다 — **거리가 멀어져도** 참조가
   살아 있는지를 본다. 앵커 하나만 있던 시절엔 간격 1 부터 깨졌다.
"""
import pytest

from app.core.turn_state import (TurnState, compact_context, extract_states,
                                 parse_sql, resolve_reference, verify_alignment)

_T = "`skin1004-319714.Sales_Integration.SALES_ALL_Backup`"


def _turn(q: str, sql: str):
    nl = chr(10)
    body = ("답변 본문…" + nl + "<details><summary>실행된 쿼리</summary>" + nl + nl
            + "```sql" + nl + sql + nl + "```" + nl + "</details>")
    return [{"role": "user", "content": q}, {"role": "assistant", "content": body}]


def _sales(country: str, start="2026-07-01", end="2026-08-01") -> str:
    return (f"SELECT SUM(Sales1_R) FROM {_T} WHERE Country='{country}' "
            f"AND Date>='{start}' AND Date<'{end}'")


def _chat(country: str):
    """조회가 아닌 잡담 턴 — 간격을 벌리는 데 쓴다."""
    return [{"role": "user", "content": f"{country} 얘기 재밌네"},
            {"role": "assistant", "content": "네, 그렇습니다."}]


# ── 1. 구조화 ───────────────────────────────────────────────────────────────

class TestParse:
    def test_table_metric_filter_period(self):
        p = parse_sql(_sales("일본"))
        assert p["table"].endswith("SALES_ALL_Backup")
        assert p["metrics"] == ["SUM(Sales1_R)"]
        assert p["filters"]["Country"] == ["일본"]
        assert p["period"] == ("2026-07-01", "2026-08-01")

    def test_in_clause(self):
        p = parse_sql(f"SELECT 1 FROM {_T} WHERE Country IN ('일본','미국')")
        assert p["filters"]["Country"] == ["일본", "미국"]

    def test_date_is_not_a_filter_value(self):
        """⚠️ 기간은 period 로 따로 잡는다 — 필터 값으로 두면 참조 해석에 잡음이 된다."""
        p = parse_sql(_sales("일본"))
        assert "Date" not in p["filters"]

    def test_empty(self):
        p = parse_sql("")
        assert p["table"] == "" and not p["filters"]


class TestExtract:
    def test_only_query_turns_counted(self):
        msgs = _turn("일본 매출", _sales("일본")) + _chat("커피") + _turn("미국은?", _sales("미국"))
        st = extract_states(msgs)
        assert [s.turn for s in st] == [1, 2]
        assert st[0].filters["Country"] == ["일본"]
        assert st[1].filters["Country"] == ["미국"]

    def test_summary_has_no_answer_text(self):
        """⚠️ 답변 원문·표·차트는 상태에 안 들어간다."""
        st = extract_states(_turn("일본 매출", _sales("일본")))[0]
        assert "답변 본문" not in st.summary()
        assert "Country=일본" in st.summary()


# ── 2. 참조 해석 ────────────────────────────────────────────────────────────

class TestResolveReference:
    @pytest.fixture
    def states(self):
        msgs = (_turn("2026년 7월 일본 매출", _sales("일본"))
                + _turn("미국은?", _sales("미국"))
                + _turn("베트남은?", _sales("베트남")))
        return extract_states(msgs)

    @pytest.mark.parametrize("q,turn", [
        ("첫 번째 질문이랑 비교해줘", 1),
        ("처음 것 다시 보여줘", 1),
        ("두 번째 것", 2),
        ("세 번째", 3),
    ])
    def test_ordinal(self, states, q, turn):
        assert resolve_reference(q, states).turn == turn

    def test_value_reference(self, states):
        """"아까 일본 건" → 일본을 필터로 쓴 턴."""
        assert resolve_reference("아까 일본 건은 어땠지?", states).turn == 1

    def test_last_reference(self, states):
        assert resolve_reference("방금 그거 차트로", states).turn == 3

    def test_no_reference(self, states):
        assert resolve_reference("2026년 8월 태국 매출 알려줘", states) is None

    def test_empty_states(self):
        assert resolve_reference("첫 번째 질문", []) is None

    def test_ordinal_out_of_range(self, states):
        """턴이 3개인데 '다섯 번째' 를 물으면 없는 것으로 본다 (지어내지 않는다)."""
        assert resolve_reference("다섯 번째 것", states) is None


# ── 3. 의미 일치 검증 ───────────────────────────────────────────────────────

class TestVerifyAlignment:
    @pytest.fixture
    def ref(self):
        return TurnState(turn=1, table="p.d.SALES_ALL_Backup",
                         metrics=["SUM(Sales1_R)"],
                         filters={"Country": ["일본"], "Sales_Type": ["B2C"]},
                         period=("2026-07-01", "2026-08-01"))

    def test_filters_kept(self, ref):
        sql = "SELECT SUM(Sales1_R) FROM `p.d.SALES_ALL_Backup` WHERE Country='일본' AND Sales_Type='B2C'"
        assert verify_alignment(sql, ref, "차트로")["ok"] is True

    def test_silent_drop_detected(self, ref):
        """⛔ 이 사고의 본체 — 참조 턴의 필터가 말없이 사라지면 다른 것을 센다."""
        sql = "SELECT SUM(Sales1_R) FROM `p.d.SALES_ALL_Backup` WHERE Sales_Type='B2C'"
        r = verify_alignment(sql, ref, "차트로 보여줘")
        assert r["ok"] is False and "Country=일본" in r["dropped"]

    def test_user_requested_change_is_not_mismatch(self, ref):
        """⚠️ 사용자가 바꾸라고 한 축은 어긋남이 아니다."""
        sql = "SELECT SUM(Sales1_R) FROM `p.d.SALES_ALL_Backup` WHERE Country='미국' AND Sales_Type='B2C'"
        assert verify_alignment(sql, ref, "미국은?")["ok"] is True

    def test_table_change_flagged(self, ref):
        sql = "SELECT COUNT(*) FROM `p.d.Product` WHERE Country='일본' AND Sales_Type='B2C'"
        assert verify_alignment(sql, ref, "")["table_changed"] is True

    def test_no_ref_is_ok(self):
        assert verify_alignment("SELECT 1", None, "")["ok"] is True


# ── 4. 압축 ─────────────────────────────────────────────────────────────────

class TestCompact:
    def test_old_turns_are_one_line(self):
        msgs = []
        for i in range(6):
            msgs += _turn(f"질문{i}", _sales("일본" if i % 2 else "미국"))
        out = compact_context(extract_states(msgs))
        assert out.count("턴") >= 6
        assert "답변 본문" not in out           # 원문은 빠진다
        assert len(out) < 1200                  # 여섯 턴이 한 화면에 들어간다

    def test_empty(self):
        assert compact_context([]) == ""


# ── 5. 간격 회귀 (0·1·4·8·12턴) ─────────────────────────────────────────────

class TestReferenceSurvivesDistance:
    """⛔ **거리가 멀어져도 참조가 살아야 한다.**

    앵커 하나만 있던 시절엔 조회가 한 번만 더 끼어도(간격 1) 첫 턴 조건이 사라졌다.
    """

    @pytest.mark.parametrize("gap", [0, 1, 4, 8, 12])
    def test_first_turn_reachable(self, gap):
        msgs = _turn("2026년 7월 일본 매출", _sales("일본"))
        for i in range(gap):
            msgs += _turn(f"{i}번째 후속", _sales("미국"))
        states = extract_states(msgs)
        ref = resolve_reference("첫 번째 질문이랑 비교해줘", states)
        assert ref is not None and ref.turn == 1
        assert ref.filters["Country"] == ["일본"]

    @pytest.mark.parametrize("gap", [0, 1, 4, 8, 12])
    def test_value_reference_reachable(self, gap):
        """"아까 일본 건" 이 간격과 무관하게 그 턴을 찾는다."""
        msgs = _turn("일본 매출", _sales("일본"))
        for i in range(gap):
            msgs += _turn(f"후속{i}", _sales("미국"))
        states = extract_states(msgs)
        ref = resolve_reference("아까 일본 건 다시", states)
        assert ref is not None and ref.filters["Country"] == ["일본"]

    @pytest.mark.parametrize("gap", [0, 1, 4, 8, 12])
    def test_state_line_present_in_context(self, gap):
        """압축 컨텍스트에 첫 턴이 **끝까지 남는지** — 잘려 나가면 안 된다."""
        msgs = _turn("2026년 7월 일본 매출", _sales("일본"))
        for i in range(gap):
            msgs += _turn(f"후속{i}", _sales("미국"))
        out = compact_context(extract_states(msgs))
        assert "Country=일본" in out, f"간격 {gap} 에서 첫 턴이 사라졌다"
