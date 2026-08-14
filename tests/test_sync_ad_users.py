import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_ad_users.py"
SPEC = importlib.util.spec_from_file_location("sync_ad_users", SCRIPT_PATH)
sync_ad_users = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(sync_ad_users)


class StdoutSensitiveAttribute:
    """Minimal ldap3 Attribute double: .value is data, str() is display-only."""

    def __init__(self, value, rendered):
        self.value = value
        self.rendered = rendered

    def __bool__(self):
        return self.value is not None

    def __str__(self):
        return self.rendered


class FakeEntry:
    entry_dn = (
        "CN=조민경,OU=글로벌세일즈4,OU=영업2,OU=Sales Dept,"
        "OU=Brand Division,OU=Users,OU=Craver_Accounts,DC=ad,DC=cravercorp,DC=com"
    )
    sAMAccountName = StdoutSensitiveAttribute("mkcho", "CORRUPTED_USERNAME")
    displayName = StdoutSensitiveAttribute("조민경", r"\uc870\ubbfc\uacbd")
    name = StdoutSensitiveAttribute("조민경", r"\uc870\ubbfc\uacbd")
    mail = StdoutSensitiveAttribute(
        "mkcho@skin1004korea.com",
        "CORRUPTED_EMAIL",
    )


class SyncAdUsersEntryTest(unittest.TestCase):
    def test_entry_conversion_uses_ldap_values_not_stdout_rendering(self):
        user = sync_ad_users._entry_to_user(FakeEntry())

        self.assertEqual(user["username"], "mkcho")
        self.assertEqual(user["display_name"], "조민경")
        self.assertEqual(user["email"], "mkcho@skin1004korea.com")
        self.assertEqual(
            user["department"],
            "Craver_Accounts > Users > Brand Division > Sales Dept > 영업2 > 글로벌세일즈4",
        )


if __name__ == "__main__":
    unittest.main()
