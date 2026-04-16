"""Unit tests for app.knowledge_map.ast_parser."""
from __future__ import annotations
from pathlib import Path

import pytest

from app.knowledge_map.ast_parser import parse_python_file, PythonNode

FIXTURE = Path(__file__).parent / "fixtures" / "knowledge_map" / "sample_module.py"


def test_parse_python_file_returns_nodes() -> None:
    result = parse_python_file(FIXTURE)
    assert result.module_doc == "Sample module for AST parser tests."
    assert len(result.imports) >= 3
    assert "app.core.llm.get_flash_client" in result.imports or "app.core.llm" in str(result.imports)
    assert len(result.classes) == 1
    assert len(result.functions) == 2  # top_level_func + async_func


def test_parse_python_file_class_details() -> None:
    result = parse_python_file(FIXTURE)
    cls = result.classes[0]
    assert cls.name == "SampleAgent"
    assert cls.docstring == "Sample agent class."
    method_names = {m.name for m in cls.methods}
    assert method_names == {"__init__", "run"}
    assert cls.line_start > 0
    assert cls.line_end > cls.line_start


def test_parse_python_file_function_details() -> None:
    result = parse_python_file(FIXTURE)
    func_by_name = {f.name: f for f in result.functions}
    assert func_by_name["top_level_func"].docstring == "Double x."
    assert func_by_name["async_func"].is_async is True
    assert func_by_name["top_level_func"].is_async is False


def test_parse_python_file_handles_syntax_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n    pass\n", encoding="utf-8")
    result = parse_python_file(bad)
    assert result is None or result.parse_error is not None
