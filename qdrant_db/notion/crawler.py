"""
Notion page and database crawler.
"""

from dataclasses import dataclass
from typing import Optional

from config import get_logger

from .client import NotionClient


logger = get_logger(__name__)


@dataclass
class PageData:
    """Collected page data."""

    id: str
    title: str
    content: str
    url: str
    breadcrumb: list[str]
    breadcrumb_path: str
    sections: list[dict]


class BlockParser:
    """Convert Notion blocks into plain text."""

    TEXT_BLOCK_TYPES = [
        "paragraph",
        "heading_1",
        "heading_2",
        "heading_3",
        "bulleted_list_item",
        "numbered_list_item",
        "quote",
        "callout",
        "toggle",
        "to_do",
    ]

    SKIP_BLOCK_TYPES = ["divider", "table_of_contents", "breadcrumb"]

    @classmethod
    def parse_block(cls, block: dict) -> tuple[str, Optional[str]]:
        block_type = block.get("type", "")

        if block_type in cls.SKIP_BLOCK_TYPES:
            return "", None

        if block_type == "heading_1":
            text = cls._extract_rich_text(block, block_type)
            return f"\n# {text}\n", text

        if block_type == "heading_2":
            text = cls._extract_rich_text(block, block_type)
            return f"\n## {text}\n", text

        if block_type == "heading_3":
            text = cls._extract_rich_text(block, block_type)
            return f"\n### {text}\n", text

        if block_type == "bulleted_list_item":
            text = cls._extract_rich_text(block, block_type)
            return f"- {text}", None

        if block_type == "numbered_list_item":
            text = cls._extract_rich_text(block, block_type)
            return f"- {text}", None

        if block_type == "code":
            code_data = block.get("code", {})
            language = code_data.get("language", "")
            text = cls._extract_rich_text(block, block_type)
            return f"\n```{language}\n{text}\n```\n", None

        if block_type in ["quote", "callout"]:
            text = cls._extract_rich_text(block, block_type)
            return f"> {text}", None

        if block_type == "toggle":
            text = cls._extract_rich_text(block, block_type)
            return f"- {text}", None

        if block_type in cls.TEXT_BLOCK_TYPES:
            text = cls._extract_rich_text(block, block_type)
            return text, None

        return "", None

    @staticmethod
    def _extract_rich_text(block: dict, block_type: str) -> str:
        rich_text = block.get(block_type, {}).get("rich_text", [])
        return "".join(text.get("plain_text", "") for text in rich_text)


