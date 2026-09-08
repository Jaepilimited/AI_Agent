# -*- coding: utf-8 -*-
"""제품정보(제품 스펙)는 제품 Q&A 와 **별개 소스**다 — 2026-09-04.

⛔ **사용자 지시**: *"제품 q&A는 따로이고, 제품정보는 cs데이터에 들어가야함"*

    제품 Q&A  = `[BD_BP] CS제품문의_모음집` 시트 — 문의 대응 기록 (914건)
    제품정보  = 노션 `스킨1004 전제품 한 눈에 파악하기` — 제품 스펙 (55종)

⛔ **내용은 4단 깊이에 있다.** 얕게 긁으면 제품명 목록만 나오고, 실제로 그래서
   "히알루테카 제품 정보" 질문에 경로와 링크만 답한 사고가 있었다.
"""
import pytest


def test_product_info_is_a_separate_source_from_qa():
    """⛔ 한 덩어리로 섞으면 답변이 어느 쪽을 근거로 말하는지 사라진다."""
    from app.agents import cs_agent

    src = cs_agent._build_answer_prompt("히알루테카 성분", "…Q&A…", 3)
    assert "CS 데이터베이스 검색 결과" in src
    assert "제품정보" in src
    assert "두 자료는 성격이 다릅니다" in src


def test_prompt_forbids_inventing_numbers():
    """⚠️ 함량·PPM 은 지어내기 쉬운 값이다."""
    from app.agents import cs_agent

    src = cs_agent._build_answer_prompt("성분", "ctx", 1)
    assert "지어내지 마세요" in src


def test_block_is_empty_when_nothing_matches(monkeypatch):
    from app.core import product_info as PI
    from app.agents import cs_agent

    monkeypatch.setattr(PI, "search", lambda q, limit=4: [])
    assert cs_agent._product_info_block("아무거나") == ""


def test_block_failure_does_not_kill_the_cs_answer(monkeypatch):
    """⚠️ 제품정보는 부수 자료다 — 실패해도 CS 답변은 나가야 한다."""
    from app.core import product_info as PI
    from app.agents import cs_agent

    def _boom(q, limit=4):
        raise RuntimeError("db down")

    monkeypatch.setattr(PI, "search", _boom)
    assert cs_agent._product_info_block("히알루테카") == ""


def test_sync_never_wipes_on_empty(monkeypatch):
    """⛔ 0건으로 덮으면 제품정보가 조용히 사라진다 (OP 재고·CS 캐시와 같은 규칙)."""
    from app.core import product_info as PI

    monkeypatch.setattr(PI, "collect", lambda: [])
    monkeypatch.setattr(PI, "ensure_table", lambda: None)
    stats = PI.sync()
    assert stats["written"] == 0 and stats.get("kept_old") is True


def test_status_query_avoids_reserved_words():
    """⛔ `lines` 는 MariaDB 예약어다 — 별칭으로 쓰면 1064 가 나고 `except` 가
    삼켜 **count 0** 으로 보인다 (조용한 오작동, 실측)."""
    import inspect

    from app.core import product_info as PI

    src = inspect.getsource(PI.status)
    assert " lines FROM" not in src
    assert "line_count" in src


def test_job_and_watch_are_registered():
    from app.core.self_check import EXPECTED_JOBS
    from app.core.data_freshness import SOURCES

    assert "product_info_sync_daily" in EXPECTED_JOBS
    assert any(s.name.startswith("제품정보") for s in SOURCES)


def test_zero_rows_is_treated_as_job_failure():
    """⚠️ 0건을 성공으로 기록하면 자가 점검이 영영 못 잡는다."""
    from pathlib import Path

    main = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    body = main[main.index("async def _product_info_sync_job"):
                main.index("async def _ingredient_sync_job")]
    assert 'if not stats.get("written")' in body and "raise" in body


def test_freshness_coverage_still_complete():
    from app.core.data_freshness import coverage_gaps

    assert not coverage_gaps()


