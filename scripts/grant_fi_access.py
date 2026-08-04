"""Grant or revoke FI_LLM_Flat access for the approved AD accounts.

Usage:
  python scripts/grant_fi_access.py --dry-run
  python scripts/grant_fi_access.py
  python scripts/grant_fi_access.py --revoke
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.db.mariadb import get_maria_conn  # noqa: E402


FI_USERNAMES = (
    "jeffrey",
    "smyang",
    "chris",
    "hejin",
    "hlnam",
    "chloe_woo",
    "kwak",
    "sckang",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant or revoke FI data access")
    parser.add_argument("--dry-run", action="store_true", help="show matches without updating")
    parser.add_argument("--revoke", action="store_true", help="set can_view_fi to 0")
    args = parser.parse_args()

    placeholders = ", ".join(["%s"] * len(FI_USERNAMES))
    conn = get_maria_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, display_name, can_view_fi FROM ad_users "
                f"WHERE username IN ({placeholders}) ORDER BY username",
                FI_USERNAMES,
            )
            rows = cur.fetchall()

            matched = {str(row["username"]).lower(): row for row in rows}
            missing = [username for username in FI_USERNAMES if username not in matched]
            if missing:
                conn.rollback()
                print("ERROR: 다음 AD username을 찾지 못해 작업을 중단합니다:")
                for username in missing:
                    print(f"  - {username}")
                return 1

            action = "회수" if args.revoke else "부여"
            print(f"FI 열람 권한 {action} 대상 ({len(FI_USERNAMES)}명):")
            for username in FI_USERNAMES:
                row = matched[username]
                print(
                    f"  - {username}: {row.get('display_name') or '-'} "
                    f"(현재={bool(row.get('can_view_fi'))})"
                )

            if args.dry_run:
                conn.rollback()
                print("DRY RUN: DB를 변경하지 않았습니다.")
                return 0

            new_value = 0 if args.revoke else 1
            cur.execute(
                "UPDATE ad_users SET can_view_fi = %s "
                f"WHERE username IN ({placeholders})",
                (new_value, *FI_USERNAMES),
            )
            if cur.rowcount > len(FI_USERNAMES):
                conn.rollback()
                print("ERROR: 예상보다 많은 행이 매칭되어 작업을 중단합니다.")
                return 1
            conn.commit()
            print(f"완료: FI 열람 권한을 {action}했습니다. (변경 행 {cur.rowcount}개)")
            return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
