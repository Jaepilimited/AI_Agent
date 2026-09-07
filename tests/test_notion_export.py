# -*- coding: utf-8 -*-
"""노션 쓰기 엔진 회귀.

⛔ 이 테스트는 네트워크를 타지 않는다. `_request` 를 monkeypatch 해서
   우리가 **보내는 것**과 **받은 것을 해석하는 방식**만 검사한다.
   실 API 관통은 `scripts/notion_write_probe.py` 가 수동으로 한다.
"""
import pytest

from app.core import notion_export as nx


def test_write_path_pins_2025_version():
    """⛔ 옛 버전으로 DB 행을 만들면 단일 소스 DB 에서만 동작한다."""
    assert nx.NOTION_VERSION == "2025-09-03"


def test_headers_carry_token_and_version(monkeypatch):
    monkeypatch.setattr(nx.settings, "notion_write_token", "secret-token")
    headers = nx._headers()
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Notion-Version"] == "2025-09-03"


def test_disabled_when_token_missing(monkeypatch):
    monkeypatch.setattr(nx.settings, "notion_write_token", "")
    assert nx.is_enabled() is False


def test_read_paths_keep_old_version():
    """읽기 경로는 그대로 둔다 — 버전은 요청마다 붙는 값이라 섞여도 된다."""
    from app.core import product_info

    assert product_info.NOTION_VERSION == "2022-06-28"


_ID = "24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"
_UUID = "24f1a2b3-c4d5-4e6f-8a9b-0c1d2e3f4a5b"


@pytest.mark.parametrize("url", [
    f"https://www.notion.so/{_ID}",
    f"https://www.notion.so/회의록-{_ID}",
    f"https://www.notion.so/workspace/My-Notes-{_ID}?v=99991111222233334444555566667777",
    f"https://www.notion.so/{_ID}?pvs=4#abc",
    f"https://www.notion.so/{_UUID}",
    _ID,
])
def test_parse_page_url_accepts_real_shapes(url):
    assert nx.parse_page_url(url) == _UUID


@pytest.mark.parametrize("url", [
    "",
    None,
    "https://example.com/nothing",
    "그냥 아무 말",
    f"https://skin1004.notion.site/{_ID}",   # ⛔ 외부 공개 사이트는 쓸 수 없다
])
def test_parse_page_url_rejects(url):
    assert nx.parse_page_url(url) is None


def test_parse_page_url_takes_the_id_not_the_view():
    """⚠️ `?v=` 뒤에도 32자 hex 가 있다 — 뷰 id 를 페이지로 읽으면 404 가 난다."""
    url = f"https://www.notion.so/{_ID}?v=99991111222233334444555566667777"
    assert nx.parse_page_url(url) == _UUID


# Task 3: clean_for_notion
def test_clean_drops_the_query_details_block():
    """⛔ 노션에서는 평문으로 펼쳐져 내부 테이블 경로가 페이지에 남는다."""
    text = (
        "2026년 매출은 1,234억원입니다.\n\n"
        "<details><summary>실행된 쿼리</summary>\n\n"
        "```sql\nSELECT SUM(Sales1_R) FROM `skin1004-319714.SALES_ALL_Backup.x`\n```\n\n"
        "</details>\n"
    )
    cleaned = nx.clean_for_notion(text)
    assert "1,234억원" in cleaned
    assert "skin1004-319714" not in cleaned
    assert "실행된 쿼리" not in cleaned


def test_clean_drops_chart_config_fence_and_orphan_heading():
    text = (
        "## 시각화\n"
        "```chart\n{\"type\": \"bar\", \"labels\": [1, 2]}\n```\n"
        "## 요약\n본문\n"
    )
    cleaned = nx.clean_for_notion(text)
    assert "chart" not in cleaned
    assert "시각화" not in cleaned   # 차트를 뺐으면 홀로 남는 제목도 걷는다
    assert "## 요약" in cleaned


def test_clean_keeps_sql_fence_that_is_not_a_query_block():
    """코드 자체를 물어본 답변까지 지우면 안 된다."""
    text = "이렇게 쓰세요:\n```python\nprint(1)\n```\n"
    assert "print(1)" in nx.clean_for_notion(text)


