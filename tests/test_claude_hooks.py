# -*- coding: utf-8 -*-
"""Claude Code 훅 회귀 — `scripts/hooks/` 의 두 훅.

⛔ **훅은 양방향으로 검사한다.** 막아야 할 것을 막는지만 보면 반쪽이다 —
   정상 작업을 막는 훅은 그 다음부터 통째로 꺼지고, 그러면 아무것도 안 지킨다
   (알림을 늘리면 아무도 안 읽게 되는 것과 같은 이유).

⚠️ 훅이 예외로 죽으면 **작업이 막힌다.** 깨진 입력·없는 파일에도 exit 0 이어야 한다.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(REPO, "scripts", "hooks")


def _load(name: str):
    path = os.path.join(HOOKS, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_hook_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(name: str, payload: dict) -> int:
    """훅을 실제 프로세스로 돌린다 — 실행 경로 그대로 재는 것이 목적이다."""
    proc = subprocess.run(
        [sys.executable, os.path.join(HOOKS, f"{name}.py")],
        input=json.dumps(payload), text=True, encoding="utf-8",
        capture_output=True, cwd=REPO,
    )
    return proc.returncode


# ── guard_destructive: 막아야 하는 것 ────────────────────────────────────────

@pytest.mark.parametrize("command,why", [
    ("python scripts/notion_user_guide.py", "가이드 페이지를 통째로 지운다"),
    ("pm2 delete skin1004-prod", "롤백 대비 서버"),
    ("pm2 stop skin1004-prod", "롤백 대비 서버"),
    ("pm2 kill skin1004-prod", "롤백 대비 서버"),
    ("pm2 reload skin1004-dev", "Windows fork 모드 고아 프로세스"),
    ("git commit -am wip", "공유 작업트리"),
    ("git commit --all -m x", "공유 작업트리"),
])
def test_destructive_commands_are_blocked(command, why):
    guard = _load("guard_destructive")
    assert guard.check("Bash", {"command": command}), f"막지 못했다: {command} ({why})"


def test_env_edits_are_blocked_but_examples_are_not():
    """`.env` 는 배포 제외라 서버와 조용히 어긋난다. 템플릿은 그냥 파일이다."""
    guard = _load("guard_destructive")
    assert guard.check("Edit", {"file_path": "/repo/.env"})
    assert guard.check("Write", {"file_path": "C:\\repo\\.env"})
    assert not guard.check("Edit", {"file_path": "/repo/.env.example"})
    assert not guard.check("Edit", {"file_path": "/repo/.env.sample"})


# ── guard_destructive: 통과해야 하는 것 ──────────────────────────────────────

@pytest.mark.parametrize("command", [
    "pm2 restart skin1004-prod",          # 정상 반영 경로
    "pm2 restart skin1004-dev",
    "pm2 delete some-other-app",          # 다른 앱은 우리 규칙 대상이 아니다
    "pm2 logs skin1004-prod --lines 30",
    'git commit -m "fix: all of the things"',   # 메시지 속 낱말에 걸리면 안 된다
    "git add app/x.py && git commit -m x",
    "git status --porcelain",
    "python scripts/sync_ad_users.py --dry-run",
])
def test_normal_work_is_not_blocked(command):
    guard = _load("guard_destructive")
    assert guard.check("Bash", {"command": command}) is None, f"정상 작업을 막았다: {command}"


# ── 작업트리 전체를 건드리는 git ────────────────────────────────────────────
#
# ⛔ `git commit -a` 만 막아 두면 규칙이 아니라 **철자 검사**다. 2026-08-31 실측에서
#    똑같이 쓸어 담는 `add -A`·`add .`·`add -u` 와, 아예 지워 버리는
#    `reset --hard`·`checkout -- .`·`restore .`·`clean -fd` 가 전부 통과했다.

@pytest.mark.parametrize("command", [
    "git add -A",
    "git add --all",
    "git add .",
    "git add -u",
    "git add -Av",
    "git add -A && git commit -m x",
    "git reset --hard",
    "git reset --hard HEAD~1",
    "git checkout -- .",
    "git checkout .",
    "git restore .",
    "git restore -- .",
    "git clean -fd",
    "git clean -fdx",
    "git stash",
    "git stash push",
    "git stash save wip",
])
def test_whole_tree_git_is_blocked(command):
    """남의 미커밋 변경을 쓸어가거나 **지우는** 명령."""
    guard = _load("guard_destructive")
    assert guard.check("Bash", {"command": command}), f"막지 못했다: {command}"


@pytest.mark.parametrize("command", [
    "git add app/frontend/chat.js",
    "git add -A -- app/frontend",        # 범위를 지목했으면 전체가 아니다
    "git checkout -- app/frontend/chat.js",
    "git checkout master",
    "git checkout -b feature/x",
    "git restore --staged app/x.py",     # 스테이징 해제는 파일을 지우지 않는다
    "git clean -nd",                     # 미리보기
    "git stash push -- app/frontend/chat.js",
    "git stash list",
    "git stash pop",
    "git reset HEAD app/x.py",
    "git diff --cached --name-only",
    "./sshenv/Scripts/python scripts/deploy_new_server.py was",
])
def test_scoped_git_is_not_blocked(command):
    """⚠️ 경로를 지목한 작업까지 막으면 훅이 방해물이 되고, 방해물은 꺼진다."""
    guard = _load("guard_destructive")
    assert guard.check("Bash", {"command": command}) is None, f"정상 작업을 막았다: {command}"


# ── 캐시 번호를 안 올린 프론트 자산 커밋 ────────────────────────────────────
#
# ⛔ 2026-08-31 실측: 다른 세션이 09:24 에 `chat.js?v=270 → 271` 을 올렸고, 몇 시간 뒤
#    다른 수정도 **같은 271** 로 커밋됐다. 271 을 이미 받아 둔 브라우저는 뒤 수정을
#    영영 안 받는다 — 배포는 성공하고 서버는 새 파일을 주므로 아무도 모른다.

_HTML = ('<link href="/static/style.css?v=193">'
         '<script src="/frontend/chat.js?v=271"></script>')


def test_same_cache_version_for_a_changed_asset_is_caught():
    guard = _load("guard_destructive")
    stale = guard.stale_cache_versions(["app/frontend/chat.js"], _HTML, _HTML)
    assert stale == ["chat.js?v=271"], stale
    # 자산이 여럿이면 여럿 다 짚는다
    both = guard.stale_cache_versions(
        ["app/frontend/chat.js", "app/static/style.css"], _HTML, _HTML)
    assert both == ["chat.js?v=271", "style.css?v=193"], both


def test_bumped_version_and_unrelated_files_pass():
    """⚠️ 반대 방향이 없으면 이 검사는 커밋을 통째로 막는 장애물이 된다."""
    guard = _load("guard_destructive")
    bumped = _HTML.replace("chat.js?v=271", "chat.js?v=272")
    assert guard.stale_cache_versions(["app/frontend/chat.js"], _HTML, bumped) == []
    assert guard.stale_cache_versions(["app/core/safety.py"], _HTML, _HTML) == []
    assert guard.stale_cache_versions([], _HTML, _HTML) == []


def test_cache_version_check_only_looks_at_commits():
    """조회 명령까지 git 을 세 번 부르면 훅이 느려진다."""
    guard = _load("guard_destructive")
    assert guard.check_cache_version("git status") is None
    assert guard.check_cache_version("ls -al") is None


def test_talking_about_a_rule_is_not_doing_it():
    """⛔ **글을 명령으로 읽지 마라.** 훅을 붙인 첫날 바로 걸린 오탐이다 —
    커밋 메시지 heredoc 안에 규칙을 설명하며 스크립트 이름을 적었더니 막혔다.
    정상 작업을 막는 훅은 그날로 꺼지고, 꺼진 훅은 아무것도 지키지 않는다.
    """
    guard = _load("guard_destructive")

    commit_message = (
        "git commit -q -F - <<'MSG'\n"
        "feat(hooks): 규칙을 코드로 옮긴다\n"
        "  · scripts/notion_user_guide.py 실행 - 페이지를 통째로 지운다\n"
        "  · pm2 delete skin1004-prod - 롤백 대비\n"
        "  · git commit -a - 공유 작업트리\n"
        "MSG\n"
    )
    assert guard.check("Bash", {"command": commit_message}) is None, \
        "규칙을 설명하는 글을 명령으로 읽었다"

    # 언급이 아니라 진짜 실행이면 그대로 막아야 한다.
    assert guard.check("Bash", {"command": "python scripts/notion_user_guide.py"})
    assert guard.check("Bash", {"command": "cd x && py scripts/notion_user_guide.py"})

    # 경로만 적은 것(문서·grep)은 실행이 아니다.
    assert guard.check(
        "Bash", {"command": "grep -n TODO scripts/notion_user_guide.py"}) is None


def test_hook_never_dies_on_bad_input():
    """⚠️ 훅이 터지면 작업이 막힌다 — 못 읽는 입력은 통과시킨다."""
    assert _run("guard_destructive", {}) == 0
    assert _run("guard_destructive", {"tool_name": "Bash"}) == 0
    assert _run("guard_destructive", {"tool_name": "Read", "tool_input": {}}) == 0

    proc = subprocess.run(
        [sys.executable, os.path.join(HOOKS, "guard_destructive.py")],
        input="not json at all", text=True, capture_output=True, cwd=REPO,
    )
    assert proc.returncode == 0


def test_blocking_exits_with_two_and_explains_why():
    """차단은 exit 2 + stderr 다 — 이유가 없으면 Claude 가 같은 일을 다시 시도한다."""
    proc = subprocess.run(
        [sys.executable, os.path.join(HOOKS, "guard_destructive.py")],
        input=json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": "git commit -am x"}}),
        text=True, encoding="utf-8", capture_output=True, cwd=REPO,
    )
    assert proc.returncode == 2
    assert "작업트리" in proc.stderr and "git add" in proc.stderr


# ── check_edit: 금지 낱말 ────────────────────────────────────────────────────

def test_forbidden_words_are_caught(tmp_path):
    """규칙의 원본은 가드 테스트다 — 훅은 그것을 편집 시점으로 앞당길 뿐이다."""
    check = _load("check_edit")

    probe = tmp_path / "briefing.py"
    probe.write_text("# claude 가 여기서 문장을 만든다\n", encoding="utf-8")
    hits = check._forbidden_words(str(probe), "app/core/briefing.py")
    assert hits and "claude" in hits[0]

    js = tmp_path / "personal-briefing.js"
    js.write_text('const a = localStorage.getItem("x");\n', encoding="utf-8")
    hits = check._forbidden_words(str(js), "app/frontend/personal-briefing.js")
    assert hits and "localStorage" in hits[0]


def test_korean_llm_mentions_are_allowed_like_the_guard_test_does():
    """`LLM 은/이` 는 한국어 문장이라 허용된다 — 가드 테스트와 같은 예외를 쓴다."""
    import inspect

    check = _load("check_edit")
    source = inspect.getsource(check._forbidden_words)
    assert 'replace("llm 은", "")' in source and 'replace("llm 이", "")' in source


def test_real_repo_files_pass_today():
    """지금 저장소가 자기 규칙을 지키고 있어야 한다 — 아니면 훅이 늘 울린다."""
    check = _load("check_edit")
    for rel in ("app/core/briefing.py", "app/frontend/personal-briefing.js"):
        path = os.path.join(REPO, rel)
        if os.path.exists(path):
            assert check._forbidden_words(path, rel) == []


# ── check_edit: ThreadPoolExecutor 함정 ──────────────────────────────────────

def test_executor_timeout_trap_is_caught(tmp_path):
    """`with ThreadPoolExecutor` + `result(timeout=)` 는 타임아웃을 무력화한다."""
    check = _load("check_edit")

    bad = tmp_path / "bad.py"
    bad.write_text(
        "import concurrent.futures\n"
        "with concurrent.futures.ThreadPoolExecutor() as pool:\n"
        "    answer = pool.submit(fn).result(timeout=8.0)\n",
        encoding="utf-8")
    assert check._executor_trap(str(bad), "app/x.py")

    good = tmp_path / "good.py"
    good.write_text(
        "pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)\n"
        "try:\n"
        "    answer = pool.submit(fn).result(timeout=8.0)\n"
        "finally:\n"
        "    pool.shutdown(wait=False)\n",
        encoding="utf-8")
    assert check._executor_trap(str(good), "app/x.py") == []


def test_describing_the_trap_is_not_falling_into_it(tmp_path):
    """⛔ 설명을 위반으로 읽지 마라 — 붙인 첫날 이 검사기가 **자기 설명문**을 물었다."""
    check = _load("check_edit")

    doc = tmp_path / "doc.py"
    doc.write_text(
        '"""이 함수는 `with ThreadPoolExecutor` + `.result(timeout=5)` 함정을 설명한다."""\n'
        "# with concurrent.futures.ThreadPoolExecutor() as pool:  <- 이렇게 쓰지 말 것\n"
        "#     pool.submit(fn).result(timeout=8.0)\n"
        "x = 1\n",
        encoding="utf-8")
    assert check._executor_trap(str(doc), "app/x.py") == [], \
        "주석·백틱 인용을 실제 코드로 읽었다"


def test_the_hook_scripts_do_not_flag_themselves():
    """검사기 자신과 그 회귀 테스트가 통과해야 한다 — 아니면 편집할 때마다 울린다."""
    check = _load("check_edit")
    for rel in ("scripts/hooks/check_edit.py", "scripts/hooks/guard_destructive.py",
                "tests/test_claude_hooks.py"):
        path = os.path.join(REPO, rel)
        assert check._executor_trap(path, rel) == [], f"자기 자신을 물었다: {rel}"


def test_check_edit_ignores_files_outside_the_repo(tmp_path):
    """저장소 밖 파일은 우리 규칙 대상이 아니다 — 스크래치패드 편집마다 울리면 안 된다."""
    outside = tmp_path / "scratch.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    assert _run("check_edit", {"tool_name": "Edit",
                               "tool_input": {"file_path": str(outside)}}) == 0


def test_check_edit_is_quiet_on_reads_and_missing_files():
    assert _run("check_edit", {"tool_name": "Read",
                               "tool_input": {"file_path": "app/main.py"}}) == 0
    assert _run("check_edit", {"tool_name": "Edit",
                               "tool_input": {"file_path": "does/not/exist.py"}}) == 0
    assert _run("check_edit", {}) == 0
