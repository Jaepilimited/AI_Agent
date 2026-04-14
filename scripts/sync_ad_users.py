"""Sync Active Directory users to MariaDB.

Queries AD via LDAPS for active users, then upserts into MariaDB ad_users table.
Credentials are loaded from .env (never hardcoded).

Usage:
  python scripts/sync_ad_users.py              # Full sync
  python scripts/sync_ad_users.py --dry-run    # Preview only (no DB writes)
"""
import os
import ssl
import sys
from datetime import datetime

# Setup path for app imports
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

from ldap3 import Server, Connection, Tls, ALL, SUBTREE
import pymysql


def get_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        print(f"ERROR: {key} not set in .env")
        sys.exit(1)
    return val


def fetch_ad_users() -> list[dict]:
    """Fetch active users from Active Directory via LDAPS."""
    ad_server = get_env("AD_SERVER")
    ad_user = get_env("AD_USER")
    ad_password = get_env("AD_PASSWORD")
    search_base = get_env("AD_SEARCH_BASE")

    tls_config = Tls(validate=ssl.CERT_NONE, version=ssl.PROTOCOL_TLSv1_2)
    server = Server(ad_server, port=636, use_ssl=True, tls=tls_config, get_info=ALL)

    print(f"Connecting to AD: {ad_server}:636 (LDAPS)")

    with Connection(server, user=ad_user, password=ad_password, auto_bind=True) as conn:
        print("AD authentication successful")

        # Active users only (exclude disabled accounts)
        search_filter = (
            "(&(objectClass=user)(objectCategory=person)"
            "(!(userAccountControl:1.2.840.113556.1.4.803:=2)))"
        )
        attributes = ["sAMAccountName", "name", "displayName", "mail", "department"]

        conn.search(
            search_base=search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=attributes,
        )

        users = []
        for entry in conn.entries:
            dn = entry.entry_dn
            # Extract OU path from DN (reversed for readability)
            ou_parts = [
                part.replace("OU=", "")
                for part in dn.split(",")
                if part.startswith("OU=")
            ]
            ou_path = " > ".join(reversed(ou_parts)) if ou_parts else "Root"

            # Prefer displayName (usually Korean) over name (CN, often English)
            display = str(entry.displayName) if hasattr(entry, 'displayName') and entry.displayName else str(entry.name)
            users.append({
                "username": str(entry.sAMAccountName),
                "display_name": display,
                "email": str(entry.mail) if entry.mail else None,
                "department": ou_path,
                "full_dn": dn,
            })

        print(f"Fetched {len(users)} active users from AD")
        return users


def get_db_connection():
    """Get MariaDB connection using .env credentials."""
    return pymysql.connect(
        host=get_env("MARIADB_HOST"),
        port=int(os.getenv("MARIADB_PORT", "3306")),
        user=get_env("MARIADB_USER"),
        password=get_env("MARIADB_PASSWORD"),
        database=get_env("MARIADB_DATABASE"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def sync_to_mariadb(users: list[dict], dry_run: bool = False):
    """Upsert AD users into MariaDB ad_users table."""
    if dry_run:
        print("\n[DRY RUN] Preview — no DB changes will be made")
        for u in users:
            print(f"  [{u['department']}] {u['display_name']} ({u['username']}) - {u['email'] or 'N/A'}")
        print(f"\nTotal: {len(users)} users")
        return

    conn = get_db_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with conn.cursor() as cursor:
            # Mark all existing users as inactive first (will re-activate matched ones)
            cursor.execute("UPDATE ad_users SET is_active = 0, synced_at = %s", (now,))

            inserted = 0
            updated = 0
            # Display name overrides: AD 이름과 다르게 사용하는 경우
            _NAME_OVERRIDES = {
                "js.bae@skin1004korea.com": "배진서",  # AD: 배서진 → 실제: 배진서
            }

            for u in users:
                # Apply name override if exists
                if u.get("email") in _NAME_OVERRIDES:
                    u["display_name"] = _NAME_OVERRIDES[u["email"]]

                # Upsert: INSERT ON DUPLICATE KEY UPDATE
                sql = """
                    INSERT INTO ad_users (username, display_name, email, department, full_dn, is_active, synced_at)
                    VALUES (%s, %s, %s, %s, %s, 1, %s)
                    ON DUPLICATE KEY UPDATE
                        display_name = VALUES(display_name),
                        email = VALUES(email),
                        department = VALUES(department),
                        full_dn = VALUES(full_dn),
                        is_active = 1,
                        synced_at = VALUES(synced_at)
                """
                cursor.execute(sql, (
                    u["username"],
                    u["display_name"],
                    u["email"],
                    u["department"],
                    u["full_dn"],
                    now,
                ))
                if cursor.lastrowid:
                    inserted += 1
                else:
                    updated += 1

            conn.commit()

            # Count results
            cursor.execute("SELECT COUNT(*) as cnt FROM ad_users WHERE is_active = 1")
            active = cursor.fetchone()["cnt"]
            cursor.execute("SELECT COUNT(*) as cnt FROM ad_users WHERE is_active = 0")
            inactive = cursor.fetchone()["cnt"]

            print(f"\nSync complete:")
            print(f"  Inserted: {inserted}")
            print(f"  Updated:  {updated}")
            print(f"  Active:   {active}")
            print(f"  Inactive: {inactive} (no longer in AD)")

    finally:
        conn.close()


def main():
    dry_run = "--dry-run" in sys.argv
    print("=" * 60)
    print("SKIN1004 AD → MariaDB User Sync")
    print("=" * 60)

    users = fetch_ad_users()
    sync_to_mariadb(users, dry_run=dry_run)

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
