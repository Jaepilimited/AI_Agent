# -*- coding: utf-8 -*-
"""브리핑 노션 자동 배송 회귀.

⛔ DB 도 네트워크도 타지 않는다 — 저장 계층과 `_request` 를 monkeypatch 한다.
"""
import pytest

from app.core import jandi_briefing as jb
from app.core import notion_briefing as nb


def test_page_url_validation_reuses_the_engine():
    """⛔ 아무 URL 이나 받으면 서버가 남의 메일 요약을 아무 데나 쓰는 기계가 된다."""
    assert nb.is_valid_page_url(
        "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b") is True
    assert nb.is_valid_page_url("https://example.com/x") is False
    assert nb.is_valid_page_url("https://skin1004.notion.site/abc") is False
    assert nb.is_valid_page_url("") is False


def test_mask_hides_the_id():
    masked = nb.mask("https://www.notion.so/회의록-24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b")
    assert "24f1a2b3" not in masked
    assert "notion.so" in masked


def test_send_time_choices_come_from_jandi_not_a_copy():
    """⛔ 두 화면이 다른 목록을 보이면 사용자가 혼란스럽다 — 사본을 만들지 않는다."""
    assert nb.SEND_TIME_CHOICES is jb.SEND_TIME_CHOICES


def test_sections_come_from_jandi_not_a_copy():
    assert nb.SECTIONS is jb.SECTIONS
