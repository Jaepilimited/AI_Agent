"""
Notion API 클라이언트 (retry 포함)
"""

import time
import requests
from notion_client import Client
from notion_client.errors import HTTPResponseError

from app.core.config import settings


def _format_uuid(id_str: str) -> str:
    """32자리 ID를 UUID 형식으로 변환"""
    clean = id_str.replace("-", "")
    if len(clean) != 32:
        return id_str
    return f"{clean[:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:]}"


def _retry(func, max_retries: int = 3, delay: int = 2):
    """429/5xx/타임아웃 에러 시 재시도"""
    for attempt in range(max_retries):
        try:
            return func()
        except (HTTPResponseError, requests.exceptions.RequestException) as e:
            status = getattr(e, "status", None) or getattr(
                getattr(e, "response", None), "status_code", None
            )
            is_retryable = status in [429, 502, 503, 504] or isinstance(
                e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)
            )
            if is_retryable and attempt < max_retries - 1:
                wait = delay * (attempt + 1)
                time.sleep(wait)
            else:
                raise


class NotionClient:
    """Notion API 클라이언트"""

    def __init__(self, token: str = None):
        self._token = token or settings.notion_token
        self._client = Client(auth=self._token, timeout_ms=30_000)

    def get_page(self, page_id: str) -> dict:
        fid = _format_uuid(page_id)
        return _retry(lambda: self._client.pages.retrieve(page_id=fid))

    def get_blocks(self, block_id: str, cursor: str = None) -> dict:
        fid = _format_uuid(block_id)
        params = {"block_id": fid}
        if cursor:
            params["start_cursor"] = cursor
        return _retry(lambda: self._client.blocks.children.list(**params))

    def query_database(self, database_id: str, cursor: str = None) -> dict:
        """
        database_id로 DB 엔트리 조회.
        Notion SDK v2에서 database_id와 data_source_id가 분리됨:
          1) database_id 직접 시도
          2) 실패 시 search API로 data_source_id를 찾아 재시도
        """
        fid = _format_uuid(database_id)
        kwargs = {}
        if cursor:
            kwargs["start_cursor"] = cursor

        try:
            return _retry(lambda: self._client.data_sources.query(fid, **kwargs))
        except Exception:
            pass

        data_source_id = self._resolve_data_source_id(fid)
        if not data_source_id:
            raise RuntimeError(
                f"database {fid}에 대한 data_source_id를 찾을 수 없음. "
                "integration에 해당 DB를 공유했는지 확인하세요."
            )
        return _retry(lambda: self._client.data_sources.query(data_source_id, **kwargs))

    def _resolve_data_source_id(self, database_id: str) -> str | None:
        """
        search API로 database_id에 매핑되는 data_source_id 반환.
        해당 DB 엔트리 페이지 중 integration이 접근 가능한 것을 찾아 parent에서 추출.
        최대 3페이지(300건)만 탐색 - 접근 가능한 엔트리라면 최근 수정 순 상위에 나타남.
        """
        try:
            cursor = None
            pages_searched = 0
            max_pages = 3
            while pages_searched < max_pages:
                params = {"filter": {"value": "page", "property": "object"}, "page_size": 100}
                if cursor:
                    params["start_cursor"] = cursor
                resp = _retry(lambda: self._client.search(**params))
                for page in resp.get("results", []):
                    parent = page.get("parent", {})
                    if parent.get("database_id") == database_id:
                        return parent.get("data_source_id")
                pages_searched += 1
                if not resp.get("has_more"):
                    break
                cursor = resp.get("next_cursor")
        except Exception:
            pass
        return None

    def get_page_title(self, page: dict) -> str:
        """페이지 제목 추출"""
        properties = page.get("properties", {})
        for prop in properties.values():
            if prop.get("type") == "title":
                title_list = prop.get("title", [])
                if title_list:
                    return title_list[0].get("plain_text", "Untitled")
        if page.get("type") == "child_page":
            return page.get("child_page", {}).get("title", "Untitled")
        return "Untitled"
