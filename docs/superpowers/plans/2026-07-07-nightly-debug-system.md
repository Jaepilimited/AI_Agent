# 야간 자동 디버깅·개선 시스템 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 22:00~07:00 사이 2시간 간격으로 서버 에러 로그와 최근 변경된 코드를 점검해, 저위험 버그는 커밋+prod 재시작까지 자동 적용하고 그 외는 리포트만 남기는 무인 스크립트를 만든다.

**Architecture:** 순수 로직(`scripts/nightly_debug_lib.py`)과 부수효과가 있는 오케스트레이션(`scripts/nightly_debug.py`)을 분리한다. 자동 적용 여부는 전부 결정적 코드(파일 데널리스트, diff 크기, AST 검사)로 판정하고, LLM(`codex exec`)은 "분석·수정 제안·adversarial 재검증"이라는 좁은 역할만 맡는다. 실제 코드 패치는 codex가 생성한 unified diff를 `git apply`로 적용해 diff-파싱을 직접 구현하지 않는다.

**Tech Stack:** Python 3.11 (표준 라이브러리 위주: `subprocess`, `ast`, `re`, `json`, `urllib`), `git` CLI, `pm2` CLI, `codex` CLI (이미 설치·검증됨: `codex-cli 0.142.5`), pytest (기존 프로젝트 컨벤션).

## Global Constraints

- 자동 적용은 다음 파일에 대해 **절대 금지** (내용과 무관하게 하드 제외): `app/agents/sql_agent.py`, `app/core/security.py`, `app/api/auth_api.py`, `app/api/admin_api.py`, `app/api/admin_group_api.py`, `app/db/mariadb.py`, 확장자 `.sql`, `prompts/sql_generator.txt`.
- 자동 적용 diff는 파일 1개만 변경, 변경 줄 수 30줄 이내, 새 함수/클래스/import 추가 없음(순수 버그 픽스만), 2차 adversarial 검증에서 명시적 SAFE 판정, `ast.parse()` 통과 — 이 5가지를 전부 만족해야 함.
- codex 호출은 항상 `codex exec -s read-only -` (읽기전용 샌드박스)로만 실행한다. 절대 `--sandbox workspace-write`나 `--dangerously-bypass-approvals-and-sandbox`를 쓰지 않는다.
- 자동 적용은 `git apply`로 실제 워킹트리에 패치를 적용한 뒤 검사하고, 검사를 통과하지 못하면 반드시 `git checkout -- <file>`로 되돌린다 (커밋 전 상태로 원복).
- 프로덕션 재시작은 항상 `pm2 restart skin1004-prod` (절대 `pm2 reload` 금지 — Windows fork 모드에서 고아 프로세스 발생 이력 있음, CLAUDE.md 참조).
- 매 실행 시작 시 `git status --porcelain`으로 uncommitted 변경이 있으면 자동 적용 전체를 스킵한다 (사람이 작업 중일 수 있음).
- 스펙 문서: `docs/superpowers/specs/2026-07-07-nightly-debug-system-design.md` (모든 세부 규칙의 근거).

---

## Task 1: 패키지 스캐폴딩 + 상태 관리

**Files:**
- Create: `scripts/__init__.py` (빈 파일 — `scripts`를 임포트 가능한 패키지로 만듦)
- Create: `scripts/nightly_debug_lib.py`
- Test: `tests/test_nightly_debug_lib.py`

**Interfaces:**
- Produces: `load_state(state_path: pathlib.Path) -> dict`, `save_state(state_path: pathlib.Path, state: dict) -> None`, `DEFAULT_STATE: dict`

- [ ] **Step 1: Write the failing test**

`tests/test_nightly_debug_lib.py` (새 파일):

```python
"""Tests for scripts/nightly_debug_lib.py — pure logic for the nightly debug system."""

import json

from scripts.nightly_debug_lib import DEFAULT_STATE, load_state, save_state


class TestState:
    def test_load_state_missing_file_returns_default(self, tmp_path):
        state_path = tmp_path / "_nightly_state.json"
        state = load_state(state_path)
        assert state == DEFAULT_STATE

    def test_save_then_load_roundtrip(self, tmp_path):
        state_path = tmp_path / "_nightly_state.json"
        original = {"last_run_at": "2026-07-08T22:00:00", "last_git_commit": "abc1234", "last_log_offset": 42}
        save_state(state_path, original)
        loaded = load_state(state_path)
        assert loaded == original

    def test_save_state_writes_valid_json(self, tmp_path):
        state_path = tmp_path / "_nightly_state.json"
        save_state(state_path, DEFAULT_STATE)
        assert json.loads(state_path.read_text(encoding="utf-8")) == DEFAULT_STATE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nightly_debug_lib.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.nightly_debug_lib'` (or `scripts` 패키지 자체가 없다는 에러)

- [ ] **Step 3: Write minimal implementation**

`scripts/__init__.py` (빈 파일):

```python
```

`scripts/nightly_debug_lib.py` (새 파일, 파일 헤더와 state 섹션만 우선 작성 — 이후 Task에서 같은 파일에 이어서 추가):

```python
"""Pure, side-effect-light logic for the nightly debug/auto-fix system.

Orchestration with real side effects (subprocess calls that mutate the repo
or restart prod) lives in scripts/nightly_debug.py, which imports from here.
See docs/superpowers/specs/2026-07-07-nightly-debug-system-design.md.
"""

import json
from pathlib import Path


# --- State ---

DEFAULT_STATE = {"last_run_at": None, "last_git_commit": None, "last_log_offset": 0}


def load_state(state_path: Path) -> dict:
    """Load the nightly-run state, or return DEFAULT_STATE if it doesn't exist yet."""
    if not state_path.exists():
        return dict(DEFAULT_STATE)
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_path: Path, state: dict) -> None:
    """Persist the nightly-run state as JSON."""
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nightly_debug_lib.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/nightly_debug_lib.py tests/test_nightly_debug_lib.py
git commit -m "feat(nightly-debug): scaffold package + state load/save"
```

---

## Task 2: 로그 에러 추출

**Files:**
- Modify: `scripts/nightly_debug_lib.py` (Task 1에서 만든 파일에 이어서 추가)
- Test: `tests/test_nightly_debug_lib.py` (이어서 추가)

**Interfaces:**
- Consumes: 없음 (독립적)
- Produces: `LogError` (dataclass: `raw: str`, `kind: str`), `extract_new_errors(log_text: str, since_offset: int) -> tuple[list[LogError], int]`

"에러"로 카운트하는 기준 (스펙 문서 그대로): (a) `"level":"error"` 또는 `"level":"critical"`을 포함한 라인, (b) `Traceback (most recent call last):`로 시작하는 블록. INFO/WARNING은 제외.

- [ ] **Step 1: Write the failing test**

`tests/test_nightly_debug_lib.py`에 추가:

```python
from scripts.nightly_debug_lib import LogError, extract_new_errors


class TestLogErrorExtraction:
    def test_no_errors_in_plain_info_logs(self):
        log_text = (
            'INFO:     127.0.0.1:51341 - "GET /health HTTP/1.1" 200 OK\n'
            '{"event": "notion_warmup_fetch_failed", "level": "warning"}\n'
        )
        errors, offset = extract_new_errors(log_text, 0)
        assert errors == []
        assert offset == len(log_text)

    def test_structured_error_line_detected(self):
        log_text = '{"event": "sql_generation_failed", "level": "error", "detail": "timeout"}\n'
        errors, _ = extract_new_errors(log_text, 0)
        assert len(errors) == 1
        assert errors[0].kind == "structured"
        assert "sql_generation_failed" in errors[0].raw

    def test_critical_level_detected(self):
        log_text = '{"event": "db_down", "level": "critical"}\n'
        errors, _ = extract_new_errors(log_text, 0)
        assert len(errors) == 1

    def test_traceback_block_captured_as_one_error(self):
        log_text = (
            "some preceding line\n"
            "Traceback (most recent call last):\n"
            '  File "app/agents/gws_agent.py", line 42, in fetch\n'
            "    result = client.get(url)\n"
            "AttributeError: 'NoneType' object has no attribute 'get'\n"
            "next unrelated line\n"
        )
        errors, _ = extract_new_errors(log_text, 0)
        assert len(errors) == 1
        assert errors[0].kind == "traceback"
        assert "AttributeError" in errors[0].raw
        assert "next unrelated line" not in errors[0].raw

    def test_since_offset_skips_already_seen_text(self):
        first = '{"event": "old_error", "level": "error"}\n'
        second = '{"event": "new_error", "level": "error"}\n'
        log_text = first + second
        errors, new_offset = extract_new_errors(log_text, len(first))
        assert len(errors) == 1
        assert "new_error" in errors[0].raw
        assert new_offset == len(log_text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nightly_debug_lib.py::TestLogErrorExtraction -v`
