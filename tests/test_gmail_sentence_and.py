# -*- coding: utf-8 -*-
"""메일 검색이 **문장 전체를 AND 로 묶어** 항상 0건이 되던 것 (붐따 #148·#149).

사고 원문 (2026-08-25 양승민 제보 — "이메일 관련하여 구체적인 검색이 되지 않음"):

    질문   Christopher 로부터 Exolyt 구독 갱신 관련 이메일이 왔는데,
           과거 1년 이내의 Exolyt 관련 메일 모두 알려줘.
    검색어 christopher 로부터 exolyt 구독 갱신 왔는데 과거 1년 exolyt 모두
    결과   검색 결과가 없습니다  (세 번 물어 세 번 다)

**Gmail 은 낱말을 AND 로 묶는다.** `로부터`·`왔는데` 가 전부 든 메일은 있을 수
없으므로 이 검색은 **구조적으로 0건**이다. 에러가 아니라 "메일이 없다" 로 보여
드라이브 0건 사고와 똑같이 조용했다.

지켜야 할 것 네 가지:
  ① 조사·활용형이 검색어로 남지 않는다 (`query_keywords` 규칙을 메일도 따른다)
  ② "X 로부터" 는 `from:X` 다 — 사람 이름은 본문이 아니라 보낸사람에 있다
  ③ "과거 1년 이내" 는 기간이다 (연산자로 번역한다)
  ④ 0건이면 **좁혀 가며 다시 본다.** 낱말을 통째로 버리는 것(기존)은 마지막 수단이고,
     그전에 신호가 센 낱말만 남겨 봐야 한다 — 사용자가 원한 건 'Exolyt' 였다
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents import gws_agent


SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 8, 25, 10, 30, tzinfo=SEOUL)

Q_YEAR_AGO = ("Christopher 로부터 Exolyt 구독 갱신 관련 이메일이 왔는데, "
              "약 1년 전 이메일 내용 참고해서 우리의 구독 내용 정리해서 알려줘.")
Q_WITHIN_YEAR = ("Christopher 로부터 Exolyt 구독 갱신 관련 이메일이 왔는데, "
                 "과거 1년 이내의 Exolyt 관련 메일 모두 알려줘.")
# 같은 사람이 세 번째로 물은 문장 — 문장부호가 붙은 `왔어.` 가 불용어를 비껴갔다
Q_RECENT = "최근에 Christopher 로부터 Exolyt 구독 갱신 관련 이메일이 왔어. 내용 뭔지 알려줘."


def _kw(question: str):
    """연산자를 뺀 **검색어만** 돌려준다."""
    return [t for t in gws_agent.build_gmail_query(question, now=NOW).split() if ":" not in t]


def _ops(question: str):
    return [t for t in gws_agent.build_gmail_query(question, now=NOW).split() if ":" in t]


class TestSenderBecomesOperator:
    """② 사람 이름은 `from:` 이다 — 본문에서 찾으면 못 찾는다."""

    @pytest.mark.parametrize("question,expected", [
        (Q_YEAR_AGO, "from:Christopher"),
        ("Christopher가 보낸 인보이스 메일 찾아줘", "from:Christopher"),
        ("김대리에게서 온 메일 알려줘", "from:김대리"),
    ])
    def test_sender_marker_becomes_from(self, question, expected):
        assert expected in _ops(question)

    def test_sender_is_not_also_a_body_keyword(self):
        assert not any(k.lower() == "christopher" for k in _kw(Q_YEAR_AGO))

    def test_explicit_operator_is_not_duplicated(self):
        ops = _ops("from:boss 오늘 메일 요약")
        assert ops.count("from:boss") == 1
        assert not [o for o in ops if o.startswith("from:") and o != "from:boss"]


class TestParticlesAndVerbEndingsAreDropped:
    """① 조사·활용형이 남으면 AND 하나가 통째로 어긋난다."""

    @pytest.mark.parametrize("junk", [
        "로부터", "왔는데", "왔어", "참고해서", "우리의", "이내의", "모두", "뭔지",
    ])
    def test_junk_never_becomes_a_keyword(self, junk):
        for q in (Q_YEAR_AGO, Q_WITHIN_YEAR, Q_RECENT):
            assert junk not in _kw(q), f"'{junk}' 가 검색어로 남았다: {_kw(q)}"

    def test_real_terms_survive(self):
        assert "Exolyt" in [k.replace("exolyt", "Exolyt") for k in _kw(Q_WITHIN_YEAR)]

    def test_high_signal_term_comes_first(self):
        """⚠️ 좁힐 때 남는 낱말이 곧 검색 품질이다 — 고유명사가 앞이어야 한다."""
        assert _kw(Q_WITHIN_YEAR)[0].lower() == "exolyt"

    def test_keyword_count_is_bounded(self):
        """문장을 통째로 AND 하지 않는다."""
        assert len(_kw(Q_YEAR_AGO)) <= 4


class TestRelativePeriod:
    """③ "과거 1년 이내" 는 기간이다."""

    @pytest.mark.parametrize("question,expected", [
        (Q_WITHIN_YEAR, "newer_than:1y"),
        ("최근 3개월 메일 중 인보이스 찾아줘", "newer_than:3m"),
        ("지난 2주 메일 정리해줘", "newer_than:14d"),
    ])
    def test_relative_window_becomes_operator(self, question, expected):
        assert expected in _ops(question)

    def test_a_year_ago_is_not_a_window(self):
        """⛔ "약 1년 전" 은 **1년 안쪽**이 아니다. 기간을 걸면 정작 그 메일이 빠진다."""
        assert not [o for o in _ops(Q_YEAR_AGO) if o.startswith("newer_than:")]


class TestNarrowingLadder:
    """④ 0건이면 **좁혀 가며** 다시 본다 (낱말 전부 버리기는 마지막 수단)."""

    def _fake(self, hits: str):
        """`hits` 와 정확히 같은 검색어일 때만 결과가 있는 가짜 Gmail."""
        tried = []

        def search(_creds, query, max_results):
            tried.append(query)
            if query == hits:
                return [{"subject": "Exolyt renewal", "from": "chris@exolyt.com",
                         "date": "2026-08-20", "snippet": "s", "body": "본문"}]
            return []
        return search, tried

    def test_falls_back_to_the_strongest_keyword(self, monkeypatch):
        search, tried = self._fake("from:Christopher exolyt")
        monkeypatch.setattr(gws_agent, "search_gmail", search)

        messages, note = gws_agent.run_gmail_search(object(), Q_YEAR_AGO, 10)

        assert messages, f"좁혀 찾기가 동작하지 않았다. 시도한 검색어: {tried}"
        assert note, "넓혀 찾은 사실이 비어 있다"
        assert len(tried) >= 2 and tried[0] != "from:Christopher exolyt", (
            "처음부터 좁힌 검색어로 가면 정확한 질문이 손해를 본다")

    def test_sender_only_is_tried_before_giving_up(self, monkeypatch):
        search, tried = self._fake("from:Christopher")
        monkeypatch.setattr(gws_agent, "search_gmail", search)

        messages, note = gws_agent.run_gmail_search(object(), Q_YEAR_AGO, 10)

        assert messages and note

    def test_exact_hit_is_not_announced_as_widened(self, monkeypatch):
        search, _ = self._fake(gws_agent.build_gmail_query(Q_YEAR_AGO))
        monkeypatch.setattr(gws_agent, "search_gmail", search)

        messages, note = gws_agent.run_gmail_search(object(), Q_YEAR_AGO, 10)

        assert messages and note == "", "정확히 맞은 검색을 넓혔다고 말하면 안 된다"

    def test_never_dumps_the_whole_mailbox(self, monkeypatch):
        """⛔ 연산자도 검색어도 없는 빈 질의는 보내지 않는다 — 메일함 전체가 답처럼 보인다."""
        search, tried = self._fake("nothing matches")
        monkeypatch.setattr(gws_agent, "search_gmail", search)

        gws_agent.run_gmail_search(object(), Q_YEAR_AGO, 10)

        assert all(t.strip() for t in tried), f"빈 검색어를 보냈다: {tried}"


class TestWideningIsDisclosedByCode:
    """⛔ 넓힌 사실을 LLM 에게 맡기면 지워진다 — 실제로 지워졌다 (#148 대화 3번째 턴).

    프로덕션 로그: `gmail_empty_relaxed` 로 넓혀 결과를 얻었는데도 답변은
    "검색 결과가 없습니다" 였다. **보증은 코드가 한다.**
    """

    def test_collect_returns_the_notice_separately(self, monkeypatch):
        monkeypatch.setattr(gws_agent, "search_gmail",
                            lambda _c, q, max_results: (
                                [{"subject": "s", "from": "f", "date": "d",
                                  "snippet": "sn", "body": "b"}] if ":" not in q or
                                q == "from:Christopher" else []))
        _text, notices = gws_agent.GWSAgent()._collect(object(), Q_YEAR_AGO, "gmail")
        assert notices, "넓힌 사실이 호출부까지 올라오지 않는다"

    @pytest.mark.asyncio
    async def test_answer_keeps_the_notice_even_if_llm_drops_it(self, monkeypatch):
        class Creds:
            pass

        class Auth:
            def get_credentials(self, _email):
                return Creds()

        class Flash:
            def generate(self, *_a, **_k):
                return "검색 결과가 없습니다."      # LLM 이 넓힌 사실을 지운 그 답변

        monkeypatch.setattr(gws_agent, "_get_auth_manager", lambda: Auth())
        monkeypatch.setattr(gws_agent.GWSAgent, "_collect",
                            lambda self, c, q, t: ("[메일]\n- 결과 있음", ["(검색어를 넓혀 찾음: 'from:Christopher')"]))
        import app.core.llm as _llm
        monkeypatch.setattr(_llm, "get_flash_client", lambda: Flash())

        answer = await gws_agent.GWSAgent().run(Q_YEAR_AGO, user_email="a@b.com")

        assert "넓혀 찾음" in answer
