"""로그인 실패 응답 지연 회귀 — 2026-08-31.

프로덕션 실측: 팀 드롭다운을 잘못 고른 로그인 시도가 135.9초 걸렸고, 곧바로 재시도한
같은 요청도 135.2초 걸렸다. 원인은 `_lookup_ad_user`가 조회 실패 시 라이브 AD 재동기화
(전체 약 362명 upsert)를 **요청 경로에서 await** 했기 때문이다. 게다가 쿨다운은 동기화가
*시작*될 때 찍혀서, 동기화 자체가 쿨다운(120초)보다 오래 걸리면(135초) 사실상 매 실패마다
새 전체 동기화가 다시 돈다 — 속도 제한이 전혀 제한하지 못했다.

이 파일은 세 가지를 지킨다:
1. 조회 실패는 전체 동기화를 기다리지 않고 즉시 응답한다.
2. 동기화 도중/직후에 온 두 번째 실패는 새 동기화를 또 시작하지 않는다.
3. 정상 조회(비밀번호 오류 포함)는 영향받지 않는다.

LDAP은 APP 서버에서만 열려 있어 여기서는 실제 AD에 붙을 수 없다 — `fetch_ad_users`/
`sync_to_db`를 스텁으로 대체해 제어 흐름만 검증한다.
"""

from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

from app.api import auth_api


class _FakeSyncModule(types.ModuleType):
    """scripts.sync_ad_users 자리에 끼워 넣는 스텁 — 지연·호출 횟수를 계측한다."""

    def __init__(self, delay: float = 0.0):
        super().__init__("scripts.sync_ad_users")
        self.delay = delay
        self.fetch_calls = 0
        self.sync_calls = 0
        self._locked = False

    def fetch_ad_users(self, retries: int = 1):
        self.fetch_calls += 1
        if self.delay:
            time.sleep(self.delay)
        return [{"display_name": "테스트유저", "username": "test", "email": "t@x.com",
                  "department": "동남아시아1팀"}]

    def sync_to_db(self, users, dry_run: bool = False):
        self.sync_calls += 1

    def _acquire_lock(self) -> bool:
        if self._locked:
            return False
        self._locked = True
        return True

    def _release_lock(self):
        self._locked = False


@pytest.fixture(autouse=True)
def _reset_resync_state(monkeypatch, password_login_on):
    """모듈 전역 상태(진행 중 플래그·쿨다운 시각)를 매 테스트마다 초기화한다.

    ⚠️ `password_login_on` 도 함께 받는다 — 2026-09-08 부터 로컬 ID/PW 는 기본
       꺼짐이라, 켜 두지 않으면 `signin` 이 관문에서 403 으로 끝나 **여기서
       재려는 지연을 아예 재지 못한다** (통과가 아니라 무의미해진다).
    """
    monkeypatch.setattr(auth_api, "_resync_in_progress", False)
    monkeypatch.setattr(auth_api, "_last_ad_resync_finished", 0.0)
    yield


def _install_fake_sync(monkeypatch, delay: float = 0.0) -> _FakeSyncModule:
    fake = _FakeSyncModule(delay=delay)
    monkeypatch.setitem(sys.modules, "scripts.sync_ad_users", fake)
    return fake