Expected: FAIL with `ImportError: cannot import name 'LogError'`

- [ ] **Step 3: Write minimal implementation**

`scripts/nightly_debug_lib.py`에 추가 (state 섹션 아래):

```python
import re
from dataclasses import dataclass


# --- Log error extraction ---

@dataclass
class LogError:
    raw: str
    kind: str  # "structured" | "traceback"


_ERROR_LEVEL_RE = re.compile(r'"level"\s*:\s*"(error|critical)"')
_TRACEBACK_START = "Traceback (most recent call last):"


def extract_new_errors(log_text: str, since_offset: int) -> "tuple[list, int]":
    """Return (errors, new_offset). Offsets are character positions into log_text."""
    new_text = log_text[since_offset:]
    errors: list = []
    lines = new_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _TRACEBACK_START in line:
            block = [line]
            i += 1
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                block.append(lines[i])
                i += 1
            if i < len(lines):
                block.append(lines[i])  # exception summary line, e.g. "AttributeError: ..."
                i += 1
            errors.append(LogError(raw="\n".join(block), kind="traceback"))
            continue
        if _ERROR_LEVEL_RE.search(line):
            errors.append(LogError(raw=line.strip(), kind="structured"))
        i += 1
    return errors, len(log_text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nightly_debug_lib.py -v`
Expected: PASS (8 passed — 3 from Task 1 + 5 new)

- [ ] **Step 5: Commit**

```bash
git add scripts/nightly_debug_lib.py tests/test_nightly_debug_lib.py
git commit -m "feat(nightly-debug): extract structured errors and tracebacks from logs"
```

---

## Task 3: Git 헬퍼 함수

**Files:**
- Modify: `scripts/nightly_debug_lib.py`
- Test: `tests/test_nightly_debug_lib.py`

**Interfaces:**
- Produces: `get_current_commit(repo_dir) -> str`, `has_uncommitted_changes(repo_dir) -> bool`, `get_changed_files(repo_dir, since_commit) -> list[str]`, `get_file_diff(repo_dir, since_commit, file_path) -> str`, `diff_check_applies(repo_dir, diff_text) -> bool`, `apply_diff_to_worktree(repo_dir, diff_text) -> bool`, `discard_worktree_changes(repo_dir, file_path) -> None`, `commit_worktree_changes(repo_dir, file_path, commit_message) -> bool`, `revert_last_commit(repo_dir) -> bool`

이 함수들은 실제 `git` 서브프로세스를 부르므로, mock이 아니라 **진짜 임시 git 저장소**(`tmp_path` + `git init`)로 테스트한다 — git 동작의 미묘한 차이(개행, exit code)를 mock으로 흉내내면 오히려 거짓 안심을 주기 때문.

- [ ] **Step 1: Write the failing test**

`tests/test_nightly_debug_lib.py`에 추가:

```python
import subprocess

from scripts.nightly_debug_lib import (
    apply_diff_to_worktree,
    commit_worktree_changes,
    diff_check_applies,
    discard_worktree_changes,
    get_changed_files,
    get_current_commit,
    get_file_diff,
    has_uncommitted_changes,
    revert_last_commit,
)


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "sample.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "sample.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    return repo


class TestGitHelpers:
    def test_get_current_commit_returns_sha(self, tmp_path):
        repo = _init_repo(tmp_path)
        sha = get_current_commit(repo)
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_has_uncommitted_changes_false_on_clean_repo(self, tmp_path):
        repo = _init_repo(tmp_path)
        assert has_uncommitted_changes(repo) is False

    def test_has_uncommitted_changes_true_after_edit(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "sample.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
        assert has_uncommitted_changes(repo) is True

    def test_get_changed_files_detects_modified_file(self, tmp_path):
        repo = _init_repo(tmp_path)
        first_commit = get_current_commit(repo)
        (repo / "sample.py").write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "sample.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=repo, check=True)
        changed = get_changed_files(repo, first_commit)
        assert changed == ["sample.py"]

    def test_get_changed_files_empty_when_no_since_commit(self, tmp_path):
        repo = _init_repo(tmp_path)
        assert get_changed_files(repo, None) == []

    def test_apply_diff_check_and_apply_roundtrip(self, tmp_path):
        repo = _init_repo(tmp_path)
        diff_text = (
            "diff --git a/sample.py b/sample.py\n"
            "--- a/sample.py\n"
            "+++ b/sample.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n"
            "-    return a + b\n"
            "+    return a + b  # fixed\n"
        )
        assert diff_check_applies(repo, diff_text) is True
        assert apply_diff_to_worktree(repo, diff_text) is True
        assert "# fixed" in (repo / "sample.py").read_text(encoding="utf-8")

    def test_discard_worktree_changes_restores_original(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "sample.py").write_text("garbage", encoding="utf-8")
        discard_worktree_changes(repo, "sample.py")
        assert (repo / "sample.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"

    def test_commit_worktree_changes_creates_commit(self, tmp_path):
        repo = _init_repo(tmp_path)
        before = get_current_commit(repo)
        (repo / "sample.py").write_text("def add(a, b):\n    return a + b  # fixed\n", encoding="utf-8")
        assert commit_worktree_changes(repo, "sample.py", "[nightly-auto-fix] fix add") is True
        assert get_current_commit(repo) != before

    def test_revert_last_commit_undoes_change(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "sample.py").write_text("def add(a, b):\n    return a + b  # fixed\n", encoding="utf-8")
        commit_worktree_changes(repo, "sample.py", "[nightly-auto-fix] fix add")
        assert revert_last_commit(repo) is True
        assert (repo / "sample.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"

    def test_get_file_diff_returns_diff_for_one_file(self, tmp_path):
        repo = _init_repo(tmp_path)
        first_commit = get_current_commit(repo)
        (repo / "sample.py").write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "sample.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=repo, check=True)
        diff = get_file_diff(repo, first_commit, "sample.py")
        assert "sample.py" in diff
        assert "+1" in diff or "+    return a + b + 1" in diff
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nightly_debug_lib.py::TestGitHelpers -v`
Expected: FAIL with `ImportError: cannot import name 'get_current_commit'`

- [ ] **Step 3: Write minimal implementation**

`scripts/nightly_debug_lib.py`에 추가:

