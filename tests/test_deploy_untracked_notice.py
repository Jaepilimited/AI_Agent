# -*- coding: utf-8 -*-
"""배포 경고 — git 이 모르는 소스가 전송에 실리면 **말한다**. 2026-09-08.

⛔ **왜 있나**: 이 저장소의 배포는 git 이 아니라 작업트리 통째 SFTP 전송이다.
   그래서 "커밋 안 한 코드가 프로덕션에 있다" 가 사고가 아니라 **정상 경로**다.
   2026-09-08 실측: 프로덕션이 HEAD 가 아니라 작업트리를 돌고 있었고,
   `app/core/user_directory.py`(330줄)·`user_directory_migration.py`(393줄) 등
   **로그인 경로 코드가 git 에 한 번도 담긴 적이 없었다.** 트리가 날아가면
   그 코드로 돌아갈 길이 프로덕션 서버뿐이었다.

⛔ **막지 않는다.** 세션 서넛이 트리를 상시 공유해서 깨끗한 순간이 사실상 없다 —
   매번 걸리는 관문은 그날로 꺼지고, 꺼진 관문은 아무것도 안 지킨다
   (CLAUDE.md 가 훅에 대해 이미 같은 말을 한다).

⛔ **이 회귀가 없으면 침묵을 검증할 수 없다.** 조용한 관문과 고장난 관문은
   글자 그대로 똑같이 생겼다 — 실제로 처음 돌렸을 때 아무것도 안 찍혀서
   고장을 의심했고, 확인해 보니 정상이었다. 그 구분을 코드가 해야 한다.
"""
import subprocess

import pytest

from app.core import deploy_preflight as PF


def _repo(tmp_path):
    """진짜 git 저장소를 만든다 — `git status` 를 실제로 부르는 함수라서."""
    run = lambda *a: subprocess.run(a, cwd=tmp_path, capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (tmp_path / "app" / "core").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "app" / "core" / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    run("git", "add", "app/core/tracked.py")
    run("git", "commit", "-qm", "base")
    return tmp_path


def _write(root, rel, lines=3):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(f"# {i}" for i in range(lines)) + "\n", encoding="utf-8")
    return p


# ── 울려야 할 때 울리는가 ──────────────────────────────────────────────────

def test_it_speaks_when_a_login_path_file_is_untracked(tmp_path):
    """⛔ 이게 2026-09-08 에 실제로 있었던 상태다."""
    root = _repo(tmp_path)
    p = _write(root, "app/core/user_directory.py", lines=330)

    rows = PF.untracked_in_payload(root, [p])
    assert rows == [("app/core/user_directory.py", 330)]

    notice = PF.format_untracked_notice(rows)
    body = "\n".join(notice)
    assert "app/core/user_directory.py" in body and "330" in body
    # ⛔ 막지 않는다는 사실이 화면에 있어야 한다 — 없으면 다음 사람이 게이트로 읽는다
    assert "막지 않습니다" in body


def test_scripts_count_too(tmp_path):
    """⛔ `app/**` 만 보면 **크론 진입점이 안 잡힌다.**

    실측: `scripts/sync_entra_users.py` 는 AD 명단 동기화(매일 22:00)의 진입점인데
    git 에 없었다. 사라지면 매일 밤 동기화가 멈추고 **그 실패는 조용하다.**
    """
    root = _repo(tmp_path)
    p = _write(root, "scripts/sync_entra_users.py", lines=30)
    assert PF.untracked_in_payload(root, [p]) == [("scripts/sync_entra_users.py", 30)]


# ── 조용해야 할 때 조용한가 (이쪽이 관문의 수명을 정한다) ──────────────────

def test_it_says_nothing_when_everything_is_tracked(tmp_path):
    """⚠️ 조용한 것이 정상이다 — 매번 뜨는 경고는 곧 아무도 안 읽는다."""
    root = _repo(tmp_path)
    assert PF.untracked_in_payload(root, [root / "app/core/tracked.py"]) == []
    assert PF.format_untracked_notice([]) == []


def test_a_modified_tracked_file_is_not_flagged(tmp_path):
    """⚠️ 고쳐 둔 것은 HEAD 에 원본이 있다 — 잃을 수 없는 것만 말한다.

    이 트리는 세션 서넛이 공유해서 `M` 이 상시 수십 개다. 그것까지 세면
    경고가 매번 떠서 죽는다.
    """
    root = _repo(tmp_path)
    p = root / "app" / "core" / "tracked.py"
    p.write_text("x = 2\n", encoding="utf-8")
    assert PF.untracked_in_payload(root, [p]) == []


def test_scratch_probes_are_ignored(tmp_path):
    """⚠️ `scripts/_*.py` 는 일회성 조사용이다 — 실측 44개가 상시 떠 있다.

    이걸 세면 첫날부터 44줄짜리 경고가 매번 떠서 진짜 신호를 덮는다.
    """
    root = _repo(tmp_path)
    scratch = _write(root, "scripts/_pw_probe.py")
    real = _write(root, "scripts/sync_something.py")
    rows = PF.untracked_in_payload(root, [scratch, real])
    assert [r[0] for r in rows] == ["scripts/sync_something.py"]


def test_tests_are_ignored_because_they_are_never_deployed(tmp_path):
    """⚠️ 취향이 아니라 사실이다 — 아래 회귀가 배포 스크립트에 직접 물어본다."""
    root = _repo(tmp_path)
    t = _write(root, "tests/test_x.py")
    assert PF.untracked_in_payload(root, [t]) == []


def test_non_python_is_ignored(tmp_path):
    root = _repo(tmp_path)
    assert PF.untracked_in_payload(root, [_write(root, "app/core/notes.md")]) == []


# ── 경고와 실제 전송이 같은 것을 보는가 ────────────────────────────────────

def test_the_notice_watches_what_deploy_actually_sends():
    """⛔ 상수를 베껴 두면 `EXCLUDE_PATHS` 가 바뀔 때 **경고만 조용히 낡는다.**

    `knowledge_map` 을 이름으로 걸렀다가 소스 패키지 `app/knowledge_map/` 이
    통째로 배포에서 빠진 전례가 있다 (2026-08-05). 그래서 목록을 적지 않고
    **배포 스크립트의 `collect()` 에 직접 물어본다.**
    """
    import importlib.util
    from pathlib import Path

    proj = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "_dep", proj / "scripts" / "deploy_new_server.py")
    dep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dep)

    sent = {str(Path(p).resolve().relative_to(proj)).replace("\\", "/")
            for p in dep.collect()}
    assert any(s.startswith("app/") for s in sent), "app/ 이 안 실린다면 전제가 틀렸다"
    assert any(s.startswith("scripts/") for s in sent), "scripts/ 도 실려야 감시 대상이다"
    # ⛔ 배포에 안 실리는 것을 경고하면 소음이다 — tests/ 는 EXCLUDE_DIRS 다
    assert not [s for s in sent if s.startswith("tests/")], \
        "tests/ 가 실리기 시작했다면 _load_bearing 도 함께 고쳐야 한다"

    # 감시 대상 루트가 실제 전송 대상 안에 있는가
    for r in PF.WATCHED_ROOTS:
        assert any(s.startswith(r + "/") for s in sent), f"{r}/ 가 전송 목록에 없다"
