# -*- coding: utf-8 -*-
"""노션 저장 관문 회귀 — 저장과 검색을 **양방향**으로 지킨다."""
import pytest

from app.core import notion_export as nx
from app.core import notion_save as ns


@pytest.mark.parametrize("query", [
    "이 답변 노션에 넣어줘",
    "방금 그거 노션에 저장해줘",
    "노션에 올려줘",
    "위 내용 노션 페이지에 추가해줘",
    "브리핑 매일 노션에 넣어줘",
])
def test_save_intent_true(query):
    assert ns.notion_save_intent(query) is True


@pytest.mark.parametrize("query", [
    "노션에서 휴가 규정 찾아줘",
    "노션에 뭐 있어?",
    "노션 문서 어디 있어",
    "노션 정리 잘 돼 있나?",
    "2026년 일본 매출 알려줘",
    "잔디로 보내줘",
])
def test_save_intent_false(query):
    """⛔ 검색을 가로채면 사내 문서 검색이 통째로 죽는다."""
    assert ns.notion_save_intent(query) is False


def test_extract_url_finds_the_notion_link():
    text = "https://www.notion.so/내-페이지-24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b 여기에"
    assert ns.extract_url(text).startswith("https://www.notion.so/")


def test_extract_url_returns_empty_without_a_link():
    assert ns.extract_url("그냥 아무 말") == ""


def _msgs(*pairs):
    return [{"role": role, "content": text} for role, text in pairs]


def test_prompt_asks_for_url_and_explains_the_connection():
    prompt = ns.build_prompt()
    assert "URL" in prompt or "주소" in prompt
    assert "연결" in prompt          # ⛔ 404 의 실제 원인을 함께 알려준다
    assert ns._MARKER.search(prompt)


def test_pending_reads_the_marker_from_the_previous_assistant():
    messages = _msgs(
        ("user", "이 답변 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/x"),
    )
    assert ns.pending(messages)["kind"] == "답변"