def test_clean_drops_followup_chips():
    """⛔ 실제 후속 제안 칩 형식(`work_briefing.py` 가 잔디 본문에서 걷어내는 것과 같다)이다.

    예전 정규식(`<!-- followup: -->`)은 이 앱이 한 번도 내지 않는 형식이라
    칩이 그대로 노션 페이지에 실렸다.
    """
    text = ("본문입니다.\n\n"
            "> 💡 **이런 것도 물어보세요**\n"
            "> - 월별 추이는?\n")
    cleaned = nx.clean_for_notion(text)
    assert "본문입니다" in cleaned
    assert "이런 것도 물어보세요" not in cleaned
    assert "월별 추이" not in cleaned


# Task 4: markdown_to_blocks
def _types(blocks):
    return [b["type"] for b in blocks]


def _plain(block):
    key = block["type"]
    return "".join(r["text"]["content"] for r in block[key]["rich_text"])


def test_markdown_maps_headings_lists_and_quote():
    blocks = nx.markdown_to_blocks(
        "# 제목\n## 소제목\n본문 문단\n- 첫째\n- 둘째\n1. 하나\n> 인용\n"
    )
    assert _types(blocks) == [
        "heading_1", "heading_2", "paragraph",
        "bulleted_list_item", "bulleted_list_item",
        "numbered_list_item", "quote",
    ]
    assert _plain(blocks[0]) == "제목"
    assert _plain(blocks[3]) == "첫째"


def test_markdown_keeps_code_fence_as_code_block():
    blocks = nx.markdown_to_blocks("```python\nprint(1)\nprint(2)\n```\n")
    assert _types(blocks) == ["code"]
    assert "print(1)\nprint(2)" == _plain(blocks[0])


def test_briefing_plaintext_becomes_headings_and_bullets():
    """`render_markdown()` 산출물은 이름과 달리 **평문**이다.

    ⛔ 노션용 렌더러를 따로 만들면 사본이 갈린다 — 변환기가 흡수한다.
    """
    blocks = nx.markdown_to_blocks(
        "☀️ 오늘의 출근 브리핑 (2026-09-07 월요일)\n"
        "\n"
        "📅 오늘의 일정 · 2건\n"
        "  10:00 | 주간 회의\n"
        "      장소 3층\n"
    )
    assert _types(blocks) == [
        "heading_2", "heading_3", "bulleted_list_item", "bulleted_list_item",
    ]
    assert _plain(blocks[1]) == "📅 오늘의 일정 · 2건"


def test_rich_text_splits_at_2000_chars():
    parts = nx.rich_text("가" * 4500)
    assert [len(p["text"]["content"]) for p in parts] == [2000, 2000, 500]


def test_long_paragraph_stays_one_block_with_split_rich_text():
    blocks = nx.markdown_to_blocks("나" * 3000)
    assert len(blocks) == 1
    assert len(blocks[0]["paragraph"]["rich_text"]) == 2


def test_chunk_blocks_respects_the_100_limit():
    blocks = nx.markdown_to_blocks("\n".join(f"- 줄 {i}" for i in range(250)))
    chunks = nx.chunk_blocks(blocks)
    assert [len(c) for c in chunks] == [100, 100, 50]


def test_blank_lines_do_not_become_blocks():
    assert nx.markdown_to_blocks("첫 줄\n\n\n둘째 줄\n") == nx.markdown_to_blocks(
        "첫 줄\n둘째 줄\n")


# Task 5: 마크다운 표를 노션 표 블록으로
_TABLE_MD = (
    "| 국가 | 매출 |\n"
    "|---|---|\n"
    "| 일본 | 55.1억 |\n"
    "| 미국 | 32.0억 |\n"
)


def test_table_becomes_a_notion_table_block():
    """⛔ 표가 코드블록으로 들어가면 노션에서 정렬도 복사도 안 되는 글자 더미다."""
    blocks = nx.markdown_to_blocks(_TABLE_MD)
    assert _types(blocks) == ["table"]
    table = blocks[0]["table"]
    assert table["table_width"] == 2
    assert table["has_column_header"] is True
    rows = table["children"]
    assert len(rows) == 3                      # 머리행 + 본문 2행
    assert rows[0]["table_row"]["cells"][0][0]["text"]["content"] == "국가"
    assert rows[2]["table_row"]["cells"][1][0]["text"]["content"] == "32.0억"


