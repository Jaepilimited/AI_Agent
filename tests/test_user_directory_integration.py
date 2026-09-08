"""Opt-in provisioning and Graph application tests on isolated MariaDB.

ENTRA_MARIADB_INTEGRATION=1 enables only the dedicated test instance at
127.0.0.1:13317. No production/development database or Entra service is used.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pymysql
import pytest

from app.core import user_directory as directory
from app.core import user_directory_migration as migration


pytestmark = pytest.mark.skipif(
    os.environ.get("ENTRA_MARIADB_INTEGRATION") != "1",
    reason="requires the explicitly enabled isolated MariaDB integration server",
)
_DATABASE = "cella_entra_provision_test"
_TENANT = "11111111-1111-4111-8111-111111111111"
_OLD_OID = "22222222-2222-4222-8222-222222222222"
_PENDING_OID = "33333333-3333-4333-8333-333333333333"
_NEW_OID = "44444444-4444-4444-8444-444444444444"
_CONFLICT_OID = "55555555-5555-4555-8555-555555555555"
_TABLES = ("directory_users", "users", "user_groups", "directory_sync_state")


def _claims(oid=_OLD_OID, email="registered@cravercorp.com", **changes):
    return {"tid": _TENANT, "oid": oid, "acct": 0, "preferred_username": email,
            "name": "Verified employee", **changes}


def _graph_user(oid=_OLD_OID, email="registered@cravercorp.com", **changes):
    return {"id": oid, "userPrincipalName": email, "mail": email,
            "displayName": "Name from Graph", "department": "Graph department",
            "userType": "Member", "accountEnabled": True, **changes}


class _RealDatabase:
    def __init__(self, config):
        self.config = config

    def connect(self):
        return pymysql.connect(**self.config)

    def run(self, statement, params=()):
        connection = self.connect()
        try:
            with connection.cursor() as cur:
                affected = cur.execute(statement, params)
                rows = list(cur.fetchall())
            connection.commit()
            return rows, affected
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def sql(self, statement, params=()):
        return self.run(statement, params)[0]

    def execute(self, statement, params=()):
        return self.run(statement, params)[1]

    def fetch_one(self, statement, params=()):
        return next(iter(self.sql(statement, params)), None)

    def rows(self, table):
        assert table in (*_TABLES, "ad_users", "directory_migrations")
        return self.sql(f"SELECT * FROM `{table}` ORDER BY id")

    def snapshot(self):
        return {table: self.rows(table) for table in _TABLES}


@pytest.fixture
def real_database(monkeypatch):
    config_path = Path(__file__).resolve().parents[1] / "qa_artifacts/entra_mariadb_test/connection.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config.get("host") == "127.0.0.1" and int(config.get("port", 0)) == 13317
    config.update(cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    connection = pymysql.connect(**config)
    try:
        with connection.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{_DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    finally:
        connection.close()
    config["database"] = _DATABASE
    db = _RealDatabase(config)
    # Reset only these owned tables in the fixed scratch schema.
    for table in ("user_groups", "users", "groups", "directory_users", "ad_users",
                  "directory_migrations", "directory_sync_state"):
        db.sql(f"DROP TABLE IF EXISTS `{table}`")
    db.sql("""CREATE TABLE ad_users (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, username VARCHAR(100) NOT NULL UNIQUE,
        display_name VARCHAR(200), email VARCHAR(255), department VARCHAR(200), full_dn TEXT,
        is_active TINYINT(1) DEFAULT 1, synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        deal_id_prefix VARCHAR(4), can_view_fi TINYINT(1) NOT NULL DEFAULT 0,
        can_view_visitor_analytics TINYINT(1) NOT NULL DEFAULT 0
    ) ENGINE=InnoDB""")
    db.sql("""CREATE TABLE users (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        email VARCHAR(255) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL,
        display_name VARCHAR(200), role ENUM('user','admin') DEFAULT 'user',
        allowed_models VARCHAR(500) DEFAULT 'skin1004-Search',
        ad_user_id INT NULL, is_active TINYINT(1) DEFAULT 1,
        last_login DATETIME NULL, entra_oid VARCHAR(64) NULL UNIQUE,
        must_change_password TINYINT(1) NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        announce_seen_at DATETIME NULL, briefing_opt_out TINYINT(4) NOT NULL DEFAULT 0,
        CONSTRAINT users_ibfk_1 FOREIGN KEY (ad_user_id) REFERENCES ad_users(id) ON DELETE SET NULL
    ) ENGINE=InnoDB""")
    db.sql("""CREATE TABLE `groups` (
        id INT NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL,
        brand_filter VARCHAR(100) NULL
    ) ENGINE=InnoDB""")
    db.sql("""CREATE TABLE user_groups (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, ad_user_id INT NOT NULL, group_id INT NOT NULL,
        assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_person_group (ad_user_id, group_id),
        CONSTRAINT user_groups_ibfk_1 FOREIGN KEY (ad_user_id) REFERENCES ad_users(id) ON DELETE CASCADE,
        CONSTRAINT user_groups_ibfk_2 FOREIGN KEY (group_id) REFERENCES `groups`(id) ON DELETE CASCADE
    ) ENGINE=InnoDB""")
    for person_id, username, domain, prefix in (
        (41, "registered", "cravercorp.com", "SK"),
        (42, "pending", "skin1004korea.com", "DD"),
    ):
        db.sql("""INSERT INTO ad_users
            (id, username, display_name, email, department, full_dn, is_active,
             synced_at, created_at, updated_at, deal_id_prefix, can_view_fi,
             can_view_visitor_analytics)
            VALUES (%s, %s, 'Imported employee', %s, 'Original department', 'CN=Preserved', 1,
              '2026-09-07 12:00:00', '2026-01-01 00:00:00', '2026-09-07 12:00:00', %s, 1, 1)""",
            (person_id, username, f"{username}@{domain}", prefix))
    db.sql("""INSERT INTO users
        (id, email, password_hash, display_name, role, allowed_models, ad_user_id,
         is_active, last_login, entra_oid, must_change_password)
        VALUES (85, 'registered@cravercorp.com', 'preserve-existing-password-hash',
          'Original login name', 'admin', 'existing-model-choice', 41, 1,
          '2026-09-07 12:00:00', %s, 1)""", (_OLD_OID,))
    db.sql("INSERT INTO `groups` (id, name, brand_filter) VALUES (11, 'SK', 'SK,CBT'), (12, 'DD', 'UM')")
    db.sql("INSERT INTO user_groups (id, ad_user_id, group_id) VALUES (701, 41, 11), (702, 42, 12)")
    settings = SimpleNamespace(entra_tenant_id=_TENANT,
                               entra_employee_domains="cravercorp.com,skin1004korea.com")
    monkeypatch.setattr(migration, "get_maria_conn", db.connect)
    monkeypatch.setattr(migration, "get_settings", lambda: settings)
    monkeypatch.setattr(directory, "get_maria_conn", db.connect)
    monkeypatch.setattr(directory, "get_settings", lambda: settings)
    monkeypatch.setattr(directory, "execute", db.execute)
    monkeypatch.setattr(directory, "fetch_one", db.fetch_one)
    migration.ensure_directory_tables()
    directory.ensure_directory_sync_table()
    return db


def test_real_existing_login_keeps_id_permissions_groups_and_account_settings(real_database):
    db = real_database
    person, account, groups = db.rows("directory_users")[0], db.rows("users")[0], db.rows("user_groups")
    result = directory.provision_from_claims(_claims(name="", department=""))
    assert result == {"id": 85, "role": "admin", "ad_user_id": 41, "created": False}
    updated = db.rows("users")[0]
    for field in ("id", "email", "password_hash", "display_name", "role", "allowed_models",
                  "ad_user_id", "requires_group_assignment", "created_at"):
        assert updated[field] == account[field]
    assert updated["must_change_password"] == 0 and updated["last_login"] > account["last_login"]
    preserved = db.rows("directory_users")[0]
    for field in ("id", "can_view_fi", "can_view_visitor_analytics", "deal_id_prefix", "department", "display_name"):
        assert preserved[field] == person[field]
    assert preserved["last_signin_at"] is not None
    assert db.rows("user_groups") == groups


def test_real_prelogin_employee_enrolls_once_with_existing_sensitive_rights_and_group(real_database):
    db = real_database
    before = db.rows("directory_users")[1]
    memberships = db.rows("user_groups")
    first = directory.provision_from_claims(_claims(_PENDING_OID, "pending@cravercorp.com"))
    second = directory.provision_from_claims(_claims(_PENDING_OID, "pending-renamed@cravercorp.com"))
    assert first["created"] is True and second["created"] is False
    assert first["id"] == second["id"] == 86
    assert first["ad_user_id"] == second["ad_user_id"] == 42
    assert first["role"] == "user" and len(db.rows("users")) == 2
    person = db.rows("directory_users")[1]
    for field in ("id", "can_view_fi", "can_view_visitor_analytics", "deal_id_prefix"):
        assert person[field] == before[field]
    assert person["email"] == before["email"]
    account = db.rows("users")[1]
    assert account["requires_group_assignment"] == 1 and account["must_change_password"] == 0
    assert account["password_hash"].startswith("$2")
    assert db.rows("user_groups") == memberships


def test_real_new_employee_starts_as_restricted_ordinary_user(real_database):
    db = real_database
    memberships = db.rows("user_groups")
    result = directory.provision_from_claims(_claims(
        _NEW_OID, "new@cravercorp.com", roles=["admin"], groups=["SK", "DD"],
        can_view_fi=True, can_view_visitor_analytics=True,
    ))
    person = db.sql("SELECT * FROM directory_users WHERE id=%s", (result["ad_user_id"],))[0]
    account = db.sql("SELECT * FROM users WHERE id=%s", (result["id"],))[0]
    assert result["created"] is True and result["role"] == account["role"] == "user"
    assert person["can_view_fi"] == person["can_view_visitor_analytics"] == 0
    assert person["identity_source"] == "entra" and person["entra_oid"] == _NEW_OID
    assert account["requires_group_assignment"] == 1 and account["must_change_password"] == 0
    assert account["password_hash"].startswith("$2") and len(account["password_hash"]) == 60
    assert db.sql("SELECT * FROM user_groups WHERE ad_user_id=%s", (person["id"],)) == []
    assert db.rows("user_groups") == memberships


@pytest.mark.parametrize("conflict", ["local_part", "directory_oid", "login_oid"])
def test_real_identity_conflicts_abort_without_changes(real_database, conflict):
    db = real_database
    claims = _claims(_PENDING_OID, "pending@cravercorp.com")
    if conflict == "local_part":
        db.sql("INSERT INTO directory_users (username,email) VALUES ('duplicate-pending','pending@cravercorp.com')")
    elif conflict == "directory_oid":
        db.sql("UPDATE directory_users SET entra_tenant_id=%s,entra_oid=%s WHERE id=42", (_TENANT, _CONFLICT_OID))
    else:
        db.sql("UPDATE users SET entra_oid=%s WHERE id=85", (_CONFLICT_OID,))
        claims = _claims()
    before = db.snapshot()
    with pytest.raises(directory.DirectoryError):
        directory.provision_from_claims(claims)
    assert db.snapshot() == before


def test_real_unique_email_failure_rolls_back_new_person(real_database):
    db = real_database
    db.sql("UPDATE users SET email='future@cravercorp.com' WHERE id=85")
    before = db.snapshot()
    with pytest.raises(directory.DirectoryError) as error:
        directory.provision_from_claims(_claims(_NEW_OID, "future@cravercorp.com"))
    assert error.value.status == 503
    assert db.snapshot() == before
    # A subsequent call proves the failed transaction does not leave the identity lock held.
    assert directory.provision_from_claims(_claims())["id"] == 85


def test_real_concurrent_first_signins_create_one_person_and_account(real_database):
    barrier = threading.Barrier(2)

    def sign_in():
        barrier.wait(timeout=5)
        return directory.provision_from_claims(_claims(_NEW_OID, "concurrent@cravercorp.com"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: sign_in(), range(2)))
    assert sorted(row["created"] for row in outcomes) == [False, True]
    assert len({row["id"] for row in outcomes}) == 1
    assert len({row["ad_user_id"] for row in outcomes}) == 1
    assert len(real_database.rows("directory_users")) == 3 and len(real_database.rows("users")) == 2


def test_real_graph_sync_preserves_rights_groups_and_account_settings(real_database):
    db = real_database
    db.sql("UPDATE directory_users SET can_view_fi=0,can_view_visitor_analytics=0 WHERE id=41")
    accounts, memberships = db.rows("users"), db.rows("user_groups")
    snapshot = [_graph_user(), _graph_user(_PENDING_OID, "pending@cravercorp.com"),
                _graph_user(_NEW_OID, "graph-new@cravercorp.com")]
    assert directory._apply_graph_users(snapshot) == 3
    assert directory._apply_graph_users(snapshot) == 3
    people = {row["id"]: row for row in db.rows("directory_users")}
    assert (people[41]["can_view_fi"], people[41]["can_view_visitor_analytics"]) == (0, 0)
    assert (people[42]["can_view_fi"], people[42]["can_view_visitor_analytics"]) == (1, 1)
    assert (people[43]["can_view_fi"], people[43]["can_view_visitor_analytics"]) == (0, 0)
    assert all(row["department"] == "Graph department" for row in people.values())
    assert people[42]["last_signin_at"] is None and people[43]["last_signin_at"] is None
    assert db.rows("users") == accounts and db.rows("user_groups") == memberships
    state = db.rows("directory_sync_state")[0]
    assert state["user_count"] == 3 and state["succeeded_at"] is not None and state["error_message"] is None


def test_real_graph_later_identity_conflict_rolls_back_earlier_updates(real_database):
    db = real_database
    db.sql("UPDATE directory_users SET entra_tenant_id=%s,entra_oid=%s WHERE id=42", (_TENANT, _PENDING_OID))
    db.sql("INSERT INTO directory_sync_state (id,succeeded_at,user_count) VALUES (1,'2026-09-07',2)")
    before = db.snapshot()
    with pytest.raises(directory.DirectoryError):
        directory._apply_graph_users([
            _graph_user(displayName="Must roll back"),
            _graph_user(_NEW_OID, "also-roll-back@cravercorp.com"),
            _graph_user(_CONFLICT_OID, "pending@cravercorp.com"),
        ])
    assert db.snapshot() == before


def test_real_graph_storage_failure_rolls_back_users_and_success_marker(real_database):
    db = real_database
    db.sql("INSERT INTO directory_sync_state (id,succeeded_at,user_count) VALUES (1,'2026-09-07',2)")
    db.sql("""CREATE TRIGGER reject_directory_sync BEFORE INSERT ON directory_sync_state
        FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='isolated integration failure'""")
    before = deepcopy(db.snapshot())
    with pytest.raises(pymysql.err.MySQLError):
        directory._apply_graph_users([
            _graph_user(displayName="Must roll back"),
            _graph_user(_NEW_OID, "also-roll-back@cravercorp.com"),
        ])
    assert db.snapshot() == before
    db.sql("DROP TRIGGER reject_directory_sync")
    assert directory._apply_graph_users([_graph_user()]) == 1


@pytest.mark.parametrize("snapshot", [
    [], [_graph_user(), _graph_user()], [_graph_user(accountEnabled=None)],
])
def test_real_malformed_graph_snapshot_cannot_change_directory(real_database, snapshot):
    before = real_database.snapshot()
    with pytest.raises(directory.DirectoryError):
        directory._apply_graph_users(snapshot)
    assert real_database.snapshot() == before
