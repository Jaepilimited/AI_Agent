r"""이름이 `\uXXXX` 로 이스케이프된 채 저장되면 로그인 화면에서 자기 이름을 못 찾는다.

**실제 사고 (2026-09-01, 이주원 님 · CS 운영팀)**

`users.id=68` 의 `display_name` 이 리터럴 `이주원` 이었다 (2026-06-30 가입분).
로그인 자동완성은 `_get_ad_cache()` 의 `COALESCE(u.display_name, ad.display_name)` 를
그리므로, **`ad_users` 에는 `이주원` 이 멀쩡히 있는데도** 깨진 쪽이 이긴다.

왜 조용했나 — 셋이 겹쳤다:

1. `_check_name_encoding` 이 **`ad_users` 만** 봤다. `users` 는 검사 밖이라 2개월간 안 보였다
2. 이름 검색(`_match_cache`)은 `ad_name` 으로도 맞춰서 **행은 뜬다** — 글자만 깨져 보인다
3. 그 행을 클릭하지 않으면 `auth.js` 가 "이름과 소속 팀을 선택해 주세요" 로 **프론트에서**
   막는다 → **서버에 요청이 아예 안 간다**. 실측: 그날 signin 실패 0건, 에러 로그 0건.
   사람은 못 들어가는데 서버 기록은 완벽하게 정상이다
"""

import pytest

from app.core import self_check

ESCAPED = "\\uc774\\uc8fc\\uc6d0"
DECODED = "이주원"


def _rows(monkeypatch, users, ad_users):
    """`fetch_all` 을 테이블 이름으로 갈라 가짜 행을 돌려준다."""

    seen = []

    def fake_fetch_all(sql, params=None):
        seen.append(sql)
        if "FROM users" in sql:
            return users
        if "FROM ad_users" in sql:
            return ad_users
        return []

    monkeypatch.setattr(self_check, "fetch_all", fake_fetch_all)
    return seen


# ── 검사: users 도 봐야 한다 ────────────────────────────────────────────────

def test_the_check_looks_at_users_not_only_ad_users(monkeypatch):
    """⛔ `ad_users` 만 보면 로그인 화면이 실제로 읽는 값을 검사하지 않는 것이다."""

    seen = _rows(monkeypatch, users=[], ad_users=[])
    self_check._check_name_encoding()

    assert any("FROM users" in sql for sql in seen), "users 를 보지 않는다"
    assert any("FROM ad_users" in sql for sql in seen), "ad_users 검사를 잃었다"


def test_an_escaped_name_in_users_fails_the_check(monkeypatch):
    _rows(
        monkeypatch,
        users=[{"id": 68, "display_name": ESCAPED, "email": "ad_35260@noemail.local"}],
        ad_users=[],
    )

    result = self_check._check_name_encoding()

    assert not result.ok
    assert "68" in result.detail or "1" in result.detail


def test_clean_names_pass(monkeypatch):
    _rows(monkeypatch, users=[], ad_users=[])

    assert self_check._check_name_encoding().ok


# ── 치유: users 는 활성이어도 고쳐야 한다 ───────────────────────────────────

def test_an_escaped_users_row_is_repairable(monkeypatch):
    r"""⚠️ `ad_users` 는 활성 계정을 안 고친다 (다음 sync 가 덮어쓴다).
    `users` 는 **덮어써 주는 것이 없다** — 여기서 안 고치면 영영 그대로다."""

    _rows(
        monkeypatch,
        users=[{"id": 68, "display_name": ESCAPED, "email": "x@y.z"}],
        ad_users=[],
    )

    result = self_check._check_name_encoding()

    assert result.repairable
    assert 68 in result.repair_payload["user_ids"]


def test_repair_decodes_the_escape(monkeypatch):
    written = []
    monkeypatch.setattr(
        self_check, "fetch_one",
        lambda sql, params=None: {"display_name": ESCAPED},
    )
    monkeypatch.setattr(
        self_check, "execute",
        lambda sql, params=None: written.append((sql, params)),
    )

    self_check._repair_name_encoding({"ids": [], "user_ids": [68]})

    assert written, "아무것도 쓰지 않았다"
    sql, params = written[-1]
    assert "UPDATE users" in sql
    assert params[0] == DECODED


def test_repair_never_writes_something_still_escaped(monkeypatch):
    r"""⛔ 디코딩이 실패했는데 덮어쓰면 더 나쁜 값이 굳는다. 되돌릴 수 없다."""

    monkeypatch.setattr(
        self_check, "fetch_one",
        lambda sql, params=None: {"display_name": "\\uZZZZ"},
    )
    monkeypatch.setattr(
        self_check, "execute",
        lambda sql, params=None: pytest.fail("깨진 값을 덮어썼다"),
    )

    self_check._repair_name_encoding({"ids": [], "user_ids": [68]})


def test_repair_leaves_healthy_names_alone(monkeypatch):
    monkeypatch.setattr(
        self_check, "fetch_one", lambda sql, params=None: {"display_name": DECODED},
    )
    monkeypatch.setattr(
        self_check, "execute",
        lambda sql, params=None: pytest.fail("멀쩡한 이름을 건드렸다"),
    )

    self_check._repair_name_encoding({"ids": [], "user_ids": [68]})


# ── 유입 차단: 가입할 때 깨진 이름을 복사하지 않는다 ────────────────────────

def test_signup_normalizes_an_escaped_ad_name():
    r"""가입은 `ad_users.display_name` 을 **복사**한다. 그때 깨져 있으면 그 사본이
    `users` 에 굳고, `ad_users` 가 나중에 고쳐져도 사본은 그대로다 — 이번 사고의 경로다."""

    from app.api.auth_api import _decode_escaped_name

    assert _decode_escaped_name(ESCAPED) == DECODED
    assert _decode_escaped_name(DECODED) == DECODED
    assert _decode_escaped_name("") == ""
    # 디코딩이 안 되면 원본을 그대로 둔다 (지어내지 않는다)
    assert _decode_escaped_name("\\uZZZZ") == "\\uZZZZ"