def test_table_rows_are_padded_to_the_declared_width():
    """⚠️ 셀 수가 table_width 와 다르면 400 이 난다 — 채우거나 자른다."""
    blocks = nx.markdown_to_blocks(
        "| a | b | c |\n|---|---|---|\n| 1 |\n| 1 | 2 | 3 | 4 |\n")
    rows = blocks[0]["table"]["children"]
    assert all(len(r["table_row"]["cells"]) == 3 for r in rows)


def test_text_around_a_table_is_kept():
    blocks = nx.markdown_to_blocks("앞 문단\n" + _TABLE_MD + "뒤 문단\n")
    assert _types(blocks) == ["paragraph", "table", "paragraph"]


def test_separator_line_alone_is_not_a_table():
    """구분선처럼 생긴 줄 하나로 표를 만들지 않는다."""
    assert _types(nx.markdown_to_blocks("|---|\n")) == ["paragraph"]


def test_long_table_says_it_was_cut():
    """⛔ 조용히 자르지 않는다 — 이 저장소가 최악으로 치는 실패 유형이다."""
    md = "| 국가 | 매출 |\n|---|---|\n" + "\n".join(
        f"| 국가{i} | {i}억 |" for i in range(150))
    blocks = nx.markdown_to_blocks(md)
    assert _types(blocks) == ["table", "paragraph"]
    assert len(blocks[0]["table"]["children"]) == 100
    note = _plain(blocks[1])
    assert "100" in note
    assert "151" in note          # 머리행 1 + 데이터 150


def test_short_table_gets_no_note():
    """자르지 않았으면 아무 말도 붙이지 않는다 — 매번 뜨는 안내는 곧 무시당한다."""
    assert _types(nx.markdown_to_blocks(_TABLE_MD)) == ["table"]


# Task 6: 목적지 해석 — DB 인가 페이지인가
def _stub_requests(monkeypatch, handler):
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        return handler(method, path, body)

    monkeypatch.setattr(nx, "_request", fake)
    return calls


@pytest.fixture
def no_cache(monkeypatch):
    """DB 는 테스트에서 타지 않는다 — 캐시 함수만 갈아 끼운다."""
    store = {}
    monkeypatch.setattr(nx, "_cached_database",
                        lambda uid, pid: store.get((uid, pid)))
    monkeypatch.setattr(
        nx, "_remember_database",
        lambda uid, pid, target: store.__setitem__(
            (uid, pid), {"database_id": target.database_id,
                         "data_source_id": target.data_source_id}))
    return store


def test_db_url_is_used_as_is(monkeypatch, no_cache):
    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            # ⛔ 2025-09-03 에서 DB 객체에는 스키마가 없다 — 여기 `properties` 를 두지 않는다.
            return {"id": _UUID, "data_sources": [{"id": "ds-1", "name": "셀라"}]}
        if path == "/v1/data_sources/ds-1":
            return {"id": "ds-1", "properties": {"이름": {"type": "title"}}}
        raise AssertionError(f"불필요한 호출: {method} {path}")

    _stub_requests(monkeypatch, handler)
    target = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert (target.database_id, target.data_source_id) == (_UUID, "ds-1")
    assert target.created is False
    assert target.properties == {"이름": "title"}


def test_schema_comes_from_the_data_source_not_the_database(monkeypatch, no_cache):
    """⛔ 2025-09-03 에서 DB 객체에는 스키마가 없다 — 여기서 틀리면 저장이 전부 죽는다."""
    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            return {"id": _UUID, "data_sources": [{"id": "ds-1"}]}   # properties 없음
        if path == "/v1/data_sources/ds-1":
            return {"id": "ds-1", "properties": {"이름": {"type": "title"}}}
        raise AssertionError(f"불필요한 호출: {method} {path}")

    _stub_requests(monkeypatch, handler)
    target = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert target.properties == {"이름": "title"}


