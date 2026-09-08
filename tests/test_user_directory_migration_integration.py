"""Opt-in MariaDB preservation tests using the dedicated temporary test server.

Run with ENTRA_MARIADB_INTEGRATION=1 after the isolated 127.0.0.1:13317 server
has been prepared. Normal test runs never read its credentials or connect to DB.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pymysql
import pytest

from app.core import user_directory_migration as migration


pytestmark = pytest.mark.skipif(
    os.environ.get("ENTRA_MARIADB_INTEGRATION") != "1",
    reason="requires the explicitly enabled isolated MariaDB integration server",
)
_DATABASE = "cella_entra_migration_test"
_TENANT = "11111111-1111-4111-8111-111111111111"
_OID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def real_database(monkeypatch):
    config_path = Path(__file__).resolve().parents[1] / "qa_artifacts/entra_mariadb_test/connection.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    # Refuse production or development DBs even if the artifact is changed.
    assert config.get("host") == "127.0.0.1" and int(config.get("port", 0)) == 13317
    config.update(cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    connection = pymysql.connect(**config)
    try:
        with connection.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{_DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    finally:
        connection.close()
    config["database"] = _DATABASE

    def connect():
        return pymysql.connect(**config)

    def sql(statement, params=()):
        connection = connect()
        try:
            with connection.cursor() as cur:
                cur.execute(statement, params)
                result = cur.fetchall()
            connection.commit()
            return list(result)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # Only these tables in our fixed scratch schema are reset between tests.
    for table in ("user_groups", "directory_audit_links", "users", "groups", "directory_users", "ad_users", "directory_migrations"):
        sql(f"DROP TABLE IF EXISTS `{table}`")
    sql("""CREATE TABLE ad_users (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(100) NOT NULL UNIQUE,
        display_name VARCHAR(200), email VARCHAR(255), department VARCHAR(200), full_dn TEXT,
        is_active TINYINT(1) DEFAULT 1, synced_at DATETIME, created_at DATETIME,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        deal_id_prefix VARCHAR(4), can_view_fi TINYINT(1) NOT NULL DEFAULT 0,
        can_view_visitor_analytics TINYINT(1) NOT NULL DEFAULT 0,
        employment_code VARCHAR(32)
    ) ENGINE=InnoDB""")
    sql("""CREATE TABLE users (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, ad_user_id INT NULL,
        entra_oid VARCHAR(64) NULL UNIQUE, last_login DATETIME, role VARCHAR(32) DEFAULT 'user',
        CONSTRAINT users_ibfk_1 FOREIGN KEY (ad_user_id) REFERENCES ad_users(id) ON DELETE SET NULL
    ) ENGINE=InnoDB""")
    sql("CREATE TABLE `groups` (id INT NOT NULL PRIMARY KEY, name VARCHAR(32)) ENGINE=InnoDB")
    sql("""CREATE TABLE user_groups (
        id INT NOT NULL PRIMARY KEY, ad_user_id INT NOT NULL, group_id INT NOT NULL,
        assigned_at DATETIME,
        CONSTRAINT user_groups_ibfk_1 FOREIGN KEY (ad_user_id) REFERENCES ad_users(id) ON DELETE CASCADE,
        CONSTRAINT user_groups_ibfk_2 FOREIGN KEY (group_id) REFERENCES `groups`(id) ON DELETE CASCADE
    ) ENGINE=InnoDB""")
    sql("""CREATE TABLE directory_audit_links (
        id INT NOT NULL PRIMARY KEY, ad_user_id INT NOT NULL,
        CONSTRAINT audit_person_fk FOREIGN KEY (ad_user_id) REFERENCES ad_users(id)
            ON DELETE RESTRICT ON UPDATE CASCADE
    ) ENGINE=InnoDB""")
    for person_id in (1, 2):
        sql("""INSERT INTO ad_users
            (id, username, display_name, email, department, full_dn, is_active,
             synced_at, created_at, updated_at, deal_id_prefix, can_view_fi,
             can_view_visitor_analytics, employment_code)
            VALUES (%s, %s, 'Migration test', %s, 'Data', 'CN=Preserved', 1,
              '2026-09-07 12:00:00', '2026-01-01 00:00:00', '2026-09-07 12:00:00',
              %s, %s, 1, %s)""",
            (person_id, f"employee{person_id}", f"employee{person_id}@example.test",
             "SK" if person_id == 1 else "DD", int(person_id == 1), f"E{person_id}"))
    sql("INSERT INTO users (id, ad_user_id, entra_oid, last_login, role) VALUES (85, 1, %s, '2026-09-08 01:00:00', 'admin')", (_OID,))
    sql("INSERT INTO `groups` (id, name) VALUES (11, 'SK'), (12, 'DD')")
    # Person 2 has no login account but already has a brand assignment.
    sql("INSERT INTO user_groups (id, ad_user_id, group_id, assigned_at) VALUES (41, 1, 11, '2026-09-01'), (42, 2, 12, '2026-09-01')")
    sql("INSERT INTO directory_audit_links (id, ad_user_id) VALUES (51, 1)")
    monkeypatch.setattr(migration, "get_maria_conn", connect)
    monkeypatch.setattr(migration, "get_settings", lambda: SimpleNamespace(entra_tenant_id=_TENANT))
    return sql


def _directory_foreign_keys(sql):
    return sql("""SELECT TABLE_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE()
          AND REFERENCED_TABLE_NAME IN ('ad_users', 'directory_users')
        ORDER BY TABLE_NAME, CONSTRAINT_NAME""")


def test_real_migration_preserves_ids_grants_memberships_and_detaches_archive(real_database):
    sql = real_database
    source = sql("SELECT * FROM ad_users ORDER BY id")
    users = sql("SELECT * FROM users ORDER BY id")
    groups = sql("SELECT * FROM user_groups ORDER BY id")
    assert migration.migration_status()["completed"] is False
    status = migration.ensure_directory_tables()
    assert status["completed"] is True and status["migrated"] is True
    assert (status["directory_users"], status["entra_linked"]) == (2, 1)
    assert sql("SELECT * FROM ad_users ORDER BY id") == source
    target = sql("SELECT * FROM directory_users ORDER BY id")
    assert [{key: row[key] for key in source[0]} for row in target] == source
    assert sql("SELECT * FROM users ORDER BY id") == [dict(users[0], requires_group_assignment=0)]
    assert sql("SELECT * FROM user_groups ORDER BY id") == groups
    assert (target[0]["entra_oid"], target[0]["entra_tenant_id"]) == (_OID, _TENANT)
    assert target[1]["entra_oid"] is None and target[1]["identity_source"] == "legacy"
    assert target[0]["updated_at"] == source[0]["updated_at"]  # ON UPDATE must not erase the original timestamp.
    foreign_keys = _directory_foreign_keys(sql)
    assert len(foreign_keys) == 3
    assert all(row["REFERENCED_TABLE_NAME"] == "directory_users" for row in foreign_keys)
    rules = sql("""SELECT TABLE_NAME, DELETE_RULE, UPDATE_RULE
        FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME='directory_users'""")
    assert {row["TABLE_NAME"]: (row["DELETE_RULE"], row["UPDATE_RULE"]) for row in rules} == {
        "users": ("SET NULL", "RESTRICT"), "user_groups": ("CASCADE", "RESTRICT"),
        "directory_audit_links": ("RESTRICT", "CASCADE"),
    }
    with pytest.raises(pymysql.err.IntegrityError):
        sql("INSERT INTO directory_users (username, entra_oid, entra_tenant_id) VALUES ('duplicate', %s, %s)", (_OID, _TENANT))

    sql("UPDATE directory_users SET can_view_fi=0, can_view_visitor_analytics=0 WHERE id=1")
    sql("UPDATE users SET requires_group_assignment=1 WHERE id=85")
    # This deletes test archive records only. Active login/group relationships must survive.
    sql("DELETE FROM ad_users WHERE id IN (1, 2)")
    assert sql("SELECT * FROM user_groups ORDER BY id") == groups
    assert sql("SELECT id, ad_user_id FROM users") == [{"id": 85, "ad_user_id": 1}]
    assert migration.ensure_directory_tables()["migrated"] is False
    assert sql("SELECT can_view_fi, can_view_visitor_analytics FROM directory_users WHERE id=1") == [
        {"can_view_fi": 0, "can_view_visitor_analytics": 0}]
    assert sql("SELECT requires_group_assignment FROM users WHERE id=85")[0]["requires_group_assignment"] == 1
    assert sql("SELECT COUNT(*) AS n FROM directory_migrations")[0]["n"] == 1


def test_real_interrupted_ddl_retries_with_preserved_rows(real_database, monkeypatch):
    sql = real_database
    original_groups = sql("SELECT * FROM user_groups ORDER BY id")
    original_ddl = migration._ddl
    rebound = 0

    def interrupt_after_one_fk(conn, cur, statement):
        nonlocal rebound
        if "DROP FOREIGN KEY" in statement:
            rebound += 1
            if rebound == 2:
                raise RuntimeError("integration interruption after a committed FK replacement")
        return original_ddl(conn, cur, statement)

    monkeypatch.setattr(migration, "_ddl", interrupt_after_one_fk)
    with pytest.raises(RuntimeError, match="integration interruption"):
        migration.ensure_directory_tables()
    assert sql("SELECT COUNT(*) AS n FROM directory_migrations")[0]["n"] == 0
    assert {row["REFERENCED_TABLE_NAME"] for row in _directory_foreign_keys(sql)} == {"ad_users", "directory_users"}
    monkeypatch.setattr(migration, "_ddl", original_ddl)
    assert migration.ensure_directory_tables()["completed"] is True
    assert sql("SELECT * FROM user_groups ORDER BY id") == original_groups
    assert sql("SELECT id FROM directory_users ORDER BY id") == [{"id": 1}, {"id": 2}]
    assert sql("SELECT id, ad_user_id FROM users") == [{"id": 85, "ad_user_id": 1}]
    assert all(row["REFERENCED_TABLE_NAME"] == "directory_users" for row in _directory_foreign_keys(sql))


def test_real_partial_copy_conflict_cannot_restore_revoked_grants(real_database):
    sql = real_database
    source = sql("SELECT * FROM ad_users ORDER BY id")
    sql("CREATE TABLE directory_users LIKE ad_users")
    sql("INSERT INTO directory_users SELECT * FROM ad_users")
    sql("UPDATE directory_users SET can_view_fi=0, updated_at=updated_at WHERE id=1")
    with pytest.raises(migration.DirectoryMigrationError, match="copy conflict"):
        migration.ensure_directory_tables()
    assert sql("SELECT can_view_fi FROM directory_users WHERE id=1")[0]["can_view_fi"] == 0
    assert sql("SELECT * FROM ad_users ORDER BY id") == source
    assert sql("SELECT COUNT(*) AS n FROM directory_migrations")[0]["n"] == 0
    assert all(row["REFERENCED_TABLE_NAME"] == "ad_users" for row in _directory_foreign_keys(sql))


def test_real_concurrent_startup_serializes_to_one_completed_migration(real_database):
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: migration.ensure_directory_tables(), range(2)))
    assert sorted(result["migrated"] for result in outcomes) == [False, True]
    assert all(result["completed"] for result in outcomes)
    assert real_database("SELECT COUNT(*) AS n FROM directory_migrations")[0]["n"] == 1
    assert real_database("SELECT COUNT(*) AS n FROM directory_users")[0]["n"] == 2
