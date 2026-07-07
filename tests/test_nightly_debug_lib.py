"""Tests for scripts/nightly_debug_lib.py — pure logic for the nightly debug system."""

import json
import subprocess

from scripts.nightly_debug_lib import (
    DEFAULT_STATE,
    load_state,
    save_state,
    LogError,
    extract_new_errors,
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
