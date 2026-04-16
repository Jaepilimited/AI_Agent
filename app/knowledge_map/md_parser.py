"""Markdown structural parser — H1-H6 headings, links, date from filename."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class Heading:
    level: int
    text: str
    line: int


@dataclass
class Link:
    text: str
    target: str


@dataclass
class MarkdownNode:
    path: Path
    title: Optional[str]
    headings: list[Heading] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    filename_date: Optional[str] = None
    word_count: int = 0


def parse_markdown_file(path: Path) -> MarkdownNode:
    """Parse a .md file. Never raises — returns an empty node on error."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return MarkdownNode(path=path, title=None)

    node = MarkdownNode(path=path, title=None)

    # Headings (with line numbers)
    for idx, line in enumerate(content.splitlines(), start=1):
        m = _HEADING_RE.match(line)
        if m:
            node.headings.append(Heading(level=len(m.group(1)), text=m.group(2).strip(), line=idx))

    if node.headings and node.headings[0].level == 1:
        node.title = node.headings[0].text

    # Links
    for m in _LINK_RE.finditer(content):
        node.links.append(Link(text=m.group(1), target=m.group(2)))

    # Date from filename (for update_log_YYYY-MM-DD.md)
    dm = _DATE_RE.search(path.name)
    if dm:
        node.filename_date = dm.group(1)

    node.word_count = len(content.split())
    return node
