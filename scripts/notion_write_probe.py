# -*- coding: utf-8 -*-
"""노션 쓰기 관통 확인 — 수동 실행.

이 프로젝트는 `2025-09-03` 을 써 본 적이 없다(전부 `2022-06-28` 읽기 전용).
DB 생성 → data_source_id 획득 → 행 생성까지 한 번 관통시켜 본 뒤 나머지를 짓는다.

    python scripts/notion_write_probe.py <노션_페이지_URL>

⚠️ 콘솔이 cp949 다 — 아래 래핑이 없으면 한글 출력에서 죽는다.
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.core import notion_export as nx  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python scripts/notion_write_probe.py <노션_페이지_URL>")
        return 2
    if not nx.is_enabled():
        print("NOTION_WRITE_TOKEN 이 비어 있다. .env 에 넣고 다시 실행할 것.")
        return 2

    page_id = nx.parse_page_url(sys.argv[1])
    print(f"page_id = {page_id}")
    if not page_id:
        return 2

    created = nx._request("POST", "/v1/databases", {
        "parent": {"type": "page_id", "page_id": page_id},
        "title": [{"type": "text", "text": {"content": "셀라 (probe)"}}],
        "initial_data_source": {"properties": nx.DB_PROPERTIES},
    })
    print(f"database_id      = {created.get('id')}")
    sources = created.get("data_sources") or []
    print(f"data_sources     = {sources}")
    if not sources:
        print("⛔ data_sources 가 비었다 — 응답 형태를 확인할 것")
        return 1

    row = nx._request("POST", "/v1/pages", {
        "parent": {"type": "data_source_id", "data_source_id": sources[0]["id"]},
        "properties": {"제목": {"title": [{"type": "text",
                                          "text": {"content": "관통 확인"}}]}},
        "children": [{"object": "block", "type": "paragraph",
                      "paragraph": {"rich_text": [{"type": "text",
                                                   "text": {"content": "성공"}}]}}],
    })
    print(f"row url          = {row.get('url')}")
    print("✅ 관통 성공 — 만들어진 probe DB 는 노션에서 지워도 된다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
