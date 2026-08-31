"""구글 계정별 토큰 건강 자가 점검 (`google_account_health`) — 2026-08-31.

왜 필요한가:
    인증서류 찾기(`app/core/coa_finder.py`)는 서비스계정이 아니라 **각자의 OAuth**
    로 공유드라이브를 읽는다. 그런데 기존 `drive_shared_access` 검사는 고정된
    계정 하나(jeffrey@skin1004korea.com)만 찌른다. 프로덕션에는 연결 계정이
    15개 있고, 나머지 14개가 전부 죽어도 그 검사는 계속 초록이다.

    죽은 토큰은 화면에서 에러가 아니라 '전부 없음' 으로 보인다 — 그 계정을 쓰는
    사람만 조용히 오답을 받는다. 그래서 이 검사는 **연결된 전 계정**을 돌며
    누가 죽었는지 이름을 남긴다.
"""
from __future__ import annotations

from app.core import self_check as sc
from app.core.google_auth import CredentialLoadOutcome


class _FakeManager:
    """`load_credentials`/`has_credentials` 를 스텁으로 대체한다.

    ⛔ `_check_google_account_health` 는 `from app.core.google_auth import
       GoogleAuthManager` 를 함수 안에서 매 호출마다 새로 import 한다 — 즉
       실제로 참조를 읽는 이름은 `app.core.google_auth.GoogleAuthManager` 다.
       `self_check.GoogleAuthManager` 를 patch 해봐야 아무 효과가 없다
       (아래 `test_patch_target_is_the_real_one` 이 그 사실 자체를 증명한다).
    """

    def __init__(self, outcomes: dict[str, str], stored: set[str] | None = None):
        self._outcomes = outcomes
        # None 이면 outcomes 에 등록된 이메일 전부를 "저장된 계정" 으로 본다
        self._stored = stored if stored is not None else set(outcomes)

    def has_credentials(self, email: str) -> bool:
        return email in self._stored

    def load_credentials(self, email: str) -> CredentialLoadOutcome:
        status = self._outcomes.get(email, "ready")
        return CredentialLoadOutcome(status=status)


def _install(monkeypatch, outcomes, stored=None):
    import app.core.google_auth as ga_mod

    fake = _FakeManager(outcomes, stored)
    monkeypatch.setattr(ga_mod, "GoogleAuthManager", lambda: fake)
    return fake


def _users_rows(emails):
    return [{"id": i, "email": e, "name": e.split("@")[0]} for i, e in enumerate(emails, 1)]


def test_check_is_registered():
    ids = {c.id: c for c in sc.CHECKS}
    assert "google_account_health" in ids
    chk = ids["google_account_health"]
    assert chk.category == "datasource"
    assert chk.severity == sc.SEV_WARNING


def test_all_healthy_passes(monkeypatch):
    emails = ["a@skin1004korea.com", "b@skin1004korea.com", "c@skin1004korea.com"]
    monkeypatch.setattr(sc, "fetch_all", lambda *a, **k: _users_rows(emails))
    _install(monkeypatch, {e: "ready" for e in emails})

    r = sc._check_google_account_health()
    assert r.ok, r.detail
    assert "3명" in r.detail


def test_one_dead_account_fails_and_is_named(monkeypatch):
    emails = ["a@skin1004korea.com", "dead@skin1004korea.com", "c@skin1004korea.com"]
    monkeypatch.setattr(sc, "fetch_all", lambda *a, **k: _users_rows(emails))
    outcomes = {"a@skin1004korea.com": "ready", "dead@skin1004korea.com": "invalid",
                "c@skin1004korea.com": "ready"}
    _install(monkeypatch, outcomes)

    r = sc._check_google_account_health()
    assert not r.ok
    assert "dead@skin1004korea.com" in r.detail, r.detail
    assert "invalid" in r.detail


