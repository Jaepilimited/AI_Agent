"""Unit tests for app.knowledge_map.md_parser."""
from __future__ import annotations
from pathlib import Path

import pytest

from app.knowledge_map.md_parser import parse_markdown_file, MarkdownNode

FIXTURE = Path(__file__).parent / "fixtures" / "knowledge_map" / "sample_doc.md"


def test_parse_markdown_title() -> None:
    result = parse_markdown_file(FIXTURE)
    assert result.title == "Main Title"


def test_parse_markdown_headings() -> None:
    result = parse_markdown_file(FIXTURE)
    heading_texts = [h.text for h in result.headings]
    assert "Main Title" in heading_texts
    assert "Section One" in heading_texts
    assert "Subsection A" in heading_texts
    assert "Section Two" in heading_texts

    by_level = {h.text: h.level for h in result.headings}
    assert by_level["Main Title"] == 1
    assert by_level["Section One"] == 2
    assert by_level["Subsection A"] == 3


def test_parse_markdown_links() -> None:
    result = parse_markdown_file(FIXTURE)
    targets = [l.target for l in result.links]
    assert "../specs/design.md" in targets
    assert "https://example.com" in targets


def test_parse_markdown_date_from_filename(tmp_path: Path) -> None:
    f = tmp_path / "update_log_2026-03-25.md"
    f.write_text("# Daily\n", encoding="utf-8")
    result = parse_markdown_file(f)
    assert result.filename_date == "2026-03-25"


def test_parse_markdown_no_date() -> None:
    result = parse_markdown_file(FIXTURE)
    assert result.filename_date is None