def test_page_url_creates_the_database_once(monkeypatch, no_cache):
    created = {"count": 0}
    schema = {name: {"type": list(spec)[0]} for name, spec in nx.DB_PROPERTIES.items()}

    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            raise nx.NotionError("not_connected", "not a database", 404)
        if path == "/v1/databases/db-new":
            return {"id": "db-new", "data_sources": [{"id": "ds-new"}]}
        if path == "/v1/data_sources/ds-new":
            return {"id": "ds-new", "properties": schema}
        if path == f"/v1/pages/{_UUID}":
            return {"id": _UUID, "object": "page"}
        if path.startswith(f"/v1/blocks/{_UUID}/children"):
            return {"results": []}
        if method == "POST" and path == "/v1/databases":
            created["count"] += 1
            return {"id": "db-new", "data_sources": [{"id": "ds-new"}]}
        raise AssertionError(f"불필요한 호출: {method} {path}")

    _stub_requests(monkeypatch, handler)
    first = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert first.created is True
    assert created["count"] == 1

    # ⛔ 두 번째 저장에서 DB 가 또 생기면 안 된다 — 가장 흔할 실수다.
    second = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert created["count"] == 1
    assert second.database_id == "db-new"
    assert second.created is False
    # ⛔ 스키마가 비면 저장 단계가 "제목 속성이 없는 DB" 로 죽는다.
    assert second.properties.get("제목") == "title"


def test_existing_child_database_is_reused_without_cache(monkeypatch, no_cache):
    """캐시가 비어도 부모에서 찾는다 — 사용자가 DB 를 옮겨도 회복한다."""
    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            raise nx.NotionError("not_connected", "", 404)
        if path == f"/v1/pages/{_UUID}":
            return {"id": _UUID}
        if path.startswith(f"/v1/blocks/{_UUID}/children"):
            return {"results": [
                {"type": "child_page", "child_page": {"title": "셀라"}},
                {"id": "db-old", "type": "child_database",
                 "child_database": {"title": "셀라"}},
            ]}
        if path == "/v1/databases/db-old":
            return {"id": "db-old", "data_sources": [{"id": "ds-old"}]}
        if path == "/v1/data_sources/ds-old":
            return {"properties": {"제목": {"type": "title"}}}
        raise AssertionError(f"불필요한 호출: {method} {path}")

    _stub_requests(monkeypatch, handler)
    target = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert (target.database_id, target.created) == ("db-old", False)


def test_cached_database_that_was_deleted_falls_through_to_create(monkeypatch, no_cache):
    """캐시가 가리키는 DB 를 사용자가 지웠으면 다시 만든다 — 캐시를 맹신하지 않는다."""
    no_cache[(7, _UUID)] = {"database_id": "db-gone", "data_source_id": "ds-gone"}
    created = {"count": 0}

    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            raise nx.NotionError("not_connected", "", 404)
        if path == "/v1/databases/db-gone":
            raise nx.NotionError("not_connected", "", 404)
        if path == f"/v1/pages/{_UUID}":
            return {"id": _UUID}
        if path.startswith(f"/v1/blocks/{_UUID}/children"):
            return {"results": []}
        if method == "POST" and path == "/v1/databases":
            created["count"] += 1
            return {"id": "db-new", "data_sources": [{"id": "ds-new"}]}
        if path == "/v1/data_sources/ds-new":
            return {"properties": {"제목": {"type": "title"}}}
        raise AssertionError(f"불필요한 호출: {method} {path}")

    _stub_requests(monkeypatch, handler)
    target = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert created["count"] == 1
    assert target.database_id == "db-new"


def test_bad_url_raises_before_any_call(monkeypatch, no_cache):
    _stub_requests(monkeypatch, lambda *a, **k: pytest.fail("호출하면 안 된다"))
    with pytest.raises(nx.NotionError) as exc:
        nx.resolve_target(7, "https://example.com/x")
    assert exc.value.kind == "bad_request"


def test_forbidden_is_not_mistaken_for_a_page(monkeypatch, no_cache):
    """⛔ 403 을 404 처럼 삼키면 권한 문제가 '페이지인가 보다' 로 둔갑한다."""
    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            raise nx.NotionError("forbidden", "no access", 403)
        raise AssertionError(f"403 뒤로 더 진행하면 안 된다: {method} {path}")

    _stub_requests(monkeypatch, handler)
    with pytest.raises(nx.NotionError) as exc:
        nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert exc.value.kind == "forbidden"


