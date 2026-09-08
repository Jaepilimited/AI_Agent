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


# ---------------------------------------------------------------------------
# 경고와 실제 전송이 같은 것을 보는가 — collect() 에 직접 물어본다
#
# ⚠️ 위의 배선 회귀는 소스 문자열을 본다. 이 회귀는 배포 스크립트를 **실제로
#    불러** 무엇이 실리는지 센다. 둘 다 필요하다 — 앞의 것은 호출 모양을,
#    뒤의 것은 전제(app/·scripts/ 는 실리고 tests/ 는 안 실린다)를 지킨다.
#    (ai-agent-de 세션이 같은 파일에 쓴 것을 합쳤다.)
# ---------------------------------------------------------------------------

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
    for r in WATCHED_ROOTS:
        assert any(s.startswith(r + "/") for s in sent), f"{r}/ 가 전송 목록에 없다"


# ---------------------------------------------------------------------------
# 아래는 같은 파일을 두 세션이 동시에 쓰면서 사라졌던 회귀다 (2026-09-09).
#
# ⚠️ ai-agent-de 세션과 내가 같은 기능을 나란히 구현했고, 커밋이 엇갈리면서
#    22문항이 8문항으로 줄었다. **되돌리지 않고 되살려 합친다** — 그쪽 문항
#    (특히 `collect()` 를 실제로 불러 전송 목록을 대조하는 것)은 그대로 둔다.
# ⛔ 이 사고 자체가 이 파일이 지키는 것과 같은 종류다: 에러가 나지 않는다.
#    테스트는 8개가 전부 통과했고, 사라진 14개는 아무 소리도 내지 않았다.
# ---------------------------------------------------------------------------

import re  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
DEPLOY = _ROOT / "scripts" / "deploy_new_server.py"
ROOT = _ROOT
from app.core.deploy_preflight import (  # noqa: E402
    WATCHED_ROOTS, _load_bearing, format_untracked_notice,
    untracked_in_payload, untracked_paths,
)


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    return _repo(tmp_path)


def _write2(repo, rel, lines):
    return _write(repo, rel, lines)


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


def test_a_clean_tree_says_nothing(repo):
    """기준선은 침묵이다 — 여기서 한 줄이라도 나오면 경고가 태어나자마자 죽는다."""
    payload = [repo / "app" / "core" / "tracked.py"]
    assert untracked_in_payload(repo, payload) == []
    assert format_untracked_notice([]) == []


def test_untracked_source_in_payload_is_reported(repo):
    _write(repo, "app/core/ghost.py", 12)
    payload = [repo / "app" / "core" / "tracked.py", repo / "app" / "core" / "ghost.py"]
    rows = untracked_in_payload(repo, payload)
    assert rows == [("app/core/ghost.py", 12)]


def test_untracked_but_not_shipped_is_not_reported(repo):
    """⛔ 판정은 전송 목록에서 한다.

    파일이 트리에 있어도 `collect()` 가 안 싣는다면 알릴 일이 아니다 —
    그렇지 않으면 `EXCLUDE_PATHS` 가 넓어질 때 경고만 남아 소음이 된다.
    """
    _write(repo, "app/core/ghost.py", 5)
    rows = untracked_in_payload(repo, [repo / "app" / "core" / "tracked.py"])
    assert rows == []


def test_tracked_but_modified_is_not_reported(repo):
    """수정 중인 파일은 알리지 않는다 — 이 트리에서는 그게 정상 상태다."""
    (repo / "app" / "core" / "tracked.py").write_text("A = 2\n", encoding="utf-8")
    rows = untracked_in_payload(repo, [repo / "app" / "core" / "tracked.py"])
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
