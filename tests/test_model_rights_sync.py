from __future__ import annotations

from types import SimpleNamespace


def test_sync_model_rights_retries_google_reads(monkeypatch):
    from google.oauth2 import service_account
    from googleapiclient import discovery

    from app.core import model_rights

    request_calls: list[dict] = []
    build_calls: list[dict] = []
    db_calls: list[str] = []

    class FakeRequest:
        def __init__(self, result):
            self.result = result

        def execute(self, **kwargs):
            request_calls.append(kwargs)
            return self.result

    class FakeValues:
        def get(self, **_kwargs):
            return FakeRequest({"values": []})

    class FakeSpreadsheets:
        def get(self, **_kwargs):
            return FakeRequest(
                {
                    "sheets": [
                        {"properties": {"title": "A"}},
                        {"properties": {"title": "B"}},
                    ]
                }
            )

        def values(self):
            return FakeValues()

    class FakeService:
        def spreadsheets(self):
            return FakeSpreadsheets()

    def fake_build(*_args, **kwargs):
        build_calls.append(kwargs)
        return FakeService()

    credential_paths: list[str] = []
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(
        model_rights,
        "get_settings",
        lambda: SimpleNamespace(google_application_credentials="test-key.json"),
    )
    monkeypatch.setattr(
        service_account.Credentials,
        "from_service_account_file",
        lambda path, **_kwargs: credential_paths.append(path) or object(),
    )
    monkeypatch.setattr(discovery, "build", fake_build)
    monkeypatch.setattr(model_rights, "ensure_model_rights_tables", lambda: None)
    monkeypatch.setattr(
        model_rights,
        "execute",
        lambda sql, *_args, **_kwargs: db_calls.append(sql),
    )

    stats = model_rights.sync_model_rights()

    assert stats == {"models": 0, "periods": 0, "tabs": 2}
    assert credential_paths == ["test-key.json"]
    assert build_calls[0]["num_retries"] == 2
    assert request_calls == [
        {"num_retries": 2},
        {"num_retries": 2},
        {"num_retries": 2},
    ]
    assert db_calls == [
        "DELETE FROM model_right_periods",
        "DELETE FROM model_rights",
    ]