def test_page_url_survives_the_400_notion_actually_returns(monkeypatch, no_cache):
    """⛔ 노션은 "이건 페이지다" 를 404 가 아니라 **400** 으로 답한다 (2026-09-08 실측).

    404 만 보고 넘어가면 페이지 주소를 준 사용자가 그 자리에서 죽는다.
    """
    schema = {name: {"type": list(spec)[0]} for name, spec in nx.DB_PROPERTIES.items()}
    created = {"count": 0}

    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            raise nx.NotionError(
                "bad_request",
                f"Provided database_id {_UUID} is a page, not a database. "
                "Use the pages API instead, or pass the ID of the database itself.",
                400)
        if path == "/v1/databases/db-new":
            return {"id": "db-new", "data_sources": [{"id": "ds-new"}], "properties": schema}
        if path == "/v1/data_sources/ds-new":
            return {"id": "ds-new", "properties": schema}
        if path == f"/v1/pages/{_UUID}":
            return {"id": _UUID, "object": "page"}
        if path.startswith(f"/v1/blocks/{_UUID}/children"):
            return {"results": []}
        if method == "POST" and path == "/v1/databases":
            created["count"] += 1
            return {"id": "db-new", "data_sources": [{"id": "ds-new"}], "properties": schema}
        raise AssertionError(f"불필요한 호출: {method} {path}")

    _stub_requests(monkeypatch, handler)
    target = nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert created["count"] == 1
    assert target.created is True
    assert target.properties.get("제목") == "title"


def test_child_scan_truncation_leaves_a_warning(monkeypatch, no_cache):
    """⛔ 1,000블록을 다 보고도 못 찾으면 DB 를 새로 만든다 — 흔적 없이 그러지 않는다."""
    warnings = []
    monkeypatch.setattr(nx.logger, "warning",
                        lambda event, **kw: warnings.append(event))

    def handler(method, path, body=None):
        if path == f"/v1/databases/{_UUID}":
            raise nx.NotionError("not_connected", "", 404)
        if path == f"/v1/pages/{_UUID}":
            return {"id": _UUID}
        if path.startswith(f"/v1/blocks/{_UUID}/children"):
            return {"results": [], "has_more": True, "next_cursor": "c"}
        if method == "POST" and path == "/v1/databases":
            return {"id": "db-new", "data_sources": [{"id": "ds-new"}]}
        if path == "/v1/data_sources/ds-new":
            return {"properties": {"제목": {"type": "title"}}}
        raise AssertionError(f"불필요한 호출: {method} {path}")

    _stub_requests(monkeypatch, handler)
    nx.resolve_target(7, f"https://www.notion.so/{_ID}")
    assert "notion_child_scan_truncated" in warnings


# Task 7: 저장 — 행 생성과 속성 채우기
@pytest.fixture
def our_target():
    return nx.Target(database_id="db-1", data_source_id="ds-1",
                     properties={"제목": "title", "날짜": "date",
                                 "종류": "select", "셀라 링크": "url"})


@pytest.fixture
def user_made_target():
    """사용자가 직접 만든 DB — 이름이 다르고 속성이 적다."""
    return nx.Target(database_id="db-2", data_source_id="ds-2",
                     properties={"Name": "title", "Tags": "multi_select"})


def test_save_creates_a_row_under_the_data_source(monkeypatch, our_target):
    def handler(method, path, body=None):
        if path == "/v1/pages":
            assert body["parent"] == {"type": "data_source_id",
                                      "data_source_id": "ds-1"}
            return {"id": "row-1", "url": "https://notion.so/row-1"}
        raise AssertionError(path)

    _stub_requests(monkeypatch, handler)
    result = nx.save(our_target, "2026년 일본 매출", "본문입니다", kind="답변",
                     link="http://ai.example/chat")
    assert result.url == "https://notion.so/row-1"
    assert result.skipped == []