# ──────────────────────────────────────────────────────────────────────────
# ⛔ 질문의 군더더기 한 낱말이 조회를 통째로 0건으로 만든다 — 2026-09-08
#
#   실사용 제보:
#       질문: "테카 앰플 정보좀"
#       답변: "'테카(TECA) 앰플'에 대한 등록 정보가 없습니다"
#
#   노션 원본 실측(2026-09-08): `센텔라-테카 (New) | 마다가스카르 센텔라 테카 앰플`
#   이 **725자 본문과 함께 있다.** 데이터는 멀쩡했고 검색이 빗나갔다.
#
#   낱말을 AND 로 걸기 때문에 하나라도 자료에 없으면 0건이다. `정보`·`좀` 은 각각
#   불용어에 있는데 `정보좀` 은 붙어 있어 걸리지 않았다 (`좀` 은 조사가 아니라서
#   `strip_particle` 도 떼지 않는다).
#   ⛔ 대응은 불용어 목록을 늘리는 것이 아니다 — 끝이 없다(정보좀·뭐임·궁금…).
#      OP 재고가 같은 사고("얼마나")를 **데이터에 물어** 풀었다. 여기도 그 규칙을 쓴다.
# ──────────────────────────────────────────────────────────────────────────

# 노션 원본에서 그대로 가져온 표본 (2026-09-08 실측)
_ROWS = [
    {"line": "센텔라-테카 (New)", "product": "마다가스카르 센텔라 테카 앰플",
     "body": "테카(TECA) 성분을 담은 앰플입니다. 아침저녁 세안 후 사용하세요.", "url": ""},
    {"line": "센텔라-테카 (New)", "product": "마다가스카르 센텔라 테카 크림",
     "body": "테카 성분 크림입니다.", "url": ""},
    {"line": "히알루-테카 (New)", "product": "마다가스카르 히알루-테카 플럼핑 앰플",
     "body": "히알루론산과 테카를 담은 앰플입니다.", "url": ""},
    {"line": "센텔라", "product": "마다가스카르 센텔라 앰플",
     "body": "병풀 추출물 앰플입니다.", "url": ""},
]

_TECA_AMPOULE = "마다가스카르 센텔라 테카 앰플"


@pytest.fixture()
def _rows(monkeypatch):
    """⛔ 가짜 DB 가 `WHERE` 를 무시하면 이 회귀는 **아무것도 못 잡는다** —
    전부 돌려주면 0건 사고가 재현되지 않는다. LIKE 의미(낱말 AND)를 흉내 낸다.
    """
    import app.db.mariadb as db

    def _fake(sql, params=None):
        rows = [dict(r) for r in _ROWS]
        if not params:
            return rows
        # 낱말마다 `(product LIKE %s OR body LIKE %s)` 두 개씩 들어온다
        words = [str(p).strip("%") for p in params[::2]]
        return [r for r in rows
                if all(w in r["product"] or w in r["body"] for w in words)]

    monkeypatch.setattr(db, "fetch_all", _fake)
    return _ROWS


@pytest.mark.parametrize("q", [
    "테카 앰플 정보좀",          # ← 실제 제보 문장
    "테카 앰플 정보 좀 알려줘",
    "테카 앰플이 뭐임",
    "테카 앰플 궁금해요",
])
def test_filler_words_must_not_empty_the_result(_rows, q):
    """⛔ 군더더기 한 낱말 때문에 0건이 나면 안 된다.

    ⚠️ 에러가 아니라 빈손이라 조용하다 — "정말 없다" 와 글자 그대로 똑같이 보인다.
    """
    from app.core import product_info as PI

    names = [r["product"] for r in PI.search(q)]
    assert names, f"{q!r} → 0건 (군더더기 낱말이 조회를 죽였다)"
    assert _TECA_AMPOULE in names


def test_words_that_are_in_the_data_still_narrow(_rows):
    """⚠️ 반대로 너무 넓히면 안 된다 — 자료에 있는 낱말은 살아서 결과를 좁힌다.

    잡음은 답처럼 보여서 0건보다 나쁘다 (드라이브 검색과 같은 규칙).
    """
    from app.core import product_info as PI

    names = {r["product"] for r in PI.search("테카 앰플 정보좀")}
    # '테카' 가 살아 있으니 센텔라 앰플은 빠지고, '앰플' 이 살아 있으니 테카 크림도 빠진다
    assert names == {_TECA_AMPOULE, "마다가스카르 히알루-테카 플럼핑 앰플"}