class NotionCrawler:
    """Crawl Notion pages and databases recursively."""

    RECURSIVE_BLOCK_TYPES = {
        "toggle",
        "bulleted_list_item",
        "numbered_list_item",
        "quote",
        "callout",
        "to_do",
        "column",
        "column_list",
    }

    def __init__(self, client: NotionClient = None, max_pages: int = None, skip_databases: bool = False):
        self._client = client or NotionClient()
        self._parser = BlockParser()
        self._max_pages = max_pages
        self._skip_databases = skip_databases
        self._page_count = 0

    def crawl(self, root_id: str) -> list[PageData]:
        self._page_count = 0

        try:
            self._client.get_page(root_id)
            logger.info("Root id detected as page: %s", root_id)
            return self._crawl_page(root_id, breadcrumb=[])
        except Exception as exc:
            if "is a database" in str(exc):
                logger.info("Root id detected as database: %s", root_id)
                return self._crawl_database(root_id, breadcrumb=[])

            logger.exception("Failed to resolve root id: %s", root_id)
            return []

    def _crawl_page(self, page_id: str, breadcrumb: list[str]) -> list[PageData]:
        if self._max_pages and self._page_count >= self._max_pages:
            return []

        pages = []
        indent = "  " * len(breadcrumb)

        try:
            page = self._client.get_page(page_id)
            title = self._get_page_title(page)
            url = page.get("url", "")
            logger.info("%s[PAGE] %s", indent, title)
        except Exception:
            logger.exception("%sPage retrieval failed (%s)", indent, page_id)
            return pages

        current_breadcrumb = breadcrumb + [title]
        breadcrumb_path = " > ".join(current_breadcrumb)
        content, sections = self._get_page_content(page_id)

        if content.strip():
            pages.append(
                PageData(
                    id=page_id,
                    title=title,
                    content=content,
                    url=url,
                    breadcrumb=current_breadcrumb,
                    breadcrumb_path=breadcrumb_path,
                    sections=sections,
                )
            )
            self._page_count += 1

        if self._max_pages and self._page_count >= self._max_pages:
            return pages

        pages.extend(self._crawl_children(page_id, current_breadcrumb))
        return pages

    def _get_page_content(self, page_id: str, depth: int = 0) -> tuple[str, list[dict]]:
        if depth > 5:
            return "", []

        texts = []
        sections = []
        current_section = {"title": None, "content": []}
        cursor = None

        while True:
            try:
                response = self._client.get_blocks(page_id, cursor)
            except Exception:
                logger.exception("Block retrieval failed for %s", page_id)
                break

            for block in response.get("results", []):
                block_type = block.get("type", "")
                text, section_title = self._parser.parse_block(block)

                if text:
                    texts.append(text)
                    if section_title:
                        if current_section["content"]:
                            sections.append(
                                {
                                    "title": current_section["title"],
                                    "content": "\n".join(current_section["content"]),
                                }
                            )
                        current_section = {"title": section_title, "content": []}
                    else:
                        current_section["content"].append(text)

                if block.get("has_children") and block_type in self.RECURSIVE_BLOCK_TYPES:
                    child_content, _child_sections = self._get_page_content(block["id"], depth + 1)
                    if child_content:
                        texts.append(child_content)
                        current_section["content"].append(child_content)

            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")

        if current_section["content"]:
            sections.append(
                {
                    "title": current_section["title"],
                    "content": "\n".join(current_section["content"]),
                }
            )

        return "\n".join(texts), sections

    def _crawl_children(self, page_id: str, breadcrumb: list[str]) -> list[PageData]:
        if self._max_pages and self._page_count >= self._max_pages:
            return []

        pages = []
        cursor = None
        indent = "  " * len(breadcrumb)

        while True:
            if self._max_pages and self._page_count >= self._max_pages:
                break

            try:
                response = self._client.get_blocks(page_id, cursor)
            except Exception:
                logger.exception("%sChild block retrieval failed for %s", indent, page_id)
                break

            for block in response.get("results", []):
                if self._max_pages and self._page_count >= self._max_pages:
                    break

                block_type = block.get("type")

                if block_type == "child_page":
                    pages.extend(self._crawl_page(block["id"], breadcrumb))
                elif block_type == "child_database":
                    db_title = block.get("child_database", {}).get("title", "Untitled DB")
                    if self._skip_databases:
                        logger.info("%s  [DB] %s (skipped)", indent, db_title)
                        continue

                    logger.info("%s  [DB] %s", indent, db_title)
                    pages.extend(self._crawl_database(block["id"], breadcrumb))

            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")

        return pages

    def _crawl_database(self, database_id: str, breadcrumb: list[str]) -> list[PageData]:
        pages = []
        cursor = None

        while True:
            try:
                response = self._client.query_database(database_id, cursor)
            except Exception:
                logger.exception("Database query failed for %s", database_id)
                break

            for item in response.get("results", []):
                pages.extend(self._crawl_page(item["id"], breadcrumb))

            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")

        return pages

    @staticmethod
    def _get_page_title(page: dict) -> str:
        properties = page.get("properties", {})
        for prop in properties.values():
            if prop.get("type") == "title":
                title_list = prop.get("title", [])
                if title_list:
                    return title_list[0].get("plain_text", "Untitled")

        if page.get("type") == "child_page":
            return page.get("child_page", {}).get("title", "Untitled")

        return "Untitled"
