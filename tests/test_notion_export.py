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
    text = "본문입니다.\n\n<!-- followup: [\"다음 질문\"] -->\n"
    assert "followup" not in nx.clean_for_notion(text)
