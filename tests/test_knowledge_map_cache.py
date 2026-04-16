"""Unit tests for app.knowledge_map.cache."""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from app.knowledge_map.cache import FileCache, file_fingerprint


def test_file_fingerprint_stable(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("print('hi')\n", encoding="utf-8")
    fp1 = file_fingerprint(f)
    fp2 = file_fingerprint(f)
    assert fp1 == fp2
    assert "sha256" in fp1
    assert "mtime" in fp1


def test_file_fingerprint_changes_on_edit(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("print('hi')\n", encoding="utf-8")
    fp1 = file_fingerprint(f)
    f.write_text("print('bye')\n", encoding="utf-8")
    fp2 = file_fingerprint(f)
    assert fp1["sha256"] != fp2["sha256"]


def test_cache_load_missing_returns_empty(tmp_path: Path) -> None:
    cache = FileCache(tmp_path / "cache.json")
    assert cache.load() == {}


def test_cache_roundtrip(tmp_path: Path) -> None:
    cache_file = tmp_path / "cache.json"
    cache = FileCache(cache_file)
    data = {"a.py": {"sha256": "abc", "mtime": 1.0}}
    cache.save(data)
    assert cache_file.exists()
    assert cache.load() == data


def test_is_changed_detects_new_file(tmp_path: Path) -> None:
    f = tmp_path / "new.py"
    f.write_text("x = 1\n", encoding="utf-8")
    cache = FileCache(tmp_path / "c.json")
    assert cache.is_changed(f, previous={}) is True


def test_is_changed_skips_unchanged(tmp_path: Path) -> None:
    f = tmp_path / "same.py"
    f.write_text("x = 1\n", encoding="utf-8")
    cache = FileCache(tmp_path / "c.json")
    previous = {str(f): file_fingerprint(f)}
    assert cache.is_changed(f, previous=previous) is False