```python
import subprocess
from typing import Optional


# --- Git helpers ---

def get_current_commit(repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


def has_uncommitted_changes(repo_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo_dir),
        capture_output=True, text=True, timeout=30,
    )
    return bool(result.stdout.strip())


def get_changed_files(repo_dir: Path, since_commit: Optional[str]) -> list:
    """Files changed between since_commit and HEAD. Empty list if since_commit is falsy (first run)."""
    if not since_commit:
        return []
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{since_commit}..HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_file_diff(repo_dir: Path, since_commit: str, file_path: str) -> str:
    result = subprocess.run(
        ["git", "diff", f"{since_commit}..HEAD", "--", file_path], cwd=str(repo_dir),
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout


def diff_check_applies(repo_dir: Path, diff_text: str) -> bool:
    """Dry-run check: would this unified diff apply cleanly to the current worktree?"""
    result = subprocess.run(
        ["git", "apply", "--check", "-"], input=diff_text, cwd=str(repo_dir),
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


def apply_diff_to_worktree(repo_dir: Path, diff_text: str) -> bool:
    """Apply a unified diff to the working tree (NOT committed yet)."""
    result = subprocess.run(
        ["git", "apply", "-"], input=diff_text, cwd=str(repo_dir),
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


def discard_worktree_changes(repo_dir: Path, file_path: str) -> None:
    """Discard uncommitted changes to a single file (restore to last commit)."""
    subprocess.run(
        ["git", "checkout", "--", file_path], cwd=str(repo_dir),
        capture_output=True, text=True, timeout=30,
    )


def commit_worktree_changes(repo_dir: Path, file_path: str, commit_message: str) -> bool:
    add = subprocess.run(
        ["git", "add", file_path], cwd=str(repo_dir),
        capture_output=True, text=True, timeout=30,
    )
    if add.returncode != 0:
        return False
    commit = subprocess.run(
        ["git", "commit", "-m", commit_message], cwd=str(repo_dir),
        capture_output=True, text=True, timeout=30,
    )
    return commit.returncode == 0


def revert_last_commit(repo_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "revert", "--no-edit", "HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, timeout=60,
    )
    return result.returncode == 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nightly_debug_lib.py -v`
Expected: PASS (18 passed — 8 from before + 10 new)

- [ ] **Step 5: Commit**

```bash
git add scripts/nightly_debug_lib.py tests/test_nightly_debug_lib.py
git commit -m "feat(nightly-debug): git helpers for diff collection, apply, commit, revert"
```

---

## Task 4: codex CLI 호출 + 출력 파싱

**Files:**
- Modify: `scripts/nightly_debug_lib.py`
- Test: `tests/test_nightly_debug_lib.py`

**Interfaces:**
- Consumes: 없음 (독립적, `subprocess.run`만 사용)
- Produces: `run_codex(prompt: str, cwd: Path, timeout: int = 300) -> Optional[str]`, `parse_codex_output(stdout: str) -> Optional[str]`, `extract_diff_block(proposal_text: str) -> Optional[str]`

`parse_codex_output`은 오늘 세션에서 실제로 검증한 패턴을 그대로 코드화한다: `codex exec` stdout에서 **마지막**으로 단독으로 등장하는 `codex` 줄부터, 그다음에 나오는 `tokens used` 줄 직전까지가 최종 답변이다.

- [ ] **Step 1: Write the failing test**

`tests/test_nightly_debug_lib.py`에 추가:

```python
from unittest import mock

from scripts.nightly_debug_lib import extract_diff_block, parse_codex_output, run_codex


class TestCodexOutputParsing:
    def test_parse_codex_output_extracts_final_answer(self):
        stdout = (
            "OpenAI Codex v0.142.5\n"
            "--------\n"
            "user\n"
            "some prompt\n"
            "codex\n"
            "이건 중간 사고 과정입니다\n"
            "codex\n"
            "## Summary\n"
            "최종 답변입니다.\n"
            "tokens used\n"
            "12,345\n"
        )
        answer = parse_codex_output(stdout)
        assert answer == "## Summary\n최종 답변입니다."

    def test_parse_codex_output_no_marker_returns_none(self):
        assert parse_codex_output("no codex marker here\ntokens used\n123\n") is None

    def test_parse_codex_output_no_tokens_line_takes_rest(self):
        stdout = "codex\nfinal answer without tokens footer\n"
        assert parse_codex_output(stdout) == "final answer without tokens footer"


class TestDiffBlockExtraction:
    def test_extract_single_diff_block(self):
        proposal = (
            "원인 설명입니다.\n\n"
            "```diff\n"
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "```\n\n"
            "부작용 없음.\n"
        )
        diff = extract_diff_block(proposal)
        assert diff is not None
        assert "diff --git a/x.py b/x.py" in diff
        assert "부작용" not in diff

    def test_extract_diff_block_none_when_absent(self):
        assert extract_diff_block("그냥 설명 텍스트, diff 없음") is None