def test_asking_the_data_is_not_a_stopword_list():
    """⛔ 목록을 손으로 쌓으면 끝이 없다 (정보좀·뭐임·궁금해요…)."""
    import inspect

    from app.core import product_info as PI

    assert "usable_words" in inspect.getsource(PI.search)


def test_a_named_line_still_wins(_rows):
    """⚠️ 라인을 지목했으면 그 라인이 이긴다 (붐따 #111 방어선은 그대로)."""
    from app.core import product_info as PI

    names = [r["product"] for r in PI.search("히알루테카 앰플")]
    assert names and all("히알루" in n for n in names)


def test_nothing_usable_returns_nothing(_rows):
    """⛔ 아무 낱말도 자료에 없으면 **빈손이 맞다** — 넓혀서 아무거나 주지 않는다."""
    from app.core import product_info as PI

    assert PI.search("배송비 환불 규정 알려줘") == []


# ──────────────────────────────────────────────────────────────────────────
# ⛔ 라인을 맞혔는데 **질문한 제품이 빠진다** — 2026-09-08 (위 사고를 쫓다 드러났다)
#
#   라인 경로는 그 라인 전체를 돌려주고 `_product_info_block` 은 앞의 4개만 쓴다.
#   히알루시카는 제품이 11종이라 `"히알루시카 슬리핑 팩 알려줘"` 의 슬리핑 팩(10번째)이
#   **통째로 빠지고** 젤리핏 앰플 패드·워터핏 선 세럼이 대신 실렸다 (실측).
#   `센텔라` 는 더 넓다 — 제품명이 죄다 `마다가스카르 센텔라 …` 라 라인 필터가
#   거의 전부를 통과시켜 `"센텔라 앰플 폼"` 에 **포어마이징 4종**이 나갔다.
#
#   ⛔ 붐따 #111 과 같은 실패 모양이다: 질문한 제품이 아닌 것을 설명하면서
#      바꿔치기했다는 말은 하지 않는다. 라인을 좁혔으면 **낱말로 한 번 더** 좁힌다.
#   ⚠️ 다만 못 좁히면 라인 전체로 되돌아간다 — 제품명 표기가 어긋날 수 있어서다
#      (`히알루시카` ↔ 자료의 `히알루-시카`). 좁히려다 0건을 만들면 안 된다.
# ──────────────────────────────────────────────────────────────────────────

_LINE_ROWS = [
    {"line": "히알루 시카", "product": f"마다가스카르 센텔라 히알루-시카 {n}",
     "body": f"{n} 설명입니다.", "url": ""}
    for n in ("젤리핏 앰플 패드", "워터핏 선 세럼", "퍼스트 앰플", "실키핏 선 스틱",
              "모이스처 크림", "젠틀 클렌징 밀크", "블루 세럼", "하이드레이팅 마스크",
              "클라우디 미스트", "광채 토너", "슬리핑 팩")
]


@pytest.fixture()
def _line_rows(monkeypatch):
    import app.db.mariadb as db

    monkeypatch.setattr(db, "fetch_all", lambda *a, **k: [dict(r) for r in _LINE_ROWS])
    return _LINE_ROWS


@pytest.mark.parametrize("q,want", [
    ("히알루시카 슬리핑 팩 알려줘", "슬리핑 팩"),
    ("히알루시카 광채 토너 정보좀", "광채 토너"),
    ("히알루시카 블루 세럼", "블루 세럼"),
])
def test_the_asked_product_survives_the_limit(_line_rows, q, want):
    """⛔ 질문한 제품이 상한에 잘려 나가면 안 된다 — 답변이 다른 제품을 설명한다."""
    from app.core import product_info as PI

    # ⚠️ 4 는 `cs_agent._product_info_block` 이 실제로 쓰는 값이다
    names = [r["product"] for r in PI.search(q, limit=4)]
    assert names, f"{q!r} → 0건"
    assert any(want in n for n in names), f"{q!r} → {names}"


def test_a_line_only_question_still_gets_the_whole_line(_line_rows):
    """⚠️ 좁히려다 0건을 만들지 않는다 — 자료 표기는 `히알루-시카`(하이픈)다."""
    from app.core import product_info as PI

    assert len(PI.search("히알루시카 라인 알려줘", limit=6)) > 1