def test_no_connected_accounts_is_not_a_failure(monkeypatch):
    """아무도 등록 안 한 것은 고장이 아니다 — 기능 미사용은 미사용일 뿐이다."""
    monkeypatch.setattr(sc, "fetch_all", lambda *a, **k: _users_rows(["a@skin1004korea.com"]))
    _install(monkeypatch, {}, stored=set())  # 아무도 has_credentials 를 통과하지 못한다

    r = sc._check_google_account_health()
    assert r.ok, r.detail
    assert "없다" in r.detail


def test_transient_error_counts_as_dead_too(monkeypatch):
    emails = ["a@skin1004korea.com", "b@skin1004korea.com"]
    monkeypatch.setattr(sc, "fetch_all", lambda *a, **k: _users_rows(emails))
    outcomes = {"a@skin1004korea.com": "ready", "b@skin1004korea.com": "transient_error"}
    _install(monkeypatch, outcomes)

    r = sc._check_google_account_health()
    assert not r.ok
    assert "b@skin1004korea.com" in r.detail


def test_duplicate_emails_across_rows_are_counted_once(monkeypatch):
    """users/ad_users 조인이 같은 email 을 두 번 낼 수 있다 — 중복 계정으로 세면 안 된다."""
    rows = [
        {"id": 1, "email": "a@skin1004korea.com", "name": "a"},
        {"id": 2, "email": "a@skin1004korea.com", "name": "a"},
    ]
    monkeypatch.setattr(sc, "fetch_all", lambda *a, **k: rows)
    _install(monkeypatch, {"a@skin1004korea.com": "ready"})

    r = sc._check_google_account_health()
    assert r.ok
    assert "1명" in r.detail


def test_patching_wrong_location_leaves_real_loader_in_control(monkeypatch):
    """`self_check.GoogleAuthManager` 를 patch 하는 것은 **틀린 자리**다.

    `_check_google_account_health` 는 함수 안에서 매번
    `from app.core.google_auth import GoogleAuthManager` 를 새로 실행하므로
    실제로 읽는 이름은 `app.core.google_auth.GoogleAuthManager` 뿐이다.
    self_check 모듈에는 애초에 그 이름이 없어서, 거기다 값을 심어도
    (`raising=False`) 함수 본문은 여전히 진짜 `app.core.google_auth` 를
    import 한다 — 그러면 파일이 없는 이 환경에서 진짜 로더가 그대로 돌아
    `disconnected` 로 판정된다. 만약 patch 가 먹혔다면 여기서 미리 심어둔
    `_AlwaysDead` 대신 실제 계정 상태가 나왔을 것이므로, 이 결과 자체가
    "틀린 자리를 패치해도 효과가 없다"의 증거다.
    """
    monkeypatch.setattr(
        sc, "fetch_all",
        lambda *a, **k: _users_rows(["nobody-real@example-nonexistent.test"]))

    class _AlwaysDead:
        def has_credentials(self, email):
            return True

        def load_credentials(self, email):
            raise AssertionError("틀린 자리가 불렸다 — 패치가 잘못 먹었다")

    monkeypatch.setattr(sc, "GoogleAuthManager", _AlwaysDead, raising=False)

    # 진짜 app.core.google_auth.GoogleAuthManager 가 실제로 돈다 — 이 테스트
    # 환경엔 저장된 토큰 파일이 없으므로 has_credentials() 가 False 를 내고
    # "구글을 연결한 사용자가 없다"로 통과한다. _AlwaysDead 는 한 번도 안 불린다
    # (불렸다면 위에서 AssertionError 로 즉시 실패했을 것이다).
    r = sc._check_google_account_health()
    assert r.ok
    assert "없다" in r.detail


def test_side_effect_is_documented_as_intentional_exception():
    """이 검사는 부작용이 있다 — docstring 에 명시적으로 밝혀야 한다 (설계 원칙의 예외)."""
    doc = sc._check_google_account_health.__doc__ or ""
    assert "부작용" in doc
