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