async def _drain_background_tasks():
    """asyncio.create_task 로 띄운 백그라운드 리싱크가 완료될 시간을 준다."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ── 1. 조회 실패는 즉시 응답한다 (전체 동기화를 기다리지 않는다) ──

@pytest.mark.asyncio
async def test_lookup_miss_returns_without_waiting_for_full_sync(monkeypatch):
    fake = _install_fake_sync(monkeypatch, delay=2.0)  # "느린 AD" 시뮬레이션

    async def fake_fetch_one(sql, params=()):
        return None  # DB에 없음 — 매번 미스

    monkeypatch.setattr(auth_api, "_db_fetch_one", fake_fetch_one)

    start = time.monotonic()
    result = await auth_api._lookup_ad_user("동남아시아1팀", "테스트유저", None)
    elapsed = time.monotonic() - start

    assert result is None
    assert elapsed < 1.0, f"조회 실패가 백그라운드 동기화를 기다렸다 ({elapsed:.2f}s)"

    # 백그라운드로는 실제로 동기화가 걸렸어야 한다 (same-day hire 자가치유 유지).
    await asyncio.sleep(2.5)
    assert fake.fetch_calls == 1


@pytest.mark.asyncio
async def test_signin_miss_answers_in_about_a_second(monkeypatch):
    """팀 드롭다운을 잘못 고른 로그인 — 135초가 아니라 곧바로 401 이어야 한다."""
    _install_fake_sync(monkeypatch, delay=3.0)

    async def fake_fetch_one(sql, params=()):
        return None

    monkeypatch.setattr(auth_api, "_db_fetch_one", fake_fetch_one)

    from fastapi import HTTPException, Response

    req = auth_api.SigninRequest(department="동남아시아팀", name="테스트유저", password="whatever1", id=None)

    start = time.monotonic()
    with pytest.raises(HTTPException) as exc:
        await auth_api.signin(req, Response())
    elapsed = time.monotonic() - start

    assert exc.value.status_code == 401
    assert elapsed < 1.0, f"signin 실패가 {elapsed:.2f}s 걸렸다 — 백그라운드로 빠지지 않았다"


# ── 2. 동기화 도중/직후 두 번째 실패는 새 동기화를 또 시작하지 않는다 ──

@pytest.mark.asyncio
async def test_second_miss_during_sync_does_not_start_another(monkeypatch):
    fake = _install_fake_sync(monkeypatch, delay=0.3)

    async def fake_fetch_one(sql, params=()):
        return None

    monkeypatch.setattr(auth_api, "_db_fetch_one", fake_fetch_one)

    # 첫 번째 미스 — 백그라운드 동기화 예약.
    await auth_api._lookup_ad_user("동남아시아1팀", "테스트유저", None)
    # 아직 끝나지 않았을 시점에 두 번째 미스가 온다.
    await auth_api._lookup_ad_user("동남아시아1팀", "테스트유저", None)

    await asyncio.sleep(0.6)  # 첫 동기화가 끝날 시간을 준다

    assert fake.fetch_calls == 1, "동기화가 진행 중인데 두 번째가 또 시작됐다"


@pytest.mark.asyncio
async def test_cooldown_is_measured_from_completion_not_start(monkeypatch):
    """실측 버그: 쿨다운(120s)이 동기화 시작 시각에 찍혀서, 동기화 자체가 135초 걸리면
    끝났을 땐 이미 쿨다운이 지나 있어 다음 실패가 곧바로 새 동기화를 또 돌렸다.
    동기화가 끝난 직후 온 실패는 쿨다운이 소진되지 않았어야 한다."""
    fake = _install_fake_sync(monkeypatch, delay=0.2)

    async def fake_fetch_one(sql, params=()):
        return None

    monkeypatch.setattr(auth_api, "_db_fetch_one", fake_fetch_one)
    monkeypatch.setattr(auth_api, "_AD_RESYNC_COOLDOWN", 5)  # 테스트용으로 축소

    await auth_api._lookup_ad_user("동남아시아1팀", "테스트유저", None)
    await asyncio.sleep(0.4)  # 동기화 완료 대기
    assert fake.fetch_calls == 1

    # 완료 직후 온 두 번째 실패 — 쿨다운(5s) 안이므로 또 돌면 안 된다.
    await auth_api._lookup_ad_user("동남아시아1팀", "테스트유저", None)
    await asyncio.sleep(0.1)
    assert fake.fetch_calls == 1, "동기화 완료 직후인데 쿨다운을 무시하고 또 시작했다"


# ── 3. 정상 조회 / 비밀번호 오류 경로는 그대로다 ──

@pytest.mark.asyncio
async def test_successful_lookup_does_not_trigger_resync(monkeypatch):
    fake = _install_fake_sync(monkeypatch, delay=0.0)

    async def fake_fetch_one(sql, params=()):
        return {"id": 7, "display_name": "테스트유저", "email": "t@x.com", "department": "동남아시아1팀"}

    monkeypatch.setattr(auth_api, "_db_fetch_one", fake_fetch_one)

    result = await auth_api._lookup_ad_user("동남아시아1팀", "테스트유저", None)
    assert result is not None

    await asyncio.sleep(0.1)
    assert fake.fetch_calls == 0, "성공한 조회인데 재동기화를 시작했다"


@pytest.mark.asyncio
async def test_wrong_password_path_unaffected(monkeypatch):
    """AD 사용자는 찾았지만 비밀번호가 틀린 경우 — 재동기화도 안 타고 메시지도 그대로."""
    fake = _install_fake_sync(monkeypatch, delay=0.0)

    async def fake_fetch_one(sql, params=()):
        if "ad_users" in sql:
            return {"id": 7, "display_name": "테스트유저", "email": "t@x.com", "department": "동남아시아1팀"}
        if "FROM users" in sql:
            import bcrypt
            return {"id": 1, "password_hash": bcrypt.hashpw(b"correct-pw", bcrypt.gensalt()).decode(),
                    "role": "user", "allowed_models": ""}
        return None

    monkeypatch.setattr(auth_api, "_db_fetch_one", fake_fetch_one)

    from fastapi import HTTPException, Response

    req = auth_api.SigninRequest(department="동남아시아1팀", name="테스트유저", password="wrong-pw", id=None)
    with pytest.raises(HTTPException) as exc:
        await auth_api.signin(req, Response())

    assert exc.value.status_code == 401
    assert exc.value.detail == "비밀번호가 일치하지 않습니다"
    await asyncio.sleep(0.1)
    assert fake.fetch_calls == 0, "비밀번호 오류인데 AD 재동기화가 돌았다"


# ── 4. 팀을 잘못 고른 미스는 더 나은 메시지를 준다 (느린 조회 없이) ──

@pytest.mark.asyncio
async def test_signin_hints_wrong_team_when_name_exists_elsewhere(monkeypatch):
    _install_fake_sync(monkeypatch, delay=0.0)

    async def fake_fetch_one(sql, params=()):
        if "department = %s AND display_name = %s" in sql:
            return None  # 고른 팀에서는 못 찾는다
        if "display_name = %s AND department != %s" in sql:
            return {"1": 1}  # 다른 팀에는 있다
        return None

    monkeypatch.setattr(auth_api, "_db_fetch_one", fake_fetch_one)

    from fastapi import HTTPException, Response

    req = auth_api.SigninRequest(department="동남아시아팀", name="테스트유저", password="whatever1", id=None)
    with pytest.raises(HTTPException) as exc:
        await auth_api.signin(req, Response())

    assert exc.value.status_code == 401
    assert "찾지 못했습니다" in exc.value.detail or "다른 팀" in exc.value.detail
    assert exc.value.detail != "사용자를 찾을 수 없습니다"


@pytest.mark.asyncio
async def test_signin_generic_message_when_name_does_not_exist_anywhere(monkeypatch):
    _install_fake_sync(monkeypatch, delay=0.0)

    async def fake_fetch_one(sql, params=()):
        return None  # 어디에도 없다

    monkeypatch.setattr(auth_api, "_db_fetch_one", fake_fetch_one)

    from fastapi import HTTPException, Response

    req = auth_api.SigninRequest(department="동남아시아팀", name="존재안함", password="whatever1", id=None)
    with pytest.raises(HTTPException) as exc:
        await auth_api.signin(req, Response())

    assert exc.value.status_code == 401
    assert exc.value.detail == "사용자를 찾을 수 없습니다"


@pytest.mark.asyncio
async def test_id_based_lookup_miss_skips_wrong_team_hint(monkeypatch):
    """id로 조회했는데 없으면(계정 비활성 등) '다른 팀' 힌트를 지어내지 않는다."""
    _install_fake_sync(monkeypatch, delay=0.0)

    calls = {"other_dept_checked": False}

    async def fake_fetch_one(sql, params=()):
        if "id = %s" in sql:
            return None
        if "department != %s" in sql:
            calls["other_dept_checked"] = True
            return {"1": 1}
        return None

    monkeypatch.setattr(auth_api, "_db_fetch_one", fake_fetch_one)

    from fastapi import HTTPException, Response

    req = auth_api.SigninRequest(department="동남아시아1팀", name="테스트유저", password="whatever1", id=999)
    with pytest.raises(HTTPException) as exc:
        await auth_api.signin(req, Response())

    assert exc.value.status_code == 401
    assert exc.value.detail == "사용자를 찾을 수 없습니다"
    assert calls["other_dept_checked"] is False
