# -*- coding: utf-8 -*-
"""제품 라인 지목 판정 — 질문이 가리킨 라인과 다른 라인으로 답하지 않게 한다.

⛔ 실제 오답 (붐따 #111, 2026-07-23 양승민):
       질문: "히알루 테카 라인 제품 정보 알려줘"
       답변: "### 🧴 히알루시카(Hyalucica) 라인 제품 정보 안내 ..."

   **히알루테카(Hyalu_Teca)는 히알루시카와 다른 라인**이다 — 플럼핑앰플·퍼밍크림·
   글라스스킨밀크. 그런데 CS 검색이 '라인·제품·정보' 같은 일반 낱말의 겹침만으로
   히알루시카 Q&A 를 최상위로 올렸고, LLM 이 그것을 그대로 답으로 썼다.
   **바꿔치기했다는 말은 어디에도 없다** — 성분에서 '미상'을 '미포함'으로 쓴 것과
   같은 종류의 조용한 오답이다.

   프롬프트에는 이미 "질문한 제품/브랜드와 다른 제품의 정보를 제공하지 마세요"(규칙 8)가
   있었다. 그래도 났다 — 프롬프트는 확률을 높일 뿐 보증하지 않는다. 보증은 코드가 한다.
"""


def test_asking_hyaluteca_never_matches_hyalucica():
    """붐따 #111 그 자체 — 두 라인은 서로를 대신할 수 없다."""
    from app.core import product_lines as pl

    assert pl.mentioned("히알루 테카 라인 제품 정보 알려줘") == {"히알루테카"}
    assert pl.mentioned("히알루시카 라인 제품 정보 알려줘") == {"히알루시카"}


def test_spacing_and_hyphens_do_not_change_the_line():
    """'히알루 테카'·'히알루-테카'·'히알루테카' 는 한 라인이다."""
    from app.core import product_lines as pl

    for form in ("히알루테카", "히알루 테카", "히알루-테카"):
        assert pl.mentioned(f"{form} 앰플 알려줘") == {"히알루테카"}


def test_english_line_names_are_recognized(): 
    """CS 시트의 라인 값이 영문이어도 같은 라인으로 본다 — 아니면 정상 질문이 0건이 된다."""
    from app.core import product_lines as pl

    assert pl.mentioned("Hyalu-Cica line") == {"히알루시카"}
    assert pl.mentioned("SK_Hyalu_Teca_Plumping_Ampoule_50ml") == {"히알루테카"}


def test_longer_line_wins_over_its_prefix():
    """'센텔라테카' 를 '센텔라' 로 읽으면 다시 다른 라인으로 답하게 된다."""
    from app.core import product_lines as pl

    assert pl.mentioned("센텔라 테카 크림") == {"센텔라테카"}
    assert pl.mentioned("센텔라 앰플") == {"센텔라"}


def test_vocabulary_comes_from_the_sql_prompt_table():
    """⛔ 라인 목록을 손으로 또 적지 않는다 — 사본은 반드시 낡는다.

    단일 소스는 `prompts/sql_generator.txt` 의 '### 제품 라인' 표다. 라인이 늘면
    그 표만 고치면 된다.
    """
    import inspect

    from app.core import product_lines as pl

    src = inspect.getsource(pl)
    assert "sql_generator.txt" in src
    assert "제품 라인" in src
    # 표에 있는 라인은 전부 어휘에 들어와야 한다
    vocab = pl.known_lines()
    for line in ("히알루테카", "히알루시카", "센텔라테카", "포어마이징", "랩인네이처"):
        assert line in vocab, f"{line} 이 표에서 안 읽혔다"


def test_cs_search_drops_other_lines_instead_of_answering_with_them(monkeypatch):
    """질문이 라인을 지목했는데 그 라인 자료가 없으면 **0건**이어야 한다.

    0건이면 `_generate_no_match_answer` 가 "찾지 못했습니다" 라고 답하고
    `knowledge_gaps` 에 남는다 — 없는 것을 없다고 말하는 쪽이 맞다.
    """
    from app.agents import cs_agent

    cache = [
        {"product": "", "line": "히알루시카", "brand": "SKIN1004", "category": "",
         "question": "히알루시카 라인 제품 정보 알려줘",
         "answer": "수분·진정 라인입니다", "tab": "제품"},
        {"product": "", "line": "센텔라", "brand": "SKIN1004", "category": "",
         "question": "센텔라 라인 제품 정보 알려줘",
         "answer": "진정 라인입니다", "tab": "제품"},
    ]
    monkeypatch.setattr(cs_agent, "_qa_cache", cache)

    assert cs_agent.search_qa("히알루 테카 라인 제품 정보 알려줘") == []
    got = cs_agent.search_qa("히알루시카 라인 제품 정보 알려줘")
    assert len(got) == 1 and got[0]["line"] == "히알루시카"


def test_cs_search_is_unchanged_when_no_line_is_named(monkeypatch):
    """라인을 안 물었으면 예전대로 — 이 방어가 일반 CS 질문을 막으면 안 된다."""
    from app.agents import cs_agent

    cache = [
        {"product": "", "line": "히알루시카", "brand": "SKIN1004", "category": "선케어",
         "question": "선크림 사용법 알려줘", "answer": "외출 15분 전에 바르세요",
         "tab": "제품"},
    ]
    monkeypatch.setattr(cs_agent, "_qa_cache", cache)

    assert len(cs_agent.search_qa("선크림 사용법 알려줘")) == 1