# ──────────────────────────────────────────────────────────────────────────
# ⛔ 이름에 걸린 제품이 본문에 걸린 제품보다 앞에 와야 한다 — 2026-09-08
#
#   좁히기를 고친 뒤에도 `"마다가스카르 센텔라 앰플"` 의 상위 4건에 정작
#   `마다가스카르 센텔라 앰플`이 없었다 (실측). 본문에 '앰플' 이 적힌 제품이
#   표 순서대로 먼저 실렸기 때문이다 — 호출부는 앞의 4개만 쓴다.
#
#   ⚠️ 순위는 **코드가 정한다**. LLM 에게 "질문한 제품을 골라라" 고 맡기면 확률이다.
#   ⚠️ 동점이면 **이름이 짧은 쪽**이 이긴다 — 군더더기 낱말이 적을수록 가까운 이름이다.
# ──────────────────────────────────────────────────────────────────────────

_RANK_ROWS = [
    {"line": "포어마이징", "product": "마다가스카르 센텔라 포어마이징 라이트 젤 크림",
     "body": "앰플 다음 단계로 발라주세요.", "url": ""},
    {"line": "히알루 시카", "product": "마다가스카르 센텔라 히알루-시카 젤리핏 앰플 패드",
     "body": "패드형 앰플입니다.", "url": ""},
    {"line": "센텔라", "product": "마다가스카르 센텔라 앰플",
     "body": "병풀 추출물 앰플입니다.", "url": ""},
    {"line": "센텔라", "product": "마다가스카르 센텔라 앰플 폼",
     "body": "앰플 성분을 담은 클렌징 폼입니다.", "url": ""},
]


@pytest.fixture()
def _rank_rows(monkeypatch):
    import app.db.mariadb as db

    monkeypatch.setattr(db, "fetch_all", lambda *a, **k: [dict(r) for r in _RANK_ROWS])
    return _RANK_ROWS


def test_name_matches_outrank_body_matches(_rank_rows):
    """⛔ 본문에 스친 제품이 앞자리를 차지하면 질문한 제품이 상한에 잘린다."""
    from app.core import product_info as PI

    names = [r["product"] for r in PI.search("마다가스카르 센텔라 앰플", limit=2)]
    assert "마다가스카르 센텔라 앰플" == names[0], names
    # 이름에 '앰플' 이 없는 젤 크림은 뒤로 밀린다
    assert "마다가스카르 센텔라 포어마이징 라이트 젤 크림" not in names


def test_ties_prefer_the_shorter_name(_rank_rows):
    """⚠️ 동점이면 이름이 짧은 쪽 — 군더더기가 적을수록 가까운 이름이다."""
    from app.core import product_info as PI

    names = [r["product"] for r in PI.search("센텔라 앰플", limit=4)]
    assert names.index("마다가스카르 센텔라 앰플") < names.index("마다가스카르 센텔라 앰플 폼")


# ⚠️ 실측 재현: `"센텔라 테카 앰플 성분"` — 테카 앰플 **본문에는 '성분' 이 없고**
#    토너·크림 본문에는 있다. 그래서 AND 가 비어 라인 전체로 되돌아갔고,
#    되돌아간 목록이 표 순서라 정작 앰플이 세 번째였다 (호출부는 앞의 몇 개만 쓴다).
_FALLBACK_ROWS = [
    {"line": "센텔라-테카 (New)", "product": "마다가스카르 센텔라 테카 수딩 토너",
     "body": "주요 성분은 병풀입니다.", "url": ""},
    {"line": "센텔라-테카 (New)", "product": "마다가스카르 센텔라 테카 크림",
     "body": "주요 성분은 병풀입니다.", "url": ""},
    {"line": "센텔라-테카 (New)", "product": "마다가스카르 센텔라 테카 앰플",
     "body": "아침저녁 세안 후 발라주세요.", "url": ""},
]


