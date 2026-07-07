"""Tests for scripts/nightly_debug_lib.py — pure logic for the nightly debug system."""

import json
import subprocess
from unittest import mock

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
    extract_diff_block,
    parse_codex_output,
    run_codex,
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

    def test_apply_diff_check_and_apply_multiline_hunk(self, tmp_path):
        """Reproduces Critical #1: CRLF corruption from text=True input breaks
        `git apply --check -` on multi-line hunks (a trivial single-line hunk
        happens to survive the corruption and doesn't catch this)."""
        repo = _init_repo(tmp_path)
        (repo / "sample.py").write_text(
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def sub(a, b):\n"
            "    return a - b\n"
            "\n"
            "def mul(a, b):\n"
            "    return a * b\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "sample.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add more functions"], cwd=repo, check=True)

        diff_text = (
            "diff --git a/sample.py b/sample.py\n"
            "--- a/sample.py\n"
            "+++ b/sample.py\n"
            "@@ -1,8 +1,8 @@\n"
            " def add(a, b):\n"
            "-    return a + b\n"
            "+    return a + b  # fixed\n"
            "\n"
            " def sub(a, b):\n"
            "-    return a - b\n"
            "+    return a - b  # fixed\n"
            "\n"
            " def mul(a, b):\n"
            "-    return a * b\n"
            "+    return a * b  # fixed\n"
        )
        assert diff_check_applies(repo, diff_text) is True
        assert apply_diff_to_worktree(repo, diff_text) is True
        content = (repo / "sample.py").read_text(encoding="utf-8")
        assert content.count("# fixed") == 3

    def test_diff_check_applies_false_for_non_matching_diff(self, tmp_path):
        """A diff whose context lines don't exist in the current file should
        be rejected by `git apply --check -`, not silently accepted."""
        repo = _init_repo(tmp_path)
        diff_text = (
            "diff --git a/sample.py b/sample.py\n"
            "--- a/sample.py\n"
            "+++ b/sample.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def this_line_does_not_exist_in_the_file():\n"
            "-    return nonsense\n"
            "+    return nonsense  # fixed\n"
        )
        assert diff_check_applies(repo, diff_text) is False

    def test_get_file_diff_handles_korean_content(self, tmp_path):
        """Reproduces Critical #2: without explicit encoding='utf-8', git's
        stdout is decoded with locale.getpreferredencoding() (cp949 on this
        production machine), which crashes on non-ASCII output."""
        repo = _init_repo(tmp_path)
        first_commit = get_current_commit(repo)
        (repo / "sample.py").write_text(
            "def add(a, b):\n"
            "    # 한글 주석\n"
            "    return a + b\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "sample.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add korean comment"], cwd=repo, check=True)

        diff = get_file_diff(repo, first_commit, "sample.py")
        assert isinstance(diff, str)
        assert "한글 주석" in diff

    def test_has_uncommitted_changes_fails_closed_on_git_error(self, tmp_path):
        """When the git command itself fails (not a git repo at all), the
        function must fail closed (return True / 'treat as dirty') rather
        than returning False as if the repo were legitimately clean."""
        not_a_repo = tmp_path / "not_a_repo"
        not_a_repo.mkdir()
        assert has_uncommitted_changes(not_a_repo) is True

    def test_get_current_commit_raises_on_git_error(self, tmp_path):
        """When `git rev-parse HEAD` fails (not a git repo), the function
        must raise RuntimeError rather than silently returning ''."""
        not_a_repo = tmp_path / "not_a_repo"
        not_a_repo.mkdir()
        try:
            get_current_commit(not_a_repo)
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


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