class TestRunCodex:
    def test_run_codex_returns_parsed_answer_on_success(self, tmp_path):
        fake_stdout = "codex\nanswer text\ntokens used\n100\n"
        with mock.patch("scripts.nightly_debug_lib.subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0, stdout=fake_stdout)
            result = run_codex("some prompt", cwd=tmp_path)
        assert result == "answer text"
        args, kwargs = mock_run.call_args
        assert args[0][:4] == ["codex", "exec", "-s", "read-only"]

    def test_run_codex_returns_none_on_nonzero_exit(self, tmp_path):
        with mock.patch("scripts.nightly_debug_lib.subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=1, stdout="")
            assert run_codex("prompt", cwd=tmp_path) is None

    def test_run_codex_returns_none_on_timeout(self, tmp_path):
        with mock.patch("scripts.nightly_debug_lib.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="codex", timeout=300)
            assert run_codex("prompt", cwd=tmp_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nightly_debug_lib.py::TestCodexOutputParsing tests/test_nightly_debug_lib.py::TestDiffBlockExtraction tests/test_nightly_debug_lib.py::TestRunCodex -v`
Expected: FAIL with `ImportError: cannot import name 'run_codex'`

- [ ] **Step 3: Write minimal implementation**

`scripts/nightly_debug_lib.py`에 추가:

```python
# --- codex CLI wrapper ---

_TOKENS_USED_MARKER = "tokens used"
_DIFF_BLOCK_RE = re.compile(r"```diff\n(.*?)```", re.DOTALL)


def run_codex(prompt: str, cwd: Path, timeout: int = 300) -> Optional[str]:
    """Run `codex exec -s read-only -` with prompt piped via stdin.

    Returns the parsed final answer, or None on failure/timeout/non-zero exit.
    Always read-only — never pass a write-capable sandbox mode here.
    """
    try:
        result = subprocess.run(
            ["codex", "exec", "-s", "read-only", "-"],
            input=prompt, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return parse_codex_output(result.stdout)


def parse_codex_output(stdout: str) -> Optional[str]:
    """Extract the final answer: text between the LAST standalone 'codex' marker
    line and the following 'tokens used' line (codex exec's own transcript format).
    """
    lines = stdout.splitlines()
    last_marker_idx = None
    for idx, line in enumerate(lines):
        if line.strip() == "codex":
            last_marker_idx = idx
    if last_marker_idx is None:
        return None
    end_idx = len(lines)
    for idx in range(last_marker_idx + 1, len(lines)):
        if lines[idx].strip() == _TOKENS_USED_MARKER:
            end_idx = idx
            break
    answer = "\n".join(lines[last_marker_idx + 1:end_idx]).strip()
    return answer or None


def extract_diff_block(proposal_text: str) -> Optional[str]:
    """Extract and concatenate all fenced ```diff blocks from a codex proposal."""
    blocks = _DIFF_BLOCK_RE.findall(proposal_text)
    if not blocks:
        return None
    return "\n".join(block.rstrip("\n") for block in blocks) + "\n"
```

파일 맨 위 import 섹션에 `subprocess` import가 Task 3에서 이미 추가됐으므로 중복 추가하지 않는다 (한 파일이니 상단에 한 번만).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nightly_debug_lib.py -v`
Expected: PASS (26 passed — 18 from before + 8 new)

- [ ] **Step 5: Commit**

```bash
git add scripts/nightly_debug_lib.py tests/test_nightly_debug_lib.py
git commit -m "feat(nightly-debug): codex CLI wrapper with output/diff parsing"
```

---

## Task 5: 리스크 게이트

**Files:**
- Modify: `scripts/nightly_debug_lib.py`
- Test: `tests/test_nightly_debug_lib.py`

**Interfaces:**
- Consumes: 없음 (순수 함수)
- Produces: `RiskVerdict` (dataclass: `auto_apply_eligible: bool`, `reasons: list[str]`), `DENYLIST_FILES: list[str]`, `DENYLIST_SUFFIXES: list[str]`, `MAX_CHANGED_LINES: int`, `is_denylisted(file_path: str) -> bool`, `count_changed_lines(diff_text: str) -> int`, `diff_touches_single_file(diff_text: str) -> bool`, `extract_diff_target_file(diff_text: str) -> Optional[str]`, `pre_check(diff_text: str) -> RiskVerdict`, `post_apply_check(original_source: str, patched_source: str) -> RiskVerdict`, `verification_passed(verification_text: str) -> bool`

**중요**: `pre_check`은 파일 경로를 별도 인자로 받지 않고 **diff 텍스트 자체(`diff --git a/<path> b/<path>` 헤더)에서 대상 파일을 추출**해 데널리스트를 검사한다. 로그 에러처럼 애초에 "어느 파일"인지 모르고 시작하는 이슈(파일 라벨이 `(server log)` 같은 placeholder)에서, codex가 제안한 diff가 실제로는 `sql_agent.py`를 건드리는 경우를 caller가 잘못된 파일명을 넘겨서 놓치는 것을 막기 위함이다 — 항상 diff의 실제 내용을 신뢰의 근거로 삼는다. `pre_check`은 diff를 실제로 적용하기 **전에** 판단 가능한 것(diff에서 뽑은 파일 경로, diff 크기)만 본다. `post_apply_check`은 `git apply`로 실제 적용한 **후** 파일 내용을 읽어서 syntax·새 정의 여부를 확인한다 (Task 8의 오케스트레이터가 두 단계를 순서대로 호출).

- [ ] **Step 1: Write the failing test**

`tests/test_nightly_debug_lib.py`에 추가:

```python
from scripts.nightly_debug_lib import (
    is_denylisted,
    count_changed_lines,
    diff_touches_single_file,
    extract_diff_target_file,
    pre_check,
    post_apply_check,
    verification_passed,
)


class TestRiskGate:
    def test_is_denylisted_exact_match(self):
        assert is_denylisted("app/agents/sql_agent.py") is True
        assert is_denylisted("app/core/security.py") is True

    def test_is_denylisted_sql_suffix(self):
        assert is_denylisted("migrations/2026_add_column.sql") is True

    def test_is_denylisted_false_for_normal_file(self):
        assert is_denylisted("app/agents/gws_agent.py") is False

    def test_is_denylisted_normalizes_backslashes(self):
        assert is_denylisted("app\\agents\\sql_agent.py") is True

    def test_count_changed_lines_excludes_headers(self):
        diff_text = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,2 +1,2 @@\n"
            " unchanged\n"
            "-old line\n"
            "+new line\n"
        )
        assert count_changed_lines(diff_text) == 2

    def test_diff_touches_single_file_true(self):
        diff_text = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        assert diff_touches_single_file(diff_text) is True

    def test_diff_touches_single_file_false_for_multiple(self):
        diff_text = (
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            "diff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n"
        )
        assert diff_touches_single_file(diff_text) is False

    def test_extract_diff_target_file_returns_path(self):
        diff_text = "diff --git a/app/agents/gws_agent.py b/app/agents/gws_agent.py\n--- a/app/agents/gws_agent.py\n+++ b/app/agents/gws_agent.py\n"
        assert extract_diff_target_file(diff_text) == "app/agents/gws_agent.py"

    def test_extract_diff_target_file_none_when_malformed(self):
        assert extract_diff_target_file("no diff header here") is None

    def test_pre_check_passes_for_small_single_file_diff(self):
        diff_text = (
            "diff --git a/app/agents/gws_agent.py b/app/agents/gws_agent.py\n"
            "--- a/app/agents/gws_agent.py\n+++ b/app/agents/gws_agent.py\n"
            "@@ -1,1 +1,1 @@\n-old\n+new\n"
        )
        verdict = pre_check(diff_text)
        assert verdict.auto_apply_eligible is True
        assert verdict.reasons == []

    def test_pre_check_fails_for_denylisted_file(self):
        diff_text = "diff --git a/app/agents/sql_agent.py b/app/agents/sql_agent.py\n--- a/app/agents/sql_agent.py\n+++ b/app/agents/sql_agent.py\n"
        verdict = pre_check(diff_text)
        assert verdict.auto_apply_eligible is False
        assert any("denylist" in r for r in verdict.reasons)

    def test_pre_check_fails_when_target_file_undeterminable(self):
        verdict = pre_check("no diff header, just prose explanation")
        assert verdict.auto_apply_eligible is False
        assert any("could not determine target file" in r for r in verdict.reasons)

    def test_pre_check_fails_for_oversized_diff(self):
        big_body = "".join(f"-line{i}\n+line{i}fixed\n" for i in range(20))
        diff_text = (
            "diff --git a/app/agents/gws_agent.py b/app/agents/gws_agent.py\n"
            "--- a/app/agents/gws_agent.py\n+++ b/app/agents/gws_agent.py\n"
            "@@ -1,20 +1,20 @@\n" + big_body
        )
        verdict = pre_check(diff_text)
        assert verdict.auto_apply_eligible is False
        assert any("too large" in r for r in verdict.reasons)

    def test_pre_check_uses_diff_derived_file_not_caller_label(self):
        """Regression guard: a log-derived issue has no real file until the diff
        reveals one — pre_check must never trust a caller-supplied placeholder
        label like '(server log)' for the denylist check; it must read the
        actual target file out of the diff itself."""
        diff_text = "diff --git a/app/agents/sql_agent.py b/app/agents/sql_agent.py\n--- a/app/agents/sql_agent.py\n+++ b/app/agents/sql_agent.py\n@@ -1 +1 @@\n-a\n+b\n"
        verdict = pre_check(diff_text)
        assert verdict.auto_apply_eligible is False
        assert any("sql_agent.py" in r for r in verdict.reasons)

    def test_post_apply_check_passes_for_pure_bugfix(self):
        original = "def add(a, b):\n    return a - b\n"
        patched = "def add(a, b):\n    return a + b\n"
        verdict = post_apply_check(original, patched)
        assert verdict.auto_apply_eligible is True

    def test_post_apply_check_fails_for_new_function(self):
        original = "def add(a, b):\n    return a + b\n"
        patched = "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
        verdict = post_apply_check(original, patched)
        assert verdict.auto_apply_eligible is False
        assert any("new function/class/import" in r for r in verdict.reasons)

    def test_post_apply_check_fails_for_invalid_syntax(self):
        original = "def add(a, b):\n    return a + b\n"
        patched = "def add(a, b)\n    return a + b\n"  # missing colon
        verdict = post_apply_check(original, patched)
        assert verdict.auto_apply_eligible is False
        assert any("not valid Python" in r for r in verdict.reasons)

    def test_verification_passed_true_for_safe(self):
        assert verification_passed("판정: SAFE. 근거: ...") is True

    def test_verification_passed_false_for_risk(self):
        assert verification_passed("판정: STILL RISK. 근거: ...") is False

    def test_verification_passed_false_when_ambiguous(self):
        assert verification_passed("특별한 판정 없이 설명만 함") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nightly_debug_lib.py::TestRiskGate -v`
Expected: FAIL with `ImportError: cannot import name 'is_denylisted'`

- [ ] **Step 3: Write minimal implementation**

`scripts/nightly_debug_lib.py`에 추가:

```python
import ast
from dataclasses import field


# --- Risk gate ---

DENYLIST_FILES = [
    "app/agents/sql_agent.py",
    "app/core/security.py",
    "app/api/auth_api.py",
    "app/api/admin_api.py",
    "app/api/admin_group_api.py",
    "app/db/mariadb.py",
    "prompts/sql_generator.txt",
]
DENYLIST_SUFFIXES = [".sql"]
MAX_CHANGED_LINES = 30


@dataclass
class RiskVerdict:
    auto_apply_eligible: bool
    reasons: list = field(default_factory=list)


def is_denylisted(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/")
    if normalized in DENYLIST_FILES:
        return True
    return any(normalized.endswith(suf) for suf in DENYLIST_SUFFIXES)


def count_changed_lines(diff_text: str) -> int:
    """Count added+removed lines in a unified diff, excluding +++/--- headers."""
    count = 0
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            count += 1
    return count


def diff_touches_single_file(diff_text: str) -> bool:
    files = set(re.findall(r"^diff --git a/(\S+) b/\S+", diff_text, re.MULTILINE))
    return len(files) == 1


def extract_diff_target_file(diff_text: str) -> Optional[str]:
    """Return the first file path named in a unified diff's 'diff --git a/<path> b/<path>' header."""
    match = re.search(r"^diff --git a/(\S+) b/\S+", diff_text, re.MULTILINE)
    return match.group(1) if match else None


def pre_check(diff_text: str) -> RiskVerdict:
    """Checks possible before actually applying the diff: target file (read from
    the diff itself, never from a caller-supplied label) + diff size.
    """
    reasons = []
    target_file = extract_diff_target_file(diff_text)
    if target_file is None:
        reasons.append("could not determine target file from diff")
    elif is_denylisted(target_file):
        reasons.append(f"denylisted file: {target_file}")
    if not diff_touches_single_file(diff_text):
        reasons.append("diff touches more than one file")
    changed = count_changed_lines(diff_text)
    if changed > MAX_CHANGED_LINES:
        reasons.append(f"diff too large: {changed} lines > {MAX_CHANGED_LINES}")
    return RiskVerdict(auto_apply_eligible=(len(reasons) == 0), reasons=reasons)


def _top_level_names(source: str) -> "tuple[set, set, set]":
    tree = ast.parse(source)
    funcs, classes, imports = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.add(node.name)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    return funcs, classes, imports


def post_apply_check(original_source: str, patched_source: str) -> RiskVerdict:
    """Checks possible only after applying the diff: syntax validity + no new definitions."""
    reasons = []
    try:
        ast.parse(patched_source)
    except SyntaxError:
        reasons.append("patched source is not valid Python (ast.parse failed)")
        return RiskVerdict(auto_apply_eligible=False, reasons=reasons)

    orig_funcs, orig_classes, orig_imports = _top_level_names(original_source)
    new_funcs, new_classes, new_imports = _top_level_names(patched_source)
    if (new_funcs - orig_funcs) or (new_classes - orig_classes) or (new_imports - orig_imports):
        reasons.append("diff adds new function/class/import — not a pure bug fix")

    return RiskVerdict(auto_apply_eligible=(len(reasons) == 0), reasons=reasons)


_SAFE_MARKERS = ("SAFE", "RESOLVED")
_RISK_MARKERS = ("RISK", "INCOMPLETE", "FAIL")


def verification_passed(verification_text: str) -> bool:
    """Parse codex's adversarial-verification output for a pass/fail signal.

    Fail-closed: any risk marker present -> False. Requires an explicit safe
    marker to return True (silence/ambiguity is not consent).
    """
    upper = verification_text.upper()
    if any(marker in upper for marker in _RISK_MARKERS):
        return False
    return any(marker in upper for marker in _SAFE_MARKERS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nightly_debug_lib.py -v`
Expected: PASS (46 passed — 26 from before + 20 new)

- [ ] **Step 5: Commit**

```bash
git add scripts/nightly_debug_lib.py tests/test_nightly_debug_lib.py
git commit -m "feat(nightly-debug): deterministic risk gate (denylist, diff size, AST checks)"
```

---

## Task 6: PM2 재시작 + 헬스체크

**Files:**
- Modify: `scripts/nightly_debug_lib.py`
- Test: `tests/test_nightly_debug_lib.py`

**Interfaces:**
- Produces: `restart_prod() -> bool`, `check_health(url: str = "http://127.0.0.1:3000/health", timeout: float = 10.0) -> bool`

- [ ] **Step 1: Write the failing test**

`tests/test_nightly_debug_lib.py`에 추가:

```python
from scripts.nightly_debug_lib import check_health, restart_prod


class TestPm2Health:
    def test_restart_prod_calls_pm2_restart(self):
        with mock.patch("scripts.nightly_debug_lib.subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            assert restart_prod() is True
            args, kwargs = mock_run.call_args
            assert args[0] == ["pm2", "restart", "skin1004-prod"]

    def test_restart_prod_returns_false_on_failure(self):
        with mock.patch("scripts.nightly_debug_lib.subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=1)
            assert restart_prod() is False

    def test_check_health_true_on_200(self):
        fake_response = mock.MagicMock()
        fake_response.status = 200
        fake_response.__enter__ = mock.Mock(return_value=fake_response)
        fake_response.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=fake_response):
            assert check_health() is True

    def test_check_health_false_on_connection_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            assert check_health() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nightly_debug_lib.py::TestPm2Health -v`
Expected: FAIL with `ImportError: cannot import name 'restart_prod'`

- [ ] **Step 3: Write minimal implementation**

`scripts/nightly_debug_lib.py`에 추가:

```python
import urllib.error
import urllib.request


# --- PM2 restart + health check ---

def restart_prod() -> bool:
    result = subprocess.run(
        ["pm2", "restart", "skin1004-prod"],
        capture_output=True, text=True, timeout=60,
    )
    return result.returncode == 0


def check_health(url: str = "http://127.0.0.1:3000/health", timeout: float = 10.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nightly_debug_lib.py -v`
Expected: PASS (50 passed — 46 from before + 4 new)

- [ ] **Step 5: Commit**

```bash
git add scripts/nightly_debug_lib.py tests/test_nightly_debug_lib.py
git commit -m "feat(nightly-debug): pm2 restart + health check helpers"
```

---

## Task 7: 프롬프트 빌더 + 요약 + 리포트 생성

**Files:**
- Modify: `scripts/nightly_debug_lib.py`
- Test: `tests/test_nightly_debug_lib.py`

**Interfaces:**
- Produces: `build_log_error_prompt(error: LogError) -> str`, `build_diff_review_prompt(file_path: str, diff_text: str) -> str`, `build_verification_prompt(proposal: str) -> str`, `summarize(proposal: str, max_len: int = 80) -> str`, `build_report(date_label: str, applied: list, reported: list) -> Optional[str]`

`summarize`는 codex 제안 텍스트의 첫 non-empty 줄을 커밋 메시지/리포트 요약으로 쓴다 (LLM을 또 호출하지 않고 결정적으로 뽑아냄).

- [ ] **Step 1: Write the failing test**

`tests/test_nightly_debug_lib.py`에 추가:

```python
from scripts.nightly_debug_lib import (
    build_diff_review_prompt,
    build_log_error_prompt,
    build_report,
    build_verification_prompt,
    summarize,
)


class TestPromptsAndSummary:
    def test_build_log_error_prompt_includes_raw_error(self):
        error = LogError(raw="AttributeError: boom", kind="traceback")
        prompt = build_log_error_prompt(error)
        assert "AttributeError: boom" in prompt
        assert "읽기전용" in prompt or "read-only" in prompt.lower()

    def test_build_diff_review_prompt_includes_file_and_diff(self):
        prompt = build_diff_review_prompt("app/agents/gws_agent.py", "diff --git a/x b/x\n")
        assert "app/agents/gws_agent.py" in prompt
        assert "diff --git a/x b/x" in prompt

    def test_build_verification_prompt_includes_proposal(self):
        prompt = build_verification_prompt("## Summary\n원인은 이렇습니다")
        assert "원인은 이렇습니다" in prompt
        assert "SAFE" in prompt

    def test_summarize_returns_first_nonempty_line(self):
        proposal = "\n\n## Summary\n실제 원인은 NoneType 접근입니다.\n\n자세한 설명..."
        assert summarize(proposal) == "## Summary"

    def test_summarize_truncates_long_line(self):
        long_line = "가" * 200
        assert len(summarize(long_line, max_len=80)) <= 80


class TestReportBuilder:
    def test_build_report_none_when_nothing_to_report(self):
        assert build_report("2026-07-08 22:00", [], []) is None

    def test_build_report_includes_applied_and_reported_sections(self):
        applied = [{"file": "app/agents/gws_agent.py", "summary": "NoneType 수정", "commit": "abc1234", "health_status": "헬스체크 통과"}]
        reported = [{"file": "app/agents/sql_agent.py", "summary": "가드 미흡", "cause": "enabled_sources 미검증", "exclusion_reason": "denylisted file"}]
        report = build_report("2026-07-08 22:00", applied, reported)
        assert "자동적용됨 (1건)" in report
        assert "abc1234" in report
        assert "보고만 (1건)" in report
        assert "denylisted file" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nightly_debug_lib.py::TestPromptsAndSummary tests/test_nightly_debug_lib.py::TestReportBuilder -v`
Expected: FAIL with `ImportError: cannot import name 'build_log_error_prompt'`

- [ ] **Step 3: Write minimal implementation**

`scripts/nightly_debug_lib.py`에 추가:

```python
# --- Prompt builders ---

def build_log_error_prompt(error: LogError) -> str:
    return f"""다음은 프로덕션 서버 로그에서 발견된 에러입니다. 읽기전용으로 분석하세요. 파일을 수정하지 마세요.

```
{error.raw}
```

이 에러의 원인이 되는 코드를 찾아 설명하고, 수정이 필요하면 unified diff(```diff 코드블록)로 제안하세요.
확신이 없으면 diff 없이 원인 설명만 하세요."""


def build_diff_review_prompt(file_path: str, diff_text: str) -> str:
    return f"""다음은 최근 변경된 파일 `{file_path}`의 diff입니다. 읽기전용으로 리뷰하세요. 파일을 수정하지 마세요.

```diff
{diff_text}
```

이 변경에 버그가 있는지 리뷰하고, 있다면 수정을 unified diff(```diff 코드블록)로 제안하세요.
버그가 없으면 diff 없이 "이상 없음"이라고만 답하세요."""


def build_verification_prompt(proposal: str) -> str:
    return f"""다음 수정 제안을 adversarial하게 재검증하세요. 실제 코드와 대조하고, 제안자의 주장을 그대로 믿지 마세요.

{proposal}

판정을 반드시 SAFE 또는 RISK 중 하나로 명시하고 근거를 설명하세요."""


def summarize(proposal: str, max_len: int = 80) -> str:
    for line in proposal.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:max_len]
    return ""


# --- Report builder ---

def build_report(date_label: str, applied: list, reported: list) -> Optional[str]:
    """Return markdown report text, or None if there's nothing to report at all."""
    if not applied and not reported:
        return None

    lines = [f"# 야간 점검 리포트 — {date_label}", ""]

    if applied:
        lines.append(f"## 자동적용됨 ({len(applied)}건)")
        for item in applied:
            lines.append(f"- [{item['file']}] {item['summary']} — 커밋 {item['commit']}, {item['health_status']}")
        lines.append("")

    if reported:
        lines.append(f"## 보고만 ({len(reported)}건)")
        for item in reported:
            lines.append(f"- [{item['file']}] {item['summary']}")
            lines.append(f"  원인: {item['cause']}")
            lines.append(f"  자동적용 제외 사유: {item['exclusion_reason']}")
        lines.append("")

    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nightly_debug_lib.py -v`
Expected: PASS (57 passed — 50 from before + 7 new)

- [ ] **Step 5: Commit**

```bash
git add scripts/nightly_debug_lib.py tests/test_nightly_debug_lib.py
git commit -m "feat(nightly-debug): prompt builders, summarize, and markdown report builder"
```

---

## Task 8: 메인 오케스트레이터 스크립트

**Files:**
- Create: `scripts/nightly_debug.py`
- Test: `tests/test_nightly_debug_orchestration.py`

**Interfaces:**
- Consumes: 모든 Task 1~7의 `scripts/nightly_debug_lib` 함수들
- Produces: `process_issue(repo_dir, file_path, context_diff, prompt, dry_run) -> dict`, `collect_log_issues(repo_dir, log_path, state) -> list`, `collect_diff_issues(repo_dir, state) -> list`, `main(argv=None) -> int`

이 파일은 부수효과(파일 쓰기, git/pm2 호출, 실제 codex 호출)를 갖는 오케스트레이션 레이어라 대부분의 테스트는 `nightly_debug_lib`의 각 함수를 mock으로 대체해 **흐름**(어떤 조건에서 자동적용 vs 보고만으로 가는지)만 검증한다.

- [ ] **Step 1: Write the failing test**

`tests/test_nightly_debug_orchestration.py` (새 파일):

```python
"""Tests for scripts/nightly_debug.py orchestration — flow control only.

All I/O (codex, git, pm2, health) is mocked; scripts/test_nightly_debug_lib.py
already covers the real logic of each mocked function.
"""

from unittest import mock

from scripts.nightly_debug import process_issue


def _patch_lib(**overrides):
    """Patch scripts.nightly_debug's imported names with the given return values/side effects."""
    patches = []
    for name, value in overrides.items():
        p = mock.patch(f"scripts.nightly_debug.{name}", value)
        patches.append(p)
    return patches


class TestProcessIssue:
    def test_dry_run_never_applies_even_if_gate_passes(self, tmp_path):
        with mock.patch("scripts.nightly_debug.run_codex", side_effect=["## Summary\nfix\n```diff\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\n"]), \
             mock.patch("scripts.nightly_debug.pre_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.commit_worktree_changes") as mock_commit:
            result = process_issue(tmp_path, "x.py", "some context", "some prompt", dry_run=True)
        assert result["applied"] is False
        mock_commit.assert_not_called()

    def test_codex_failure_reports_only(self, tmp_path):
        with mock.patch("scripts.nightly_debug.run_codex", return_value=None):
            result = process_issue(tmp_path, "x.py", "context", "prompt", dry_run=False)
        assert result["applied"] is False
        assert "실패" in result["exclusion_reason"]

    def test_no_diff_in_proposal_reports_only(self, tmp_path):
        with mock.patch("scripts.nightly_debug.run_codex", return_value="이상 없음, 수정 필요 없음"):
            result = process_issue(tmp_path, "x.py", "context", "prompt", dry_run=False)
        assert result["applied"] is False

    def test_pre_check_failure_skips_verification_and_reports_only(self, tmp_path):
        proposal = "## Summary\nfix\n```diff\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\n"
        with mock.patch("scripts.nightly_debug.run_codex", return_value=proposal), \
             mock.patch("scripts.nightly_debug.pre_check", return_value=mock.MagicMock(auto_apply_eligible=False, reasons=["denylisted file: x.py"])) as mock_pre, \
             mock.patch("scripts.nightly_debug.diff_check_applies") as mock_check:
            result = process_issue(tmp_path, "x.py", "context", "prompt", dry_run=False)
        assert result["applied"] is False
        assert "denylisted file" in result["exclusion_reason"]
        mock_check.assert_not_called()  # never even tries to apply a denylisted diff

    def test_failed_verification_discards_worktree_change(self, tmp_path):
        proposal = "## Summary\nfix\n```diff\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\n"
        with mock.patch("scripts.nightly_debug.run_codex", side_effect=[proposal, "판정: RISK"]), \
             mock.patch("scripts.nightly_debug.pre_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.diff_check_applies", return_value=True), \
             mock.patch("scripts.nightly_debug.apply_diff_to_worktree", return_value=True), \
             mock.patch("scripts.nightly_debug.Path.read_text", return_value="def x():\n    return 1\n"), \
             mock.patch("scripts.nightly_debug.post_apply_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.verification_passed", return_value=False), \
             mock.patch("scripts.nightly_debug.discard_worktree_changes") as mock_discard, \
             mock.patch("scripts.nightly_debug.commit_worktree_changes") as mock_commit:
            result = process_issue(tmp_path, "x.py", "context", "prompt", dry_run=False)
        assert result["applied"] is False
        mock_discard.assert_called_once_with(tmp_path, "x.py")
        mock_commit.assert_not_called()

    def test_full_success_path_applies_restarts_and_confirms_health(self, tmp_path):
        proposal = "## Summary\nfix\n```diff\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\n"
        with mock.patch("scripts.nightly_debug.run_codex", side_effect=[proposal, "판정: SAFE"]), \
             mock.patch("scripts.nightly_debug.pre_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.diff_check_applies", return_value=True), \
             mock.patch("scripts.nightly_debug.apply_diff_to_worktree", return_value=True), \
             mock.patch("scripts.nightly_debug.Path.read_text", return_value="def x():\n    return 1\n"), \
             mock.patch("scripts.nightly_debug.post_apply_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.verification_passed", return_value=True), \
             mock.patch("scripts.nightly_debug.commit_worktree_changes", return_value=True), \
             mock.patch("scripts.nightly_debug.restart_prod", return_value=True), \
             mock.patch("scripts.nightly_debug.check_health", return_value=True), \
             mock.patch("scripts.nightly_debug.get_current_commit", return_value="abc1234"), \
             mock.patch("scripts.nightly_debug.time.sleep"):
            result = process_issue(tmp_path, "x.py", "context", "prompt", dry_run=False)
        assert result["applied"] is True
        assert result["health_status"] == "헬스체크 통과"

    def test_health_check_failure_triggers_rollback(self, tmp_path):
        proposal = "## Summary\nfix\n```diff\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\n"
        with mock.patch("scripts.nightly_debug.run_codex", side_effect=[proposal, "판정: SAFE"]), \
             mock.patch("scripts.nightly_debug.pre_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.diff_check_applies", return_value=True), \
             mock.patch("scripts.nightly_debug.apply_diff_to_worktree", return_value=True), \
             mock.patch("scripts.nightly_debug.Path.read_text", return_value="def x():\n    return 1\n"), \
             mock.patch("scripts.nightly_debug.post_apply_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.verification_passed", return_value=True), \
             mock.patch("scripts.nightly_debug.commit_worktree_changes", return_value=True), \
             mock.patch("scripts.nightly_debug.restart_prod", return_value=True), \
             mock.patch("scripts.nightly_debug.check_health", return_value=False), \
             mock.patch("scripts.nightly_debug.revert_last_commit", return_value=True) as mock_revert, \
             mock.patch("scripts.nightly_debug.get_current_commit", return_value="abc1234"), \
             mock.patch("scripts.nightly_debug.time.sleep"):
            result = process_issue(tmp_path, "x.py", "context", "prompt", dry_run=False)
        assert result["applied"] is True
        assert "롤백" in result["health_status"]
        mock_revert.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nightly_debug_orchestration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.nightly_debug'`

- [ ] **Step 3: Write minimal implementation**

`scripts/nightly_debug.py` (새 파일):

```python
"""Nightly debug/auto-fix orchestrator.

Scheduled via Windows Task Scheduler ("SKIN1004-Nightly-Debug") every 2 hours
between 22:00 and 07:00. See docs/superpowers/specs/2026-07-07-nightly-debug-system-design.md
for the full design and safety rationale.

Usage:
    python scripts/nightly_debug.py            # normal run — may auto-apply + restart prod
    python scripts/nightly_debug.py --dry-run   # report only, never commits or restarts
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from scripts.nightly_debug_lib import (  # noqa: E402
    apply_diff_to_worktree,
    build_diff_review_prompt,
    build_log_error_prompt,
    build_report,
    build_verification_prompt,
    commit_worktree_changes,
    check_health,
    diff_check_applies,
    discard_worktree_changes,
    extract_diff_block,
    extract_diff_target_file,
    extract_new_errors,
    get_changed_files,
    get_current_commit,
    get_file_diff,
    has_uncommitted_changes,
    load_state,
    post_apply_check,
    pre_check,
    restart_prod,
    revert_last_commit,
    run_codex,
    save_state,
    summarize,
    verification_passed,
)

STATE_PATH = REPO_DIR / "scripts" / "_nightly_state.json"
LOG_PATH = REPO_DIR / "logs" / "pm2-prod-error.log"
REPORT_DIR = REPO_DIR / "logs" / "nightly_debug"

HEALTH_CHECK_WAIT_SECONDS = 45


def process_issue(repo_dir: Path, file_path: str, context: str, prompt: str, dry_run: bool) -> dict:
    """Run one issue (a log error or a changed file) through the full pipeline.

    `file_path` is only a *label* for issues that don't start out tied to a
    real file (e.g. log errors use the placeholder "(server log)"). Once codex
    proposes a diff, the actual target file is re-derived from the diff itself
    via pre_check/extract_diff_target_file and used for every file operation
    and for the denylist check — the caller-supplied label is never trusted
    for anything security-relevant.

    Returns a dict describing the outcome, shaped for build_report()'s
    "applied" or "reported" item formats.
    """
    proposal = run_codex(prompt, cwd=repo_dir)
    if not proposal:
        return {"file": file_path, "summary": "분석 실패", "applied": False,
                "cause": context[:200], "exclusion_reason": "codex 호출 실패/타임아웃"}

    diff_text = extract_diff_block(proposal)
    summary = summarize(proposal)
    if not diff_text:
        return {"file": file_path, "summary": summary, "applied": False,
                "cause": summary, "exclusion_reason": "수정 diff 없음 (문제 없음 또는 분석만 제공)"}

    verdict = pre_check(diff_text)
    if not verdict.auto_apply_eligible:
        return {"file": file_path, "summary": summary, "applied": False,
                "cause": summary, "exclusion_reason": "; ".join(verdict.reasons)}

    # pre_check succeeding guarantees the diff names exactly one real file.
    target_file = extract_diff_target_file(diff_text)

    if dry_run:
        return {"file": target_file, "summary": summary, "applied": False,
                "cause": summary, "exclusion_reason": "dry-run 모드"}

    if not diff_check_applies(repo_dir, diff_text):
        return {"file": target_file, "summary": summary, "applied": False,
                "cause": summary, "exclusion_reason": "diff가 현재 워킹트리에 깨끗하게 적용되지 않음"}

    full_path = repo_dir / target_file
    original_source = full_path.read_text(encoding="utf-8") if full_path.exists() else ""

    if not apply_diff_to_worktree(repo_dir, diff_text):
        return {"file": target_file, "summary": summary, "applied": False,
                "cause": summary, "exclusion_reason": "git apply 실패"}

    patched_source = (repo_dir / target_file).read_text(encoding="utf-8")
    post_verdict = post_apply_check(original_source, patched_source)
    if not post_verdict.auto_apply_eligible:
        discard_worktree_changes(repo_dir, target_file)
        return {"file": target_file, "summary": summary, "applied": False,
                "cause": summary, "exclusion_reason": "; ".join(post_verdict.reasons)}

    verification = run_codex(build_verification_prompt(proposal), cwd=repo_dir)
    if not verification or not verification_passed(verification):
        discard_worktree_changes(repo_dir, target_file)
        return {"file": target_file, "summary": summary, "applied": False,
                "cause": summary, "exclusion_reason": "2차 adversarial 검증 실패"}

    commit_message = f"[nightly-auto-fix] {summary[:60]}"
    if not commit_worktree_changes(repo_dir, target_file, commit_message):
        discard_worktree_changes(repo_dir, target_file)
        return {"file": target_file, "summary": summary, "applied": False,
                "cause": summary, "exclusion_reason": "git commit 실패"}

    restart_prod()
    time.sleep(HEALTH_CHECK_WAIT_SECONDS)
    commit_sha = get_current_commit(repo_dir)[:7]

    if check_health():
        return {"file": target_file, "summary": summary, "applied": True,
                "commit": commit_sha, "health_status": "헬스체크 통과"}

    revert_last_commit(repo_dir)
    restart_prod()
    return {"file": target_file, "summary": summary, "applied": True,
            "commit": commit_sha, "health_status": "헬스체크 실패 — 자동 롤백됨"}


def collect_log_issues(repo_dir: Path, log_path: Path, state: dict) -> "tuple[list, int]":
    """Return (issue_dicts, new_log_offset). Each issue dict has file/context/prompt keys."""
    if not log_path.exists():
        return [], state.get("last_log_offset", 0)
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    errors, new_offset = extract_new_errors(log_text, state.get("last_log_offset", 0))
    issues = [
        {"file": "(server log)", "context": err.raw, "prompt": build_log_error_prompt(err)}
        for err in errors
    ]
    return issues, new_offset


def collect_diff_issues(repo_dir: Path, state: dict) -> list:
    since_commit = state.get("last_git_commit")
    changed_files = get_changed_files(repo_dir, since_commit)
    issues = []
    for file_path in changed_files:
        diff_text = get_file_diff(repo_dir, since_commit, file_path)
        if not diff_text.strip():
            continue
        issues.append({
            "file": file_path, "context": diff_text,
            "prompt": build_diff_review_prompt(file_path, diff_text),
        })
    return issues


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Nightly debug/auto-fix orchestrator")
    parser.add_argument("--dry-run", action="store_true", help="Report only, never commit or restart prod")
    args = parser.parse_args(argv)

    state = load_state(STATE_PATH)
    skip_auto_apply = args.dry_run or has_uncommitted_changes(REPO_DIR)

    log_issues, new_log_offset = collect_log_issues(REPO_DIR, LOG_PATH, state)
    diff_issues = collect_diff_issues(REPO_DIR, state)
    all_issues = log_issues + diff_issues

    applied, reported = [], []
    for issue in all_issues:
        result = process_issue(REPO_DIR, issue["file"], issue["context"], issue["prompt"], dry_run=skip_auto_apply)
        (applied if result.get("applied") else reported).append(result)

    now = datetime.now()
    report_text = build_report(now.strftime("%Y-%m-%d %H:%M"), applied, reported)
    if report_text:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        report_path = REPORT_DIR / f"{now.strftime('%Y-%m-%d_%H%M')}.md"
        report_path.write_text(report_text, encoding="utf-8")
        try:
            from scripts.upload_to_notion import upload_update_log
            upload_update_log(str(report_path))
        except Exception:
            pass  # Notion upload failure must never block state save / exit code

    save_state(STATE_PATH, {
        "last_run_at": now.isoformat(),
        "last_git_commit": get_current_commit(REPO_DIR),
        "last_log_offset": new_log_offset,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nightly_debug_orchestration.py -v`
Expected: PASS (7 passed)

Run 전체 회귀도 확인: `python -m pytest tests/test_nightly_debug_lib.py tests/test_nightly_debug_orchestration.py -v`
Expected: PASS (64 passed — 57 in test_nightly_debug_lib.py + 7 in test_nightly_debug_orchestration.py)

- [ ] **Step 5: Commit**

```bash
git add scripts/nightly_debug.py tests/test_nightly_debug_orchestration.py
git commit -m "feat(nightly-debug): main orchestrator wiring collection, gate, apply, rollback, report"
```

---

## Task 9: 수동 dry-run 검증

**Files:**
- 없음 (코드 변경 없음 — 실제 로컬 환경에서 스크립트를 손으로 실행해 검증)

**Interfaces:** 없음 (검증 단계)

- [ ] **Step 1: 로컬에서 dry-run 실행**

Run: `cd C:\Users\DB_PC\Desktop\python_bcj\AI_Agent && python scripts/nightly_debug.py --dry-run`

Expected: 에러 없이 종료 (exit code 0). `scripts/_nightly_state.json`이 생성/갱신됨. 이슈가 있었다면 `logs/nightly_debug/<날짜>_<시각>.md`가 생성됨 (git commit이나 pm2 restart는 절대 실행되지 않아야 함 — 실행 후 `git log -3`으로 새 커밋이 없는지, `pm2 status`로 skin1004-prod의 uptime이 그대로인지 확인).

- [ ] **Step 2: git/pm2에 변화가 없었는지 확인**

Run: `git log --oneline -3` — nightly-auto-fix 커밋이 없어야 함
Run: `pm2 status skin1004-prod` — uptime이 dry-run 실행 전후로 그대로여야 함 (재시작 안 됨)

- [ ] **Step 3: 리포트 내용 확인 (이슈가 있었을 경우)**

`logs/nightly_debug/` 아래 생성된 파일을 열어, "보고만" 섹션에 나온 제외 사유가 리스크 게이트 규칙과 맞는지 (예: `dry-run 모드`로 전부 표시되는지) 확인.

이 단계에서 문제가 발견되면 해당 Task로 돌아가 수정 후 Task 8의 테스트를 다시 통과시키고, 이 Task를 재실행한다. 커밋할 코드 변경은 없으므로 Step 5(커밋)는 생략한다.

---

## Task 10: Task Scheduler 등록 (dry-run 모드로 시작)

**Files:**
- 없음 (Windows 관리 작업 — 코드 변경 아님)

**Interfaces:** 없음

**중요**: 이 작업은 스펙 문서의 "테스트 계획"에 따라 **`--dry-run` 플래그를 넣은 채로 등록**한다. 며칠간 리포트를 검토해 게이트가 예상대로 동작하는 걸 확인한 뒤, `--dry-run`을 빼는 것은 **사람이 직접 결정할 별도 작업**이다 — 이 계획에 포함하지 않는다.

- [ ] **Step 1: Task Scheduler 작업 생성**

Run (PowerShell, 관리자 권한):

```powershell
$action = New-ScheduledTaskAction -Execute "C:\Users\DB_PC\AppData\Local\Programs\Python\Python311\python.exe" `
    -Argument "scripts\nightly_debug.py --dry-run" `
    -WorkingDirectory "C:\Users\DB_PC\Desktop\python_bcj\AI_Agent"

$trigger = New-ScheduledTaskTrigger -Once -At "22:00" `
    -RepetitionInterval (New-TimeSpan -Hours 2) `
    -RepetitionDuration (New-TimeSpan -Hours 9)

Register-ScheduledTask -TaskName "SKIN1004-Nightly-Debug" -Action $action -Trigger $trigger `
    -Description "야간 자동 디버깅·개선 시스템 (dry-run 모드 — docs/superpowers/specs/2026-07-07-nightly-debug-system-design.md 참조)"
```

- [ ] **Step 2: 등록 확인**

Run: `Get-ScheduledTask -TaskName "SKIN1004-Nightly-Debug" | Format-List *`
Expected: `State: Ready`, 트리거가 22:00 시작 · 2시간 간격 · 9시간 지속으로 표시됨

- [ ] **Step 3: 수동 1회 트리거로 즉시 검증**

Run: `Start-ScheduledTask -TaskName "SKIN1004-Nightly-Debug"`
몇 분 후 `logs/nightly_debug/`에 리포트 파일이 생겼는지, `scripts/_nightly_state.json`이 갱신됐는지 확인 (Task 9와 동일한 확인 방법).

---

## Self-Review 체크리스트 (계획 작성자용 — 이미 반영됨)

- **스펙 커버리지**: 리스크 게이트(Task 5) · adversarial 재검증(Task 4, 8) · 헬스체크+롤백(Task 6, 8) · 로컬+노션 리포트(Task 7, 8) · 상태 관리(Task 1) · 스케줄링(Task 10) · dry-run(Task 8, 9, 10) — 스펙의 모든 섹션에 대응하는 Task 있음.
- **플레이스홀더 스캔**: 전 Task에 실제 코드 포함, TBD/TODO 없음.
- **타입 일관성**: `RiskVerdict.auto_apply_eligible`/`reasons`, `LogError.raw`/`kind` 필드명이 정의(Task 2, 5)와 사용처(Task 7, 8, 테스트) 전체에서 동일하게 사용됨.
- **발견해서 고친 버그**: 최초 초안은 `pre_check(file_path, diff_text)`처럼 caller가 넘긴 파일 라벨로 데널리스트를 검사했다. 로그 에러(`file_path="(server log)"`)처럼 애초에 실제 파일이 없는 이슈에서는 codex가 제안한 diff가 `sql_agent.py`를 건드려도 걸러지지 않는 구멍이 있었다. `pre_check(diff_text)`로 시그니처를 바꿔 diff 헤더에서 직접 대상 파일을 추출하도록 수정(Task 5), `process_issue`도 이후 모든 파일 연산에 caller의 `file_path`가 아닌 diff에서 뽑은 `target_file`을 쓰도록 수정(Task 8).