def test_the_line_fallback_is_ranked_too(monkeypatch):
    """⚠️ 낱말이 다 안 맞아 라인 전체로 되돌아갈 때도 **가까운 이름이 앞**이다."""
    import app.db.mariadb as db

    from app.core import product_info as PI

    monkeypatch.setattr(db, "fetch_all",
                        lambda *a, **k: [dict(r) for r in _FALLBACK_ROWS])
    names = [r["product"] for r in PI.search("센텔라 테카 앰플 성분", limit=4)]
    assert names, "라인으로 되돌아가야 한다"
    assert names[0] == "마다가스카르 센텔라 테카 앰플", names


# ──────────────────────────────────────────────────────────────────────────
# ⛔ 대화 맥락이 검색어를 **굶긴다** — 2026-09-09 프로덕션 실측
#
#   어제 `정보좀` 을 고치고 배포했는데 화면에서는 그대로 실패했다:
#       질문: "테카 앰플 정보좀"
#       답변: "'테카 앰플'에 대한 상세 정보는 … 등록되어 있지 않습니다"
#             "현재 데이터베이스에는 마다가스카르 센텔라 앰플, … 티트리카 릴리프
#              앰플, … 포어마이징 프레쉬 앰플 … 정보만 확인 가능합니다"
#
#   ⛔ **함수는 고쳐졌는데 함수에 닿는 문자열이 달랐다.** 오케스트레이터는
#      `cs_agent` 에 원문이 아니라 이 덩어리를 넘긴다:
#
#          [이전 대화]
#          사용자: 센텔라 앰플 사용법 …
#          [현재 질문]
#          테카 앰플 정보좀
#
#      `extract()` 는 **문서 순서대로 8개**만 뽑는다. 현재 질문이 맨 뒤라
#      맥락이 상한을 다 먹고 `테카` 는 아예 들어오지 못한다 — 실측:
#      ['이전','대화','사용자','센텔라','앰플','사용법','셀라','마다가스카르'].
#
#   ⚠️ 그래서 프로덕션에서 `search("테카 앰플 정보좀")` 을 직접 부르면 **맞게**
#      나온다. 고쳤다고 확신하게 만드는 모양이라 특히 위험하다 — 실사용 경로는
#      그 문자열을 부르지 않는다.
# ──────────────────────────────────────────────────────────────────────────

_CTX = """[이전 대화]
사용자: 센텔라 앰플 사용법 알려줘
셀라: 마다가스카르 센텔라 앰플은 토너 다음 단계에 사용합니다. 크림 전에 발라주세요.

[현재 질문]
테카 앰플 정보좀"""


def test_the_current_question_is_not_starved_by_context(_rows):
    """⛔ 맥락이 아무리 길어도 **지금 물은 것**이 검색어에 들어와야 한다."""
    from app.agents import cs_agent

    block = cs_agent._product_info_block(_CTX)
    assert "마다가스카르 센텔라 테카 앰플" in block, block[:400]


def test_a_bare_question_still_works(_rows):
    """⚠️ 맥락 표식이 없으면 원문 그대로 쓴다 (첫 질문·비스트리밍 경로)."""
    from app.agents import cs_agent

    assert "마다가스카르 센텔라 테카 앰플" in cs_agent._product_info_block("테카 앰플 정보좀")


def test_context_still_helps_when_the_question_alone_finds_nothing(_rows):
    """⚠️ 맥락을 **버리지는** 않는다 — 현재 질문만으로 못 찾으면 되돌아본다.

    "그럼 크림은?" 같은 후속 발화는 그 자체로는 라인을 모른다.
    """
    from app.agents import cs_agent

    ctx = "[이전 대화]\n셀라: 마다가스카르 센텔라 테카 앰플 안내\n\n[현재 질문]\n그건 언제 발라?"
    # 현재 질문에는 제품어가 없다 → 맥락에서 찾아 준다
    assert "테카" in cs_agent._product_info_block(ctx)


def test_the_marker_is_not_spelled_twice():
    """⛔ 표식 문자열을 양쪽이 따로 적으면 한쪽만 고쳐졌을 때 조용히 갈린다.

    오케스트레이터가 만들고 cs_agent 가 읽는다 — 같은 상수를 봐야 한다.
    """
    import inspect

    from app.agents import cs_agent, orchestrator

    marker = cs_agent.CURRENT_QUESTION_MARKER
    assert marker in inspect.getsource(orchestrator), \
        "오케스트레이터가 이 표식으로 감싸지 않으면 cs_agent 가 못 읽는다"
