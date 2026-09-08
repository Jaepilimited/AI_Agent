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


def test_set_target_without_a_time_uses_the_default(monkeypatch):
    """⛔ 첫 등록은 시각을 주지 않는다 — 여기서 죽으면 채팅 등록이 통째로 실패한다."""
    seen = {}

    def fake_execute(sql, params=()):
        seen["params"] = params
        return 1

    monkeypatch.setattr(nb, "execute", fake_execute)
    nb.set_target(7, "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b")
    assert nb.DEFAULT_SEND_AT in seen["params"]


def test_set_target_keeps_muted_when_none_is_given(monkeypatch):
    """⚠️ 시각만 저장하는 요청이 항목 설정을 지우면 안 된다.

    `None`(안 바꿈)과 `[]`(전부 받기)는 뜻이 다르다.
    """
    seen = {}

    def fake_execute(sql, params=()):
        seen["params"] = params
        return 1

    monkeypatch.setattr(nb, "execute", fake_execute)
    url = "https://www.notion.so/24f1a2b3c4d54e6f8a9b0c1d2e3f4a5b"

    nb.set_target(7, url, muted=None)
    assert seen["params"][-1] == 0        # 기존 설정을 유지한다

    nb.set_target(7, url, muted=[])
    assert seen["params"][-1] == 1        # 빈 목록은 "전부 받기" — 바꾼다


def test_mask_is_empty_for_a_bad_url():
    """가릴 것이 없으면 빈 문자열 — 엉뚱한 문자열을 만들어내지 않는다."""
    assert nb.mask("https://example.com/x") == ""
    assert nb.mask("") == ""
