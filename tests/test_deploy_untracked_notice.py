# -*- coding: utf-8 -*-
"""배포에 실리는데 git 이 모르는 소스를 알리는 경고.

⛔ **무엇을 지키나** (2026-09-08 실측): 이 저장소의 배포는 작업트리 통째 SFTP
   전송이라 git 이 배포 관문이 아니다. 그날 프로덕션은 HEAD 가 아니라 트리를
   돌고 있었고, 그중 로그인 경로 723줄과 크론 진입점이 `git log --all` 에
   한 번도 없었다. 에러도 경고도 없었다.

이 회귀가 지키는 성질은 셋이다:

  1. **막지 않는다** — 더티 트리를 막는 관문은 이 트리(세션 상시 공유)에서
     매번 걸려 그날로 우회된다
  2. **조용할 때는 아무 말도 안 한다** — 상시 뜨는 경고는 곧 안 읽힌다
  3. **전송 목록에서 판정한다** — 경로 규칙을 따로 적으면 `EXCLUDE_PATHS` 가
     바뀔 때 경고만 조용히 낡는다
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.deploy_preflight import (  # noqa: E402
    WATCHED_ROOTS,
    _load_bearing,
    format_untracked_notice,
    untracked_in_payload,
    untracked_paths,
)

DEPLOY = ROOT / "scripts" / "deploy_new_server.py"


# ---------------------------------------------------------------------------
# 무엇을 감시 대상으로 보는가
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "app/core/user_directory.py",
    "app/core/user_directory_migration.py",
    "app/agents/orchestrator.py",
    "scripts/sync_entra_users.py",
    "scripts/deploy_new_server.py",
])
def test_load_bearing_sources_are_watched(rel):
    """잃으면 아픈 것 — 실제 사고에 등장한 파일들이 전부 잡혀야 한다."""
    assert _load_bearing(rel), f"{rel} 이 감시 밖이다"


@pytest.mark.parametrize("rel,why", [
    ("tests/test_user_directory.py", "EXCLUDE_DIRS 라 전송되지 않는다"),
    ("tests/frontend/test_x.py", "같은 이유"),
    ("scripts/_pw_q1_chart.py", "밑줄은 일회성 관례다"),
    ("scripts/_export_gcp_convos.py", "같은 이유"),
    ("app/frontend/chat.js", "파이썬이 아니다"),
    ("app/static/style.css", "파이썬이 아니다"),
    ("README.md", "소스가 아니다"),
    ("setup.py", "최상위는 감시 대상이 아니다"),
])
def test_out_of_scope_paths_are_not_watched(rel, why):
    assert not _load_bearing(rel), f"{rel} 이 잡히면 안 된다 ({why})"


def test_scratch_prefix_only_applies_to_scripts():
    """⛔ `app/` 의 밑줄 파일은 빼지 않는다 — `app/core/_x.py` 는 일회성이 아니다.

    밑줄 관례는 `scripts/` 의 것이다. 이걸 app 까지 넓히면 진짜 모듈이
    조용히 감시 밖으로 나간다.
    """
    assert _load_bearing("app/core/_internal.py")
    assert not _load_bearing("scripts/_internal.py")


def test_watched_roots_are_the_two_deployed_source_trees():
    assert set(WATCHED_ROOTS) == {"app", "scripts"}


# ---------------------------------------------------------------------------
# 전송 목록에서 판정한다 (진짜 git 저장소로)
# ---------------------------------------------------------------------------

def _git(repo: Path, *args):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "app" / "core").mkdir(parents=True)
    (r / "scripts").mkdir()
    (r / "tests").mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "app" / "core" / "kept.py").write_text("A = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    return r


def _write(repo: Path, rel: str, lines: int) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"X{i} = {i}\n" for i in range(lines)), encoding="utf-8")
    return p


def test_a_clean_tree_says_nothing(repo):
    """기준선은 침묵이다 — 여기서 한 줄이라도 나오면 경고가 태어나자마자 죽는다."""
    payload = [repo / "app" / "core" / "kept.py"]
    assert untracked_in_payload(repo, payload) == []
    assert format_untracked_notice([]) == []


def test_untracked_source_in_payload_is_reported(repo):
    _write(repo, "app/core/ghost.py", 12)
    payload = [repo / "app" / "core" / "kept.py", repo / "app" / "core" / "ghost.py"]
    rows = untracked_in_payload(repo, payload)
    assert rows == [("app/core/ghost.py", 12)]


def test_untracked_but_not_shipped_is_not_reported(repo):
    """⛔ 판정은 전송 목록에서 한다.

    파일이 트리에 있어도 `collect()` 가 안 싣는다면 알릴 일이 아니다 —
    그렇지 않으면 `EXCLUDE_PATHS` 가 넓어질 때 경고만 남아 소음이 된다.
    """
    _write(repo, "app/core/ghost.py", 5)
    rows = untracked_in_payload(repo, [repo / "app" / "core" / "kept.py"])
    assert rows == []


def test_tracked_but_modified_is_not_reported(repo):
    """수정 중인 파일은 알리지 않는다 — 이 트리에서는 그게 정상 상태다."""
    (repo / "app" / "core" / "kept.py").write_text("A = 2\n", encoding="utf-8")
    rows = untracked_in_payload(repo, [repo / "app" / "core" / "kept.py"])
    assert rows == []


def test_scratch_scripts_are_not_reported(repo):
    """46개가 상시 떠 있으면 아무도 안 읽는다."""
    _write(repo, "scripts/_probe.py", 9)
    _write(repo, "scripts/real_job.py", 4)
    payload = [repo / "scripts" / "_probe.py", repo / "scripts" / "real_job.py"]
    assert untracked_in_payload(repo, payload) == [("scripts/real_job.py", 4)]


def test_ignored_files_are_not_reported(repo):
    """⛔ 무시는 결정이지 실수가 아니다 (`analyze_warns.py` 가 그 예다)."""
    (repo / ".gitignore").write_text("app/core/skipme.py\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")
    _write(repo, "app/core/skipme.py", 30)
    rows = untracked_in_payload(repo, [repo / "app" / "core" / "skipme.py"])
    assert rows == []


def test_rows_are_ordered_by_size(repo):
    """큰 것이 위다 — 잃었을 때 아픈 순서다."""
    _write(repo, "app/core/small.py", 3)
    _write(repo, "app/core/big.py", 300)
    _write(repo, "scripts/mid.py", 40)
    payload = [repo / "app" / "core" / "small.py", repo / "scripts" / "mid.py",
               repo / "app" / "core" / "big.py"]
    assert [r[0] for r in untracked_in_payload(repo, payload)] == [
        "app/core/big.py", "scripts/mid.py", "app/core/small.py"]


def test_non_ascii_paths_survive(repo):
    """⚠️ `-z` 없이 받으면 git 이 한글 경로를 이스케이프해서 조용히 못 잡는다."""
    _write(repo, "app/core/한글모듈.py", 7)
    rows = untracked_in_payload(repo, [repo / "app" / "core" / "한글모듈.py"])
    assert rows == [("app/core/한글모듈.py", 7)]


def test_untracked_paths_uses_z_separated_output():
    """위 회귀가 무엇을 지키는지 소스에 남겨 둔다."""
    src = (ROOT / "app" / "core" / "deploy_preflight.py").read_text(encoding="utf-8")
    assert '"-z"' in src, "git status 를 -z 로 받아야 한글 경로가 산다"


def test_git_failure_does_not_raise_through(tmp_path):
    """git 저장소가 아니면 예외가 아니라 실패로 알려야 한다 (호출부가 삼킨다)."""
    with pytest.raises(RuntimeError):
        untracked_paths(tmp_path)


# ---------------------------------------------------------------------------
# 사람이 읽는 문구
# ---------------------------------------------------------------------------

def test_notice_names_the_files_and_the_stake():
    lines = format_untracked_notice([("app/core/user_directory.py", 330),
                                     ("scripts/sync_entra_users.py", 30)])
    text = "\n".join(lines)
    assert "app/core/user_directory.py" in text
    assert "330" in text
    assert "프로덕션에만 남습니다" in text, "무엇을 잃는지 말해야 한다"


def test_notice_says_it_does_not_block():
    """⛔ 막는 관문으로 읽히면 다음 사람이 우회로부터 찾는다."""
    text = "\n".join(format_untracked_notice([("app/core/x.py", 1)]))
    assert "막지 않습니다" in text


def test_notice_caps_the_list():
    rows = [(f"app/core/m{i}.py", 100 - i) for i in range(25)]
    lines = format_untracked_notice(rows)
    listed = [l for l in lines if re.search(r"app/core/m\d+\.py", l)]
    assert len(listed) == 10
    assert any("외 15개" in l for l in lines)


# ---------------------------------------------------------------------------
# 배선 — 배포 스크립트가 실제로 부르는가
# ---------------------------------------------------------------------------

def test_deploy_script_calls_the_notice_with_the_payload():
    """⛔ `collect()` 결과를 그대로 넘겨야 경고와 전송이 같은 것을 본다."""
    src = DEPLOY.read_text(encoding="utf-8")
    assert "untracked_notice(files)" in src, "전송 목록을 넘겨서 불러야 한다"
    i_collect = src.index("files = collect()")
    i_notice = src.index("for line in untracked_notice(files):")
    assert i_collect < i_notice, "collect() 뒤에 와야 한다"


def test_the_notice_never_stops_the_deploy():
    """경고 블록 안에서 배포를 세우면 안 된다."""
    src = DEPLOY.read_text(encoding="utf-8")
    start = src.index("for line in untracked_notice(files):")
    block = src[start:start + 400]
    assert "return 1" not in block
    assert "sys.exit" not in block


def test_the_notice_helper_swallows_its_own_failure():
    """⚠️ git 이 없다고 배포가 죽으면 안 된다."""
    src = DEPLOY.read_text(encoding="utf-8")
    body = src[src.index("def untracked_notice"):src.index("def main() -> int:")]
    assert "except Exception" in body
    assert "raise" not in body


def test_console_encoding_is_handled():
    """⚠️ 콘솔이 cp949 다 — 진단이 죽으면 아무도 못 본다 (CLAUDE.md)."""
    src = DEPLOY.read_text(encoding="utf-8")
    start = src.index("for line in untracked_notice(files):")
    assert "cp949" in src[start:start + 300]


def test_blocking_preflight_stays_separate_from_the_notice():
    """⛔ 둘을 섞지 마라 — 하나는 막고 하나는 적는다.

    섞이면 "적기만 한다" 던 것이 언젠가 막는 쪽으로 흘러가고, 그러면
    이 트리에서 매번 걸려 우회된다.
    """
    src = DEPLOY.read_text(encoding="utf-8")
    i_pre = src.index("if not dry and not preflight(")
    i_notice = src.index("for line in untracked_notice(files):")
    assert i_pre < i_notice
    pre_block = src[i_pre:i_pre + 120]
    assert "untracked" not in pre_block
