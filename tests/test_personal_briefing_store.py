from datetime import date, datetime

from app.core import personal_briefing_store as store


def test_get_snapshot_is_owner_and_date_scoped(monkeypatch):
    seen = {}
    monkeypatch.setattr(store, "fetch_one", lambda sql, p: seen.update(sql=sql, p=p) or None)
    assert store.get_snapshot(7, date(2026, 8, 25)) is None
    assert "user_id = %s" in seen["sql"] and "for_date = %s" in seen["sql"]
    assert seen["p"] == (7, date(2026, 8, 25))


def test_put_snapshot_strips_transient_mail_content(monkeypatch):
    captured = {}
    monkeypatch.setattr(store, "execute", lambda sql, p=(): captured.update(sql=sql, p=p) or 1)
    store.put_snapshot(
        7, date(2026, 8, 25), "hash",
        {"status": "ready", "items": []},
        {"status": "ready", "items": [{"id": "m1", "subject": "S", "snippet": "secret"}]},
        [], datetime(2026, 8, 25, 8, 30),
    )
    assert "secret" not in " ".join(map(str, captured["p"]))


def test_put_snapshot_strips_nested_transient_aliases(monkeypatch):
    captured = {}
    monkeypatch.setattr(store, "execute", lambda sql, p=(): captured.update(sql=sql, p=p) or 1)
    store.put_snapshot(
        7,
        date(2026, 8, 25),
        "hash",
        {
            "items": [{
                "accessToken": "access-secret",
                "id_token": "id-secret",
                "attachment-id": "attachment-secret",
                "nested": {"Refresh-Token": "refresh-secret", "BodyText": "body-secret"},
            }],
        },
        {"items": [{"snippet_text": "snippet-secret", "payloadData": "payload-secret"}]},
        [{"attachmentId": "priority-attachment-secret"}],
        datetime(2026, 8, 25, 8, 30),
    )
    serialized = " ".join(map(str, captured["p"]))
    for secret in (
        "access-secret", "id-secret", "attachment-secret", "refresh-secret",
        "body-secret", "snippet-secret", "payload-secret", "priority-attachment-secret",
    ):
        assert secret not in serialized


def test_get_snapshot_decodes_all_json(monkeypatch):
    monkeypatch.setattr(store, "fetch_one", lambda *_a, **_k: {
        "google_account_hash": "h", "calendar_json": '{"status":"ready"}',
        "mail_json": '{"status":"empty"}', "priorities_json": '[]',
        "generated_at": datetime(2026, 8, 25, 8, 30),
    })
    row = store.get_snapshot(7, date(2026, 8, 25))
    assert row["calendar"]["status"] == "ready"
    assert row["mail"]["status"] == "empty"
    assert row["priorities"] == []


def test_delete_and_cleanup_have_narrow_predicates(monkeypatch):
    calls = []
    monkeypatch.setattr(store, "execute", lambda sql, p=(): calls.append((sql, p)) or 1)
    store.delete_for_user(7)
    assert calls[-1][1] == (7,)
    store.cleanup(date(2026, 8, 24))
    assert "for_date < %s" in calls[-1][0]
    assert calls[-1][1] == (date(2026, 8, 24),)