def test_pending_is_none_when_the_last_assistant_has_no_marker():
    """⛔ 오래된 요청이 나중의 일반 대화를 가로채면 안 된다."""
    messages = _msgs(
        ("user", "이 답변 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "아니 됐고 매출 알려줘"),
        ("assistant", "2026년 매출은 …"),
        ("user", "고마워"),
    )
    assert ns.pending(messages) is None


def test_target_answer_is_the_message_before_the_question():
    messages = _msgs(
        ("user", "2026년 일본 매출은?"),
        ("assistant", "일본 매출은 55.1억원입니다."),
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/x"),
    )
    assert ns.target_answer(messages) == "일본 매출은 55.1억원입니다."


def test_target_answer_empty_when_history_is_trimmed():
    """⛔ 못 찾으면 빈 문자열이다 — 엉뚱한 것을 저장하지 않는다."""
    messages = _msgs(
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/x"),
    )
    assert ns.target_answer(messages) == ""


@pytest.mark.parametrize("query", [
    "노션 페이지 링크 좀 보내줘",
    "노션 주소 알려줘",
    "노션 DB url 공유해줘",
])
def test_asking_for_a_link_is_not_a_save(query):
    """⛔ 달라는 요청을 저장으로 읽으면, 링크를 물었는데 되묻기가 나간다."""
    assert ns.notion_save_intent(query) is False


def test_search_wins_when_both_verbs_appear():
    """⛔ 이 규칙이 이 관문의 존재 이유다 — 애매하면 검색이다.

    저장은 다시 시키면 되지만, 검색을 가로채면 사내 문서 검색이 통째로 죽는다.
    """
    assert ns.notion_save_intent("노션에서 찾아서 저장해줘") is False


def test_send_verb_still_means_save_when_notion_is_the_destination():
    """⚠️ 오탐을 막느라 정상 표현까지 죽이면 안 된다."""
    assert ns.notion_save_intent("이거 노션에 보내줘") is True


def test_undecodable_marker_leaves_a_trace(monkeypatch):
    """⛔ 실패를 삼키는 except 에는 흔적을 남긴다 (이 저장소의 규칙)."""
    warnings = []
    monkeypatch.setattr(ns.logger, "warning",
                        lambda event, **kw: warnings.append(event))
    messages = _msgs(
        ("user", "이 답변 노션에 넣어줘"),
        ("assistant", "<!-- notion-save-v1:zzzz -->"),
        ("user", "https://www.notion.so/x"),
    )
    assert ns.pending(messages) is None
    assert "notion_save_marker_undecodable" in warnings


@pytest.fixture
def fake_engine(monkeypatch):
    state = {"saved": None, "target": nx.Target("db-1", "ds-1", {"제목": "title"})}

    def resolve(user_id, url):
        if "boom" in url:
            raise nx.NotionError("not_connected", "", 404)
        return state["target"]

    def save(target, title, text, kind="답변", link=""):
        state["saved"] = {"title": title, "text": text, "kind": kind}
        return nx.SaveResult(url="https://notion.so/row-1",
                             created_database=target.created, skipped=[])

    monkeypatch.setattr(nx, "is_enabled", lambda: True)
    monkeypatch.setattr(nx, "resolve_target", resolve)
    monkeypatch.setattr(nx, "save", save)
    return state


def test_handle_ignores_unrelated_questions(fake_engine):
    assert ns.handle("2026년 일본 매출 알려줘", [], 7) is None


def test_handle_asks_back_when_no_url(fake_engine):
    answer = ns.handle("이 답변 노션에 넣어줘", _msgs(
        ("user", "매출은?"), ("assistant", "55.1억원입니다."),
        ("user", "이 답변 노션에 넣어줘")), 7)
    assert ns._MARKER.search(answer)


def test_handle_saves_when_the_url_arrives(fake_engine):
    messages = _msgs(
        ("user", "매출은?"),
        ("assistant", "일본 매출은 55.1억원입니다."),
        ("user", "이 답변 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert "https://notion.so/row-1" in answer
    assert fake_engine["saved"]["text"] == "일본 매출은 55.1억원입니다."


def test_handle_saves_immediately_when_url_is_in_the_request(fake_engine):
    messages = _msgs(
        ("user", "매출은?"), ("assistant", "55.1억원입니다."),
        ("user", "이거 노션에 넣어줘 https://www.notion.so/"
                 "24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert "https://notion.so/row-1" in answer
    assert fake_engine["saved"]["text"] == "55.1억원입니다."


def test_handle_explains_a_404_instead_of_saying_it_just_failed(fake_engine):
    messages = _msgs(
        ("user", "매출은?"), ("assistant", "55.1억원입니다."),
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/boom1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert "연결" in answer          # ⛔ 원인을 갈라서 말한다


def test_handle_says_nothing_to_save_when_history_is_gone(fake_engine):
    messages = _msgs(
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert "저장할" in answer
    assert fake_engine["saved"] is None


def test_handle_tells_the_user_when_the_feature_is_off(monkeypatch):
    monkeypatch.setattr(nx, "is_enabled", lambda: False)
    answer = ns.handle("이 답변 노션에 넣어줘", _msgs(
        ("user", "매출은?"), ("assistant", "55.1억"),
        ("user", "이 답변 노션에 넣어줘")), 7)
    assert "관리자" in answer


def test_gate_is_wired_into_both_orchestrator_paths():
    """⛔ 한쪽만 달면 경로에 따라 답이 갈린다.

    채팅은 **스트리밍**으로 나간다 — `answer_check` 를 만들어 놓고 비스트리밍에만
    배선해 실트래픽에서 한 번도 돌지 않았던 사고가 있었다.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "agents" / "orchestrator.py").read_text(encoding="utf-8")
    body = source.split("async def route_and_execute", 1)[1]
    non_stream, stream = body.split("async def route_and_stream", 1)
    assert "notion_save" in non_stream, "비스트리밍 경로에 관문이 없다"
    assert "notion_save" in stream, "스트리밍 경로에 관문이 없다"


def test_gate_runs_before_the_source_fast_path():
    """⛔ `@@물류` 를 켠 채로도 저장이 돼야 한다 — db_entry 판정보다 앞에 둔다."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "agents" / "orchestrator.py").read_text(encoding="utf-8")
    for chunk in source.split("async def route_and_")[1:]:
        gate = chunk.find("notion_save")
        parse = chunk.find("self.parse_db_prefix")
        assert gate > 0 and parse > 0
        assert gate < parse, "관문이 @@ 소스 판정보다 뒤에 있다"


def test_unexpected_engine_failure_is_contained(monkeypatch, fake_engine):
    """⛔ 저장 실패가 API 경계까지 올라가면 원인이 지워진 일반 에러가 나간다."""
    warnings = []
    monkeypatch.setattr(ns.logger, "warning",
                        lambda event, **kw: warnings.append(event))

    def boom(*args, **kwargs):
        raise RuntimeError("소켓이 끊겼다")

    monkeypatch.setattr(nx, "resolve_target", boom)
    messages = _msgs(
        ("user", "매출은?"), ("assistant", "55.1억원입니다."),
        ("user", "이거 노션에 넣어줘 https://www.notion.so/"
                 "24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert answer is not None                 # 관문이 놓아주면 검색 결과가 나간다
    assert "다시" in answer                    # 저장 실패라고 말한다
    assert "notion_save_unexpected" in warnings


def test_second_save_does_not_store_our_own_confirmation(fake_engine):
    """⛔ 연속 저장에서 우리 확인 메시지가 노션에 실리면 안 된다."""
    messages = _msgs(
        ("user", "매출은?"),
        ("assistant", "일본 매출은 55.1억원입니다."),
        ("user", "이거 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
        ("assistant", "노션에 저장했습니다 → https://notion.so/row-1"),
        ("user", "이것도 노션에 넣어줘 https://www.notion.so/"
                 "24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    ns.handle(messages[-1]["content"], messages, 7)
    assert fake_engine["saved"]["text"] == "일본 매출은 55.1억원입니다."


def test_recurring_briefing_request_without_url_asks_back_not_declined(fake_engine):
    """⛔ 2단계부터는 한 건 저장(_do_save)이 아니라 **등록**으로 되묻는다."""
    answer = ns.handle("브리핑 매일 노션에 넣어줘",
                       _msgs(("user", "브리핑 매일 노션에 넣어줘")), 7)
    assert ns._MARKER.search(answer)
    assert "준비 중" not in answer
    assert fake_engine["saved"] is None


@pytest.mark.parametrize("query", [
    "브리핑 매일 노션에 넣어줘",
    "브리핑 매번 자동으로 노션에 올려줘",
    "앞으로 브리핑 계속 노션에 저장해줘",
])
def test_recurring_briefing_variants_all_ask_back_for_a_url(fake_engine, query):
    answer = ns.handle(query, _msgs(("user", query)), 7)
    assert ns._MARKER.search(answer)
    assert "준비 중" not in answer
    assert fake_engine["saved"] is None


def test_title_never_comes_from_the_query_details_block(fake_engine):
    """⛔ 본문에서 걷어낸 내부 경로가 **제목**으로 새면 안 된다."""
    messages = _msgs(
        ("user", "매출은?"),
        ("assistant",
         "<details><summary>실행된 쿼리</summary>\n\n"
         "`skin1004-319714.SALES_ALL_Backup.x`\n</details>\n\n"
         "일본 매출은 55.1억원입니다."),
        ("user", "이거 노션에 넣어줘 https://www.notion.so/"
                 "24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    ns.handle(messages[-1]["content"], messages, 7)
    assert "실행된 쿼리" not in fake_engine["saved"]["title"]
    assert "skin1004-319714" not in fake_engine["saved"]["title"]
    assert "55.1억" in fake_engine["saved"]["title"]


def test_body_that_is_only_a_details_block_saves_nothing(fake_engine):
    """⛔ 정제하면 빈 본문이 되는 답변으로 빈 행을 만들지 않는다."""
    messages = _msgs(
        ("user", "매출은?"),
        ("assistant", "<details><summary>실행된 쿼리</summary>\nSELECT 1\n</details>"),
        ("user", "이거 노션에 넣어줘 https://www.notion.so/"
                 "24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert fake_engine["saved"] is None
    assert "찾지 못했습니다" in answer


def test_one_off_briefing_save_is_not_mistaken_for_recurring(fake_engine):
    """⚠️ '브리핑' 이나 '매일' 이 있어도 **둘 다** 있을 때만 가로챈다."""
    messages = _msgs(
        ("user", "오늘 브리핑 요약해줘"),
        ("assistant", "오늘의 브리핑입니다."),
        ("user", "이 답변 노션에 넣어줘"),
        ("assistant", ns.build_prompt()),
        ("user", "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert "https://notion.so/row-1" in answer
    assert fake_engine["saved"]["text"] == "오늘의 브리핑입니다."


def test_recurring_request_now_registers_instead_of_apologising(monkeypatch, fake_engine):
    """1단계에서는 "준비 중" 이라고 답했다. 2단계에서는 실제로 등록한다."""
    from app.core import notion_briefing as nb

    saved = {}
    monkeypatch.setattr(nb, "set_target",
                        lambda user_id, url, **kw: saved.update(
                            {"user_id": user_id, "url": url}))
    messages = _msgs(
        ("user", "브리핑 매일 노션에 넣어줘"),
        ("assistant", ns.build_prompt("브리핑")),
        ("user", "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert saved["user_id"] == 7
    assert "매일" in answer
    assert "준비 중" not in answer


def test_recurring_request_without_a_url_asks_for_one(fake_engine):
    messages = _msgs(
        ("user", "매출은?"), ("assistant", "55.1억원입니다."),
        ("user", "브리핑 매일 노션에 넣어줘"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert ns._MARKER.search(answer)
    assert "준비 중" not in answer


def test_registration_is_refused_when_the_feature_is_off(monkeypatch):
    """⛔ 못 보내는 상태에서 "등록했습니다" 라고 하면 거짓 약속이다."""
    from app.core import notion_briefing as nb
    from app.core import notion_export as nx

    monkeypatch.setattr(nx, "is_enabled", lambda: False)
    touched = []
    monkeypatch.setattr(nb, "set_target",
                        lambda *a, **k: touched.append(1))

    messages = _msgs(
        ("user", "브리핑 매일 노션에 넣어줘"),
        ("assistant", ns.build_prompt("브리핑")),
        ("user", "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"),
    )
    answer = ns.handle(messages[-1]["content"], messages, 7)
    assert touched == []                  # 등록하지 않는다
    assert "관리자" in answer              # 꺼져 있다고 말한다


def test_briefing_ask_back_round_trips_through_handle(fake_engine, monkeypatch):
    """⛔ 되묻기에 실린 kind 가 틀리면 URL 을 받은 뒤 **저장**으로 갈린다.

    구독을 걸었다고 믿는 사람에게 답변 한 건만 저장된다 — 조용한 오답이다.
    그래서 `build_prompt` 를 손으로 만들지 않고 `handle()` 이 낸 것을 되먹인다.
    """
    from app.core import notion_briefing as nb

    registered = {}
    monkeypatch.setattr(nb, "ensure_tables", lambda: None)
    monkeypatch.setattr(nb, "set_target",
                        lambda user_id, url, **kw: registered.update(
                            {"user_id": user_id, "url": url}))

    messages = _msgs(
        ("user", "매출은?"), ("assistant", "일본 매출은 55.1억원입니다."),
        ("user", "브리핑 매일 노션에 넣어줘"),
    )
    prompt = ns.handle(messages[-1]["content"], messages, 7)
    assert ns._MARKER.search(prompt), "되묻기가 나와야 한다"

    messages = list(messages) + [
        {"role": "assistant", "content": prompt},
        {"role": "user",
         "content": "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"},
    ]
    answer = ns.handle(messages[-1]["content"], messages, 7)

    assert registered.get("user_id") == 7, "저장이 아니라 **등록**이어야 한다"
    assert fake_engine["saved"] is None, "직전 답변을 저장하면 안 된다"
    assert "매일" in answer
