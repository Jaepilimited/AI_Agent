from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api import conversation_api


@pytest.mark.asyncio
async def test_add_message_stores_distinct_raw_content_and_nulls_same_raw_content(monkeypatch):
    inserts: list[tuple[str, tuple]] = []
    updates: list[tuple[str, tuple]] = []
    fetch_rows = [
        {"id": 101, "role": "user", "content": "정제 질문", "raw_content": "@@매출 정제 질문", "created_at": "2026-08-27 09:00:00"},
        {"id": 102, "role": "user", "content": "같은 질문", "raw_content": None, "created_at": "2026-08-27 09:01:00"},
    ]

    async def fake_fetch_one(sql: str, params: tuple = ()):
        if "FROM conversations" in sql:
            return {"id": "convo-1", "title": "기존 제목"}
        if "FROM messages" in sql:
            return fetch_rows.pop(0)
        raise AssertionError(sql)

    async def fake_execute_lastid(sql: str, params: tuple = ()):
        inserts.append((sql, params))
        return 101 if len(inserts) == 1 else 102

    async def fake_execute(sql: str, params: tuple = ()):
        updates.append((sql, params))
        return 1

    monkeypatch.setattr(conversation_api, "_db_fetch_one", fake_fetch_one)
    monkeypatch.setattr(conversation_api, "_db_execute_lastid", fake_execute_lastid)
    monkeypatch.setattr(conversation_api, "_db_execute", fake_execute)
    monkeypatch.setattr(conversation_api, "anon_id_for", lambda _user_id: "anon-1")

    long_content = "정" * 5001
    long_raw = "@@" + ("원" * 5001)
    first = await conversation_api.add_message(
        "convo-1",
        conversation_api.AddMessageRequest(role="user", content=long_content, raw_content=long_raw),
        user=SimpleNamespace(id=7),
    )
    second = await conversation_api.add_message(
        "convo-1",
        conversation_api.AddMessageRequest(role="user", content="같은 질문", raw_content="같은 질문"),
        user=SimpleNamespace(id=7),
    )

    assert first.raw_content == "@@매출 정제 질문"
    assert second.raw_content is None
    assert len(inserts) == 2
    assert "raw_content" in inserts[0][0]
    assert inserts[0][1] == ("convo-1", "user", long_content, long_raw[:4000])
    assert inserts[1][1] == ("convo-1", "user", "같은 질문", None)
    assert len(updates) == 2


@pytest.mark.asyncio
async def test_get_conversation_returns_raw_content(monkeypatch):
    async def fake_fetch_one(sql: str, params: tuple = ()):
        assert "FROM conversations" in sql
        return {"id": "convo-1", "title": "대화", "model": "gpt"}

    async def fake_fetch_all(sql: str, params: tuple = ()):
        assert "raw_content" in sql
        return [{
            "id": 9,
            "role": "user",
            "content": "정제 질문",
            "raw_content": "@@매출 정제 질문",
            "created_at": "2026-08-27 09:02:00",
        }]

    monkeypatch.setattr(conversation_api, "_db_fetch_one", fake_fetch_one)
    monkeypatch.setattr(conversation_api, "_db_fetch_all", fake_fetch_all)
    monkeypatch.setattr(conversation_api, "anon_id_for", lambda _user_id: "anon-1")

    detail = await conversation_api.get_conversation("convo-1", user=SimpleNamespace(id=7))

    assert detail.messages[0].content == "정제 질문"
    assert detail.messages[0].raw_content == "@@매출 정제 질문"


@pytest.mark.asyncio
async def test_add_message_auto_title_uses_content_not_raw_content(monkeypatch):
    updates: list[tuple[str, tuple]] = []

    async def fake_fetch_one(sql: str, params: tuple = ()):
        if "FROM conversations" in sql:
            return {"id": "convo-1", "title": "New Chat"}
        if "FROM messages" in sql:
            return {"id": 88, "role": "user", "content": "정제 질문", "raw_content": "@@매출 정제 질문", "created_at": "2026-08-27 09:03:00"}
        raise AssertionError(sql)

    async def fake_execute(sql: str, params: tuple = ()):
        updates.append((sql, params))
        return 1

    async def fake_execute_lastid(*_args, **_kwargs):
        return 88

    monkeypatch.setattr(conversation_api, "_db_fetch_one", fake_fetch_one)
    monkeypatch.setattr(conversation_api, "_db_execute", fake_execute)
    monkeypatch.setattr(conversation_api, "_db_execute_lastid", fake_execute_lastid)
    monkeypatch.setattr(conversation_api, "anon_id_for", lambda _user_id: "anon-1")

    await conversation_api.add_message(
        "convo-1",
        conversation_api.AddMessageRequest(role="user", content="정제 질문", raw_content="@@매출 정제 질문"),
        user=SimpleNamespace(id=7),
    )

    assert updates[0][1] == ("정제 질문", "convo-1")
    assert not updates[0][1][0].startswith("@@")


def test_frontend_save_button_uses_raw_question_when_present():
    source = Path("app/frontend/chat.js").read_text(encoding="utf-8")
    assert "m.raw_content || m.content" in source


def test_frontend_bubble_keeps_rendering_content():
    source = Path("app/frontend/chat.js").read_text(encoding="utf-8")
    assert 'appendMessage(m.role, m.content, false, m.created_at)' in source
    assert 'appendMessage(m.role, m.raw_content' not in source


def test_ensure_message_columns_is_idempotent(monkeypatch):
    checks = [None, {"ok": 1}]
    executed: list[str] = []

    monkeypatch.setattr(conversation_api, "fetch_one", lambda *_a, **_k: checks.pop(0))
    monkeypatch.setattr(conversation_api, "execute", lambda sql, params=(): executed.append(sql) or 1)

    conversation_api.ensure_message_columns()
    conversation_api.ensure_message_columns()

    assert executed == ["ALTER TABLE messages ADD COLUMN raw_content TEXT NULL"]
