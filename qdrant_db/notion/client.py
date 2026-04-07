"""
Notion API wrapper with retry support.
"""

import time

import requests
from notion_client import Client
from notion_client.errors import HTTPResponseError

from config import get_logger, settings


NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

logger = get_logger(__name__)


def format_uuid(id_str: str) -> str:
    """Convert a 32-char Notion id into UUID format when possible."""
    compact = id_str.replace("-", "")
    if len(compact) != 32:
        return id_str
    return f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:]}"


def retry_request(func, max_retries: int = 3, delay: int = 2):
    """Retry transient API errors with incremental backoff."""
    for attempt in range(max_retries):
        try:
            return func()
        except (HTTPResponseError, requests.exceptions.RequestException) as exc:
            status = getattr(exc, "status", None) or getattr(getattr(exc, "response", None), "status_code", None)
            if status in [429, 502, 503, 504] and attempt < max_retries - 1:
                wait_time = delay * (attempt + 1)
                logger.warning("Notion API transient error | status=%s retry_in=%ss", status, wait_time)
                time.sleep(wait_time)
                continue

            logger.exception("Notion API request failed")
            raise


class NotionClient:
    """Thin wrapper around the official Notion client."""

    def __init__(self, token: str = None):
        self._token = token or settings.notion_token
        self._client = Client(auth=self._token)

    def get_page(self, page_id: str) -> dict:
        formatted_id = format_uuid(page_id)
        return retry_request(lambda: self._client.pages.retrieve(page_id=formatted_id))

    def get_blocks(self, block_id: str, cursor: str = None) -> dict:
        formatted_id = format_uuid(block_id)
        params = {"block_id": formatted_id}
        if cursor:
            params["start_cursor"] = cursor
        return retry_request(lambda: self._client.blocks.children.list(**params))

    def query_database(self, database_id: str, cursor: str = None) -> dict:
        formatted_id = format_uuid(database_id)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        def do_request():
            url = f"{NOTION_API_BASE}/databases/{formatted_id}/query"
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response.json()

        return retry_request(do_request)
