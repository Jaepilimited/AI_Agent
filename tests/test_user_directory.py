"""Entra enrollment preserves Cella people, permissions and account history."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import re
import sqlite3
import threading
from types import SimpleNamespace

import pytest
import requests

from app.core import user_directory as directory


TENANT = "11111111-1111-4111-8111-111111111111"
OID = "22222222-2222-4222-8222-222222222222"
OTHER_OID = "33333333-3333-4333-8333-333333333333"
NEW_OID = "44444444-4444-4444-8444-444444444444"
NOW = "2026-09-08 12:00:00"


def _claims(**changes):
    return {
        "tid": TENANT, "oid": OID, "acct": 0,
        "preferred_username": "alex@cravercorp.com", "name": "Alex",
        **changes,
    }


def _graph_user(oid=OID, **changes):
    return {
        "id": oid, "displayName": "Alex Entra", "mail": "alex@skin1004korea.com",
        "userPrincipalName": "alex@cravercorp.com", "department": "Marketing",
        "userType": "Member", "accountEnabled": True, **changes,
    }


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    config = SimpleNamespace(
        entra_tenant_id=TENANT, entra_client_id="test-client",
        entra_client_secret="test-secret",
        entra_employee_domains="cravercorp.com,skin1004korea.com",
    )
    monkeypatch.setattr(directory, "get_settings", lambda: config)
    real_gensalt = directory.bcrypt.gensalt
    monkeypatch.setattr(directory.bcrypt, "gensalt", lambda: real_gensalt(rounds=4))

    def no_network(*args, **kwargs):
        pytest.fail("The directory unit test attempted a real network request")

    monkeypatch.setattr(directory.requests, "post", no_network)
    monkeypatch.setattr(directory.requests, "get", no_network)
    return config


class _Cursor:
    """Translate MariaDB syntax; SQLite still executes the real queries."""

    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.raw.cursor()
        self.synthetic = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    def execute(self, sql, params=()):
        database = self.connection.database
        self.synthetic = None
        if "GET_LOCK(" in sql:
            acquired = database.lock_allowed and database.lock.acquire(timeout=3)
            self.synthetic = [{"acquired": int(acquired)}]
            return 1
        if "RELEASE_LOCK(" in sql:
            database.lock.release()
            database.releases += 1
            self.synthetic = [{"released": 1}]
            return 1
        if database.fail_on and database.fail_on(sql, params):
            raise sqlite3.OperationalError("injected storage failure")
        sql = re.sub(r"\s+FOR UPDATE\b", "", sql, flags=re.IGNORECASE)
        sql = sql.replace("%s", "?")
        sql = sql.replace("ON DUPLICATE KEY UPDATE", "ON CONFLICT(id) DO UPDATE SET")
        sql = re.sub(r"VALUES\((\w+)\)", r"excluded.\1", sql, flags=re.IGNORECASE)
        return self.cursor.execute(sql, params).rowcount

    def fetchone(self):
        if self.synthetic is not None:
            return self.synthetic.pop(0) if self.synthetic else None
        row = self.cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        if self.synthetic is not None:
            return list(self.synthetic)
        return [dict(row) for row in self.cursor.fetchall()]


class _Connection:
    def __init__(self, database):
        self.database = database
        self.raw = database.raw_connection()

    def cursor(self):
        return _Cursor(self)

    def begin(self):
        self.raw.execute("BEGIN")

    def commit(self):
        self.raw.commit()
        self.database.commits += 1

    def rollback(self):
        self.raw.rollback()
        self.database.rollbacks += 1

    def close(self):
        self.raw.close()
        self.database.closes += 1


class _Database:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.lock_allowed = True
        self.fail_on = None
        self.commits = self.rollbacks = self.closes = self.releases = 0
        connection = self.raw_connection()
        connection.executescript("""
            CREATE TABLE directory_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,
                display_name TEXT, email TEXT, department TEXT, is_active INTEGER,
                can_view_fi INTEGER DEFAULT 0, can_view_visitor_analytics INTEGER DEFAULT 0,
                entra_tenant_id TEXT, entra_oid TEXT, identity_source TEXT,
                synced_at TEXT, last_signin_at TEXT,
                UNIQUE(entra_tenant_id, entra_oid)
            );
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE,
                password_hash TEXT, display_name TEXT, role TEXT, allowed_models TEXT,
                ad_user_id INTEGER REFERENCES directory_users(id), is_active INTEGER,
                last_login TEXT, entra_oid TEXT UNIQUE, must_change_password INTEGER,
                requires_group_assignment INTEGER DEFAULT 0
            );
            CREATE TABLE access_groups (id INTEGER PRIMARY KEY, name TEXT, brand_filter TEXT);
            CREATE TABLE user_groups (
                ad_user_id INTEGER REFERENCES directory_users(id),
                group_id INTEGER REFERENCES access_groups(id),
                PRIMARY KEY(ad_user_id, group_id)
            );
            CREATE TABLE directory_sync_state (
                id INTEGER PRIMARY KEY, attempted_at TEXT, succeeded_at TEXT,
                user_count INTEGER DEFAULT 0, error_message TEXT
            );
        """)
        connection.close()

    def raw_connection(self):
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=4)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("NOW", 0, lambda: NOW)
        connection.create_function(
            "SUBSTRING_INDEX", 3,
            lambda value, separator, count: None if value is None else (
                separator.join(value.split(separator)[:count]) if count > 0
                else separator.join(value.split(separator)[count:])
            ),
        )
        return connection

    def connection(self):
        return _Connection(self)

    def seed(self, table, **values):
        connection = self.raw_connection()
        columns = ",".join(values)
        marks = ",".join("?" for _ in values)
        try:
            return connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values())
            ).lastrowid
        finally:
            connection.close()

    def person(self, **changes):
        return self.seed("directory_users", **{
            "id": 41, "username": "alex", "display_name": "Alex Imported",
            "email": "alex@skin1004korea.com", "department": "Data Team",
            "is_active": 1, "can_view_fi": 1, "can_view_visitor_analytics": 1,
            "identity_source": "legacy", **changes,
        })

    def account(self, **changes):
        return self.seed("users", **{
            "id": 7, "email": "alex@skin1004korea.com", "password_hash": "old-hash",
            "display_name": "Alex Account", "role": "user", "allowed_models": "old-model",
            "ad_user_id": 41, "is_active": 1, "must_change_password": 1,
            "requires_group_assignment": 0, **changes,
        })

    def group(self, person_id=41, brand_filter="SK,CBT"):
        group_id = self.seed("access_groups", id=1, brand_filter=brand_filter)
        self.seed("user_groups", ad_user_id=person_id, group_id=group_id)

    def rows(self, table):
        connection = self.raw_connection()
        try:
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
        finally:
            connection.close()

    def snapshot(self):
        return {table: self.rows(table) for table in (
            "directory_users", "users", "user_groups", "access_groups", "directory_sync_state",
        )}

    def execute(self, sql, params=()):
        connection = self.connection()
        try:
            with connection.cursor() as cursor:
                return cursor.execute(sql, params)
        finally:
            connection.close()

    def fetch_one(self, sql, params=()):
        connection = self.connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchone()
        finally:
            connection.close()


@pytest.fixture
def database(tmp_path, monkeypatch):
    database = _Database(str(tmp_path / "directory.sqlite"))
    monkeypatch.setattr(directory, "get_maria_conn", database.connection)
    monkeypatch.setattr(directory, "execute", database.execute)
    monkeypatch.setattr(directory, "fetch_one", database.fetch_one)
    return database


@pytest.mark.parametrize("changes", [
    {"tid": OTHER_OID}, {"tid": None}, {"tid": "not-a-tenant"},
    {"oid": None}, {"oid": "not-an-object-id"}, {"acct": 1}, {"acct": "1"},
    {"preferred_username": "alex_example.com#EXT#@cravercorp.com"},
    {"idp": "https://sts.windows.net/" + OTHER_OID + "/"},
    {"idp": "live.com"},
])
def test_invalid_or_external_identity_is_rejected_before_storage(monkeypatch, changes):
    monkeypatch.setattr(directory, "get_maria_conn", lambda: pytest.fail("Rejected identity reached DB"))
    with pytest.raises(directory.DirectoryError) as error:
        directory.provision_from_claims(_claims(**changes))
    assert error.value.status == 403


@pytest.mark.parametrize("idp", ["", f"https://sts.windows.net/{TENANT}/",
                                   f"https://login.microsoftonline.com/{TENANT}/v2.0"])
def test_home_tenant_identity_accepts_canonical_uuid_and_issuer(idp):
    assert directory.require_active_identity(_claims(tid=TENANT.upper(), oid=OID.upper(), idp=idp)) == (TENANT, OID)


def test_existing_account_keeps_ids_permissions_memberships_and_history_keys(database):
    database.person()
    database.account(role="admin")
    database.group()
    old_account = database.rows("users")[0]
    old_membership = database.rows("user_groups")

    result = directory.provision_from_claims(_claims(name="", department=""))

    assert result == {"id": 7, "role": "admin", "ad_user_id": 41, "created": False}
    person = database.rows("directory_users")[0]
    account = database.rows("users")[0]
    assert (person["id"], person["can_view_fi"], person["can_view_visitor_analytics"]) == (41, 1, 1)
    assert (person["display_name"], person["department"]) == ("Alex Imported", "Data Team")
    assert person["email"] == old_account["email"]
    assert (person["entra_oid"], person["entra_tenant_id"], person["last_signin_at"]) == (OID, TENANT, NOW)
    assert database.rows("user_groups") == old_membership
    for field in ("id", "ad_user_id", "role", "allowed_models", "email", "password_hash", "requires_group_assignment"):
        assert account[field] == old_account[field]
    assert account["entra_oid"] == OID and account["must_change_password"] == 0


@pytest.mark.parametrize("brand_filter", ["SK,CBT", None, ""])
def test_imported_employee_without_signup_inherits_grants_and_real_group(database, brand_filter):
    database.person()
    database.group(brand_filter=brand_filter)
    membership = database.rows("user_groups")

    result = directory.provision_from_claims(_claims())

    assert result["created"] is True and result["ad_user_id"] == 41
    assert len(database.rows("directory_users")) == 1
    person = database.rows("directory_users")[0]
    assert person["can_view_fi"] == person["can_view_visitor_analytics"] == 1
    account = database.rows("users")[0]
    assert account["id"] == result["id"] and account["role"] == "user"
    # The persistent flag remains set even when an inherited group currently
    # grants access, so removing that group cannot open unrestricted access.
    assert account["requires_group_assignment"] == 1
    assert database.rows("user_groups") == membership


def test_unknown_employee_starts_without_sensitive_permissions_or_brand_assignment(database):
    # 기본 그룹(SK_Brand·DD)이 아예 없는 저장소 — 자동 배정은 조용히 물러서고 관리자 배정으로 남는다
    result = directory.provision_from_claims(_claims(preferred_username="new@cravercorp.com"))
    person = database.rows("directory_users")[0]
    account = database.rows("users")[0]
    assert result["created"] is True
    assert person["can_view_fi"] == person["can_view_visitor_analytics"] == 0
    assert account["role"] == "user" and account["requires_group_assignment"] == 1
    assert database.rows("user_groups") == []
    assert account["password_hash"].startswith("$2")
    assert person["identity_source"] == "entra"


@pytest.mark.parametrize("email", ["alex@gmail.com", "alex@other.example", "broken", ""])
def test_unknown_or_personal_domain_cannot_claim_an_imported_local_part(database, email):
    database.person()
    before = database.snapshot()
    with pytest.raises(directory.DirectoryError):
        directory.provision_from_claims(_claims(preferred_username=email))
    assert database.snapshot() == before


def test_bootstrap_domains_remain_after_every_imported_person_is_linked(database, settings):
    settings.entra_employee_domains = ""
    database.person(entra_tenant_id=TENANT, entra_oid=OTHER_OID, identity_source="entra")
    database.person(id=42, username="personal", email="personal@gmail.com", identity_source="entra")

    result = directory.provision_from_claims(_claims(preferred_username="new@skin1004korea.com"))
    assert result["created"] is True
    for email in ("new@gmail.com", "new@untrusted.example"):
        with pytest.raises(directory.DirectoryError):
            directory.provision_from_claims(_claims(oid=NEW_OID, preferred_username=email))


def test_bound_oid_survives_changed_or_missing_email_claim(database):
    database.person(entra_tenant_id=TENANT, entra_oid=OID, identity_source="entra")
    database.account(entra_oid=OID)
    first = directory.provision_from_claims(_claims(preferred_username="renamed@cravercorp.com"))
    second = directory.provision_from_claims(_claims(preferred_username=""))
    assert first["id"] == second["id"] == 7
    assert len(database.rows("directory_users")) == len(database.rows("users")) == 1
    assert database.rows("directory_users")[0]["email"] == "alex@skin1004korea.com"


@pytest.mark.parametrize("conflict", ["duplicate_local_part", "bound_person", "bound_account", "duplicate_accounts",
                                    "inactive_person", "inactive_account"])
def test_conflicting_or_inactive_binding_does_not_change_any_record(database, conflict):
    person_changes = {"is_active": 0} if conflict == "inactive_person" else {}
    if conflict == "bound_person":
        person_changes.update(entra_tenant_id=TENANT, entra_oid=OTHER_OID)
    database.person(**person_changes)
    database.account(**({"is_active": 0} if conflict == "inactive_account" else
                        {"entra_oid": OTHER_OID} if conflict == "bound_account" else {}))
    if conflict == "duplicate_local_part":
        database.person(id=42, username="alex-duplicate", email="alex@cravercorp.com")
    if conflict == "duplicate_accounts":
        database.person(id=42, username="other", email="other@cravercorp.com")
        database.account(id=8, ad_user_id=42, email="other@cravercorp.com", entra_oid=OID)
    before = database.snapshot()

    with pytest.raises(directory.DirectoryError):
        directory.provision_from_claims(_claims())

    assert database.snapshot() == before
    assert database.rollbacks == 1 and database.releases == 1 and not database.lock.locked()


def test_repeated_enrollment_creates_one_account(database):
    database.person()
    first = directory.provision_from_claims(_claims())
    second = directory.provision_from_claims(_claims())
    assert first["created"] is True and second["created"] is False
    assert first["id"] == second["id"] and first["ad_user_id"] == second["ad_user_id"] == 41
    assert len(database.rows("users")) == len(database.rows("directory_users")) == 1


def test_concurrent_first_logins_share_one_person_and_one_account(database):
    barrier = threading.Barrier(2)

    def enroll():
        barrier.wait(timeout=3)
        return directory.provision_from_claims(_claims())

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: enroll(), range(2)))

    assert sorted(result["created"] for result in results) == [False, True]
    assert len({result["id"] for result in results}) == 1
    assert len(database.rows("directory_users")) == len(database.rows("users")) == 1
    assert database.releases == 2 and not database.lock.locked()


def test_failed_account_insert_rolls_back_new_person_and_releases_lock(database):
    database.fail_on = lambda sql, params: sql.startswith("INSERT INTO users ")
    with pytest.raises(directory.DirectoryError) as error:
        directory.provision_from_claims(_claims())
    assert error.value.status == 503
    assert database.rows("directory_users") == database.rows("users") == []
    assert database.rollbacks == 1 and database.releases == 1 and database.closes == 1


def test_busy_identity_lock_returns_retryable_failure_without_changes(database):
    database.lock_allowed = False
    with pytest.raises(directory.DirectoryError) as error:
        directory.provision_from_claims(_claims())
    assert error.value.status == 503
    assert database.rows("directory_users") == database.rows("users") == []
    assert database.releases == 0 and database.closes == 1


def test_connection_acquisition_failure_returns_safe_retryable_error(monkeypatch):
    def unavailable():
        raise sqlite3.OperationalError("host credential detail that must not escape")

    monkeypatch.setattr(directory, "get_maria_conn", unavailable)
    with pytest.raises(directory.DirectoryError) as error:
        directory.provision_from_claims(_claims())
    assert error.value.status == 503 and "credential detail" not in str(error.value)


class _Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        return deepcopy(self.payload)


def _mock_graph(monkeypatch, pages, *, token_response=None):
    posts, gets = [], []

    def post(url, **kwargs):
        posts.append((url, kwargs))
        return token_response or _Response(payload={"access_token": "opaque-test-token"})

    def get(url, **kwargs):
        gets.append((url, kwargs))
        return pages.pop(0)

    monkeypatch.setattr(directory.requests, "post", post)
    monkeypatch.setattr(directory.requests, "get", get)
    return posts, gets


def test_graph_pagination_collects_entire_snapshot_and_uses_application_token(monkeypatch):
    next_url = "https://graph.microsoft.com/v1.0/users?$skiptoken=page-two"
    first, second = _graph_user(), _graph_user(OTHER_OID, userPrincipalName="other@cravercorp.com")
    posts, gets = _mock_graph(monkeypatch, [
        _Response(payload={"value": [first], "@odata.nextLink": next_url}),
        _Response(payload={"value": [second]}),
    ])

    assert directory._graph_users() == [first, second]
    assert posts[0][1]["data"]["grant_type"] == "client_credentials"
    assert posts[0][1]["data"]["scope"] == "https://graph.microsoft.com/.default"
    assert gets[0][1]["params"]["$top"] == 999 and gets[1][1]["params"] is None
    assert gets[1][0] == next_url
    assert all(call[1]["headers"]["Authorization"] == "Bearer opaque-test-token" for call in gets)


@pytest.mark.parametrize("next_url", [
    "http://graph.microsoft.com/v1.0/users?page=2",
    "https://graph.microsoft.com.evil.example/users",
    "https://graph.microsoft.com@evil.example/users",
    "https://graph.microsoft.com/v1.0/users",
])
def test_graph_next_page_cannot_leak_token_or_loop(monkeypatch, next_url):
    _, gets = _mock_graph(monkeypatch, [
        _Response(payload={"value": [_graph_user()], "@odata.nextLink": next_url}),
    ])
    with pytest.raises(directory.DirectoryError) as error:
        directory._graph_users()
    assert error.value.status == 503 and len(gets) == 1


def test_graph_403_explains_missing_application_permission_and_preserves_state(database, monkeypatch):
    database.person()
    before = database.rows("directory_users")
    _mock_graph(monkeypatch, [_Response(403, {"error": {"message": "secret detail"}})])

    result = directory.sync_directory()

    assert result["ok"] is False and "User.Read.All" in result["error"]
    assert "secret detail" not in result["error"]
    assert database.rows("directory_users") == before
    assert database.rows("directory_sync_state")[0]["succeeded_at"] is None


def test_later_graph_page_failure_never_applies_the_partial_roster(database, monkeypatch):
    database.person(entra_tenant_id=TENANT, entra_oid=OID)
    database.person(id=42, username="other", email="other@cravercorp.com",
                    entra_tenant_id=TENANT, entra_oid=OTHER_OID)
    before = database.rows("directory_users")
    _mock_graph(monkeypatch, [
        _Response(payload={"value": [_graph_user()], "@odata.nextLink":
                           "https://graph.microsoft.com/v1.0/users?$skiptoken=next"}),
        _Response(503, {"error": "unavailable"}),
    ])

    result = directory.sync_directory()

    assert result["ok"] is False and database.rows("directory_users") == before
    assert database.commits == 0


@pytest.mark.parametrize("payload", [{}, {"value": {}}, {"value": []}, [], {"value": [None]}])
def test_malformed_or_empty_graph_response_cannot_mutate_roster(database, monkeypatch, payload):
    database.person(entra_tenant_id=TENANT, entra_oid=OID)
    before = database.rows("directory_users")
    _mock_graph(monkeypatch, [_Response(payload=payload)])

    result = directory.sync_directory()

    assert result["ok"] is False
    assert database.rows("directory_users") == before
    assert database.rows("directory_sync_state")[0]["succeeded_at"] is None


@pytest.mark.parametrize("response", [_Response(400, {"error": "invalid_client"}), _Response(payload={})])
def test_graph_token_failure_does_not_request_users(monkeypatch, response):
    _, gets = _mock_graph(monkeypatch, [], token_response=response)
    with pytest.raises(directory.DirectoryError) as error:
        directory._graph_users()
    assert error.value.status == 503 and gets == []


@pytest.mark.parametrize("payload", [None, [], "unexpected", 123])
def test_malformed_token_document_returns_directory_error(monkeypatch, payload):
    _, gets = _mock_graph(monkeypatch, [], token_response=_Response(payload=payload))
    with pytest.raises(directory.DirectoryError) as error:
        directory._graph_users()
    assert error.value.status == 503 and gets == []


@pytest.mark.parametrize("payload", [None, [], "unexpected", 123])
def test_malformed_page_document_returns_directory_error(monkeypatch, payload):
    _mock_graph(monkeypatch, [_Response(payload=payload)])
    with pytest.raises(directory.DirectoryError) as error:
        directory._graph_users()
    assert error.value.status == 503


def test_graph_network_error_preserves_last_success(database, monkeypatch):
    database.person()
    database.seed("directory_sync_state", id=1, succeeded_at="2026-09-07 12:00:00", user_count=10)

    def unreachable(*args, **kwargs):
        raise requests.ConnectionError("test network outage")

    monkeypatch.setattr(directory.requests, "post", unreachable)
    result = directory.sync_directory()
    state = database.rows("directory_sync_state")[0]
    assert result["ok"] is False and "graph.microsoft.com:443" in result["error"]
    assert state["succeeded_at"] == "2026-09-07 12:00:00" and state["user_count"] == 10


@pytest.mark.parametrize("snapshot", [
    [], None, {}, [None], [123], ["not-a-user"],
    [_graph_user(), _graph_user()],
    [_graph_user(accountEnabled=None)], [_graph_user(accountEnabled=1)],
    [_graph_user(userType="Unknown")], [_graph_user(id="bad-id")],
    [_graph_user(), {"id": OTHER_OID, "userType": "Member"}],
])
def test_incomplete_graph_snapshot_rejects_before_any_transaction(database, snapshot):
    database.person()
    before = database.snapshot()
    with pytest.raises(directory.DirectoryError):
        directory._apply_graph_users(snapshot)
    assert database.snapshot() == before
    assert database.commits == database.rollbacks == database.closes == 0


def test_graph_sync_preserves_permissions_memberships_and_does_not_create_login_accounts(database):
    database.person()
    database.account()
    database.group()
    accounts = database.rows("users")
    memberships = database.rows("user_groups")
    count = directory._apply_graph_users([
        _graph_user(),
        _graph_user(NEW_OID, userPrincipalName="new@cravercorp.com", mail="new@cravercorp.com"),
    ])

    people = database.rows("directory_users")
    assert count == 2 and len(people) == 2
    assert people[0]["id"] == 41
    assert people[0]["can_view_fi"] == people[0]["can_view_visitor_analytics"] == 1
    assert people[0]["display_name"] == "Alex Entra" and people[0]["department"] == "Marketing"
    assert people[1]["can_view_fi"] == people[1]["can_view_visitor_analytics"] == 0
    assert database.rows("users") == accounts and database.rows("user_groups") == memberships
    assert database.rows("directory_sync_state")[0]["user_count"] == 2


def test_graph_verified_member_can_introduce_a_new_company_domain(database):
    claims = _claims(preferred_username="new@newcompany.example")
    with pytest.raises(directory.DirectoryError):
        directory.provision_from_claims(claims)

    assert directory._apply_graph_users([
        _graph_user(userPrincipalName="new@newcompany.example", mail="new@newcompany.example"),
    ]) == 1
    result = directory.provision_from_claims(claims)
    assert result["created"] is True
    assert len(database.rows("directory_users")) == len(database.rows("users")) == 1
    assert database.rows("users")[0]["requires_group_assignment"] == 1


def test_repeated_graph_sync_does_not_restore_explicitly_revoked_permissions(database):
    database.person()
    directory._apply_graph_users([_graph_user()])
    database.execute("UPDATE directory_users SET can_view_fi=0,can_view_visitor_analytics=0 WHERE id=%s", (41,))
    directory._apply_graph_users([_graph_user()])
    person = database.rows("directory_users")[0]
    assert person["can_view_fi"] == person["can_view_visitor_analytics"] == 0


def test_graph_disabled_guest_and_absent_bound_users_cannot_sign_in(database):
    database.person(entra_tenant_id=TENANT, entra_oid=OID)
    database.person(id=42, username="absent", email="absent@cravercorp.com",
                    entra_tenant_id=TENANT, entra_oid=OTHER_OID)
    database.person(id=43, username="guest", email="guest@cravercorp.com",
                    entra_tenant_id=TENANT, entra_oid=NEW_OID)
    database.person(id=44, username="legacy", email="legacy@cravercorp.com")
    assert directory._apply_graph_users([
        _graph_user(accountEnabled=False),
        _graph_user(NEW_OID, userType="Guest", userPrincipalName="guest#EXT#@cravercorp.com"),
        _graph_user("55555555-5555-4555-8555-555555555555", userType="Guest"),
    ]) == 1
    people = {person["id"]: person for person in database.rows("directory_users")}
    assert len(people) == 4 and people[44]["is_active"] == 1
    assert all(people[person_id]["is_active"] == 0 for person_id in (41, 42, 43))
    for oid in (OID, OTHER_OID, NEW_OID):
        with pytest.raises(directory.DirectoryError):
            directory.provision_from_claims(_claims(oid=oid))
    assert database.rows("users") == []


def test_later_graph_identity_conflict_rolls_back_earlier_profile_update(database):
    database.person()
    database.person(id=42, username="other", email="other@cravercorp.com",
                    entra_tenant_id=TENANT, entra_oid=OTHER_OID)
    before = database.snapshot()

    with pytest.raises(directory.DirectoryError):
        directory._apply_graph_users([
            _graph_user(), _graph_user(NEW_OID, userPrincipalName="other@cravercorp.com"),
        ])

    assert database.snapshot() == before
    assert database.rollbacks == 1 and database.releases == 1 and not database.lock.locked()


def test_graph_storage_failure_rolls_back_all_profiles_and_success_marker(database):
    database.person()
    database.group()
    before = database.snapshot()
    database.fail_on = lambda sql, params: sql.startswith("INSERT INTO directory_sync_state ")
    with pytest.raises(sqlite3.OperationalError):
        directory._apply_graph_users([_graph_user()])
    assert database.snapshot() == before
    assert database.rollbacks == 1 and database.releases == 1 and not database.lock.locked()


# ── 기본 그룹 자동 배정 (2026-09-16: "유통본부는 DD, 나머지는 SK, 회원가입하면 자동으로") ──
from app.core.group_autoassign import default_group_name  # noqa: E402

DIST = "Craver_Accounts > Users > 유통부문 > 유통1본부 > 리테일팀 > 리테일1파트"
IT = "Craver_Accounts > Users > 경영부문 > 재무·IT본부 > IT팀 > I_개발파트"


@pytest.mark.parametrize("department, expected", [
    (DIST, "DD"),
    ("Craver_Accounts > Users > 유통부문 > 유통SCM본부 > 물류운영팀", "DD"),
    ("Craver_Accounts > Users > 유통부문 > 유통2본부", "DD"),
    ("Craver_Accounts > Users > Distribution Division > 유통1 본부 > 리테일 > 리테일1", "DD"),
    (IT, "SK_Brand"),
    ("Craver_Accounts > Users > 브랜드부문 > 글로벌마케팅본부 > 일본사업팀", "SK_Brand"),
    # ⛔ '유통' 글자만 보면 브랜드부문 사람이 DD 가 된다 — 마디로 판정한다
    ("Craver_Accounts > Users > 브랜드부문 > 글로벌마케팅본부 > 중국사업팀 > 신규 브랜드 유통파트", "SK_Brand"),
    ("", "SK_Brand"),
    (None, "SK_Brand"),
])
def test_default_group_follows_the_division_segment(department, expected):
    assert default_group_name(department) == expected


def _seed_brand_groups(database):
    database.seed("access_groups", id=2, name="SK_Brand", brand_filter="SK,CL,CBT")
    database.seed("access_groups", id=3, name="DD", brand_filter="UM")


@pytest.mark.parametrize("department, group_id", [(DIST, 3), (IT, 2), ("", 2)])
def test_new_employee_is_assigned_a_default_group_by_department(database, department, group_id):
    _seed_brand_groups(database)
    result = directory.provision_from_claims(
        _claims(preferred_username="new@cravercorp.com", department=department))
    assert result["created"] is True
    account = database.rows("users")[0]
    # 플래그는 그대로 1 — 그룹이 나중에 회수되면 다시 막혀야 한다
    assert account["requires_group_assignment"] == 1
    assert database.rows("user_groups") == [{"ad_user_id": result["ad_user_id"], "group_id": group_id}]


def test_pre_assigned_group_is_never_overridden_by_department(database):
    _seed_brand_groups(database)
    database.person(department=DIST)          # 유통 소속이지만 관리자가 SK 를 미리 줬다
    database.seed("user_groups", ad_user_id=41, group_id=2)
    directory.provision_from_claims(_claims())
    assert database.rows("user_groups") == [{"ad_user_id": 41, "group_id": 2}]


def test_existing_flagged_account_without_group_is_assigned_on_next_login(database):
    # 정재명의 상태: 가입은 됐는데(플래그 1) 그룹이 비어 있다 → 다음 로그인에 붙는다
    _seed_brand_groups(database)
    database.person(department=IT, entra_tenant_id=TENANT, entra_oid=OID)
    database.account(requires_group_assignment=1, entra_oid=OID)
    result = directory.provision_from_claims(_claims())
    assert result["created"] is False
    assert database.rows("user_groups") == [{"ad_user_id": 41, "group_id": 2}]


def test_legacy_account_without_group_keeps_unrestricted_access(database):
    # ⛔ 플래그 0 인 옛 계정은 그룹이 없어도 전체가 열려 있다 — 붙이면 접근이 조용히 좁아진다
    _seed_brand_groups(database)
    database.person(department=DIST, entra_tenant_id=TENANT, entra_oid=OID)
    database.account(requires_group_assignment=0, entra_oid=OID)
    directory.provision_from_claims(_claims())
    assert database.rows("user_groups") == []


def test_missing_default_group_falls_back_to_manual_assignment(database):
    # 그룹 이름이 바뀌었으면 조용히 실패하지 않고 관리자 배정으로 남는다 (WARNING 은 로그로)
    database.seed("access_groups", id=9, name="Renamed", brand_filter="SK")
    result = directory.provision_from_claims(_claims(preferred_username="new@cravercorp.com", department=IT))
    assert result["created"] is True
    assert database.rows("user_groups") == []
    assert database.rows("users")[0]["requires_group_assignment"] == 1