def test_save_fills_our_properties(monkeypatch, our_target):
    seen = {}

    def handler(method, path, body=None):
        seen.update(body["properties"])
        return {"id": "row-1", "url": "u"}

    _stub_requests(monkeypatch, handler)
    nx.save(our_target, "제목입니다", "본문", kind="브리핑", link="http://x")
    assert seen["제목"]["title"][0]["text"]["content"] == "제목입니다"
    assert seen["종류"]["select"]["name"] == "브리핑"
    assert seen["셀라 링크"]["url"] == "http://x"
    assert "date" in seen["날짜"]


def test_user_made_db_gets_only_its_title_property(monkeypatch, user_made_target):
    """⛔ 남의 DB 스키마를 고치지 않는다. 모르는 속성을 보내면 400 이 난다."""
    seen = {}

    def handler(method, path, body=None):
        seen.update(body["properties"])
        return {"id": "row-1", "url": "u"}

    _stub_requests(monkeypatch, handler)
    result = nx.save(user_made_target, "제목입니다", "본문", kind="답변")
    assert list(seen) == ["Name"]                       # 이름이 달라도 찾는다
    assert seen["Name"]["title"][0]["text"]["content"] == "제목입니다"
    assert set(result.skipped) == {"날짜", "종류", "셀라 링크"}


def test_save_appends_blocks_beyond_the_first_hundred(monkeypatch, our_target):
    appended = []

    def handler(method, path, body=None):
        if path == "/v1/pages":
            assert len(body["children"]) == 100
            return {"id": "row-1", "url": "u"}
        if path == "/v1/blocks/row-1/children":
            appended.append(len(body["children"]))
            return {}
        raise AssertionError(path)

    _stub_requests(monkeypatch, handler)
    nx.save(our_target, "긴 답변", "\n".join(f"- 줄 {i}" for i in range(250)))
    assert appended == [100, 50]


def test_save_cleans_the_body_before_writing(monkeypatch, our_target):
    """⛔ 내부 테이블 경로가 노션 페이지에 남으면 안 된다."""
    captured = {}

    def handler(method, path, body=None):
        captured["children"] = body.get("children", [])
        return {"id": "row-1", "url": "u"}

    _stub_requests(monkeypatch, handler)
    nx.save(our_target, "t",
            "값 1,234억\n\n<details><summary>실행된 쿼리</summary>\n"
            "`skin1004-319714.x.y`\n</details>\n")
    dumped = str(captured["children"])
    assert "1,234억" in dumped
    assert "skin1004-319714" not in dumped


def test_save_refuses_a_database_without_a_title_property(monkeypatch):
    """⛔ 제목 속성이 없으면 **API 를 부르기 전에** 멈춘다 — 조용히 빈 행을 만들지 않는다.

    ⚠️ `bad_property` 다 (`bad_request` 가 아니다) — "노션 주소를 읽지 못했습니다" 문구가
       붙으면 사용자는 멀쩡한 URL 을 영원히 다시 붙여넣는다.
    """
    target = nx.Target(database_id="db-3", data_source_id="ds-3",
                       properties={"Tags": "multi_select"})
    _stub_requests(monkeypatch, lambda *a, **k: pytest.fail("호출하면 안 된다"))
    with pytest.raises(nx.NotionError) as exc:
        nx.save(target, "제목", "본문")
    assert exc.value.kind == "bad_property"


def test_save_refuses_when_data_source_id_is_empty(monkeypatch):
    """⛔ 빈 data_source_id 로 POST 하면 400 이 오해하기 좋은 문구로 나간다 — 미리 막는다."""
    target = nx.Target(database_id="db-4", data_source_id="",
                       properties={"제목": "title"})
    _stub_requests(monkeypatch, lambda *a, **k: pytest.fail("호출하면 안 된다"))
    with pytest.raises(nx.NotionError) as exc:
        nx.save(target, "제목", "본문")
    assert exc.value.kind == "bad_property"


def test_date_property_is_korea_time(monkeypatch, our_target):
    """⚠️ 호스트 TZ 가 KST 가 아니어도 한국 날짜를 찍는다."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    seen = {}

    def handler(method, path, body=None):
        seen.update(body["properties"])
        return {"id": "row-1", "url": "u"}

    _stub_requests(monkeypatch, handler)
    nx.save(our_target, "t", "본문")
    expected = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    assert seen["날짜"]["date"]["start"] == expected
