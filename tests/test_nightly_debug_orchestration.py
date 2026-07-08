"""Tests for scripts/nightly_debug.py orchestration — flow control only.

All I/O (codex, git, pm2, health) is mocked; scripts/test_nightly_debug_lib.py
already covers the real logic of each mocked function.
"""

import subprocess
from unittest import mock

from scripts.nightly_debug import collect_diff_issues, collect_log_issues, process_issue
from scripts.nightly_debug_lib import get_current_commit
from tests.test_nightly_debug_lib import _init_repo


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
             mock.patch("scripts.nightly_debug.get_worktree_changed_files", return_value=["x.py"]), \
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
             mock.patch("scripts.nightly_debug.get_worktree_changed_files", return_value=["x.py"]), \
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

    def test_health_check_failure_and_revert_itself_fails(self, tmp_path):
        """revert_last_commit returns False -> report distinct failure status and never restart/re-check again."""
        proposal = "## Summary\nfix\n```diff\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\n"
        with mock.patch("scripts.nightly_debug.run_codex", side_effect=[proposal, "판정: SAFE"]), \
             mock.patch("scripts.nightly_debug.pre_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.diff_check_applies", return_value=True), \
             mock.patch("scripts.nightly_debug.apply_diff_to_worktree", return_value=True), \
             mock.patch("scripts.nightly_debug.Path.read_text", return_value="def x():\n    return 1\n"), \
             mock.patch("scripts.nightly_debug.get_worktree_changed_files", return_value=["x.py"]), \
             mock.patch("scripts.nightly_debug.post_apply_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.verification_passed", return_value=True), \
             mock.patch("scripts.nightly_debug.commit_worktree_changes", return_value=True), \
             mock.patch("scripts.nightly_debug.restart_prod", return_value=True) as mock_restart, \
             mock.patch("scripts.nightly_debug.check_health", return_value=False) as mock_health, \
             mock.patch("scripts.nightly_debug.revert_last_commit", return_value=False) as mock_revert, \
             mock.patch("scripts.nightly_debug.get_current_commit", return_value="abc1234"), \
             mock.patch("scripts.nightly_debug.time.sleep"):
            result = process_issue(tmp_path, "x.py", "context", "prompt", dry_run=False)
        assert result["applied"] is True
        assert "롤백" in result["health_status"]
        assert "실패" in result["health_status"]
        mock_revert.assert_called_once()
        # revert itself failed -> must not attempt another restart or re-check health
        mock_restart.assert_called_once()  # only the original post-commit restart
        mock_health.assert_called_once()  # only the original health check

    def test_health_check_failure_rollback_succeeds_and_post_health_passes(self, tmp_path):
        """revert_last_commit returns True, post-rollback check_health() (2nd call) returns True."""
        proposal = "## Summary\nfix\n```diff\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\n"
        with mock.patch("scripts.nightly_debug.run_codex", side_effect=[proposal, "판정: SAFE"]), \
             mock.patch("scripts.nightly_debug.pre_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.diff_check_applies", return_value=True), \
             mock.patch("scripts.nightly_debug.apply_diff_to_worktree", return_value=True), \
             mock.patch("scripts.nightly_debug.Path.read_text", return_value="def x():\n    return 1\n"), \
             mock.patch("scripts.nightly_debug.get_worktree_changed_files", return_value=["x.py"]), \
             mock.patch("scripts.nightly_debug.post_apply_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.verification_passed", return_value=True), \
             mock.patch("scripts.nightly_debug.commit_worktree_changes", return_value=True), \
             mock.patch("scripts.nightly_debug.restart_prod", return_value=True) as mock_restart, \
             mock.patch("scripts.nightly_debug.check_health", side_effect=[False, True]) as mock_health, \
             mock.patch("scripts.nightly_debug.revert_last_commit", return_value=True) as mock_revert, \
             mock.patch("scripts.nightly_debug.get_current_commit", return_value="abc1234"), \
             mock.patch("scripts.nightly_debug.time.sleep"):
            result = process_issue(tmp_path, "x.py", "context", "prompt", dry_run=False)
        assert result["applied"] is True
        assert "롤백" in result["health_status"]
        assert "통과" in result["health_status"]
        mock_revert.assert_called_once()
        assert mock_restart.call_count == 2  # original post-commit restart + post-rollback restart
        assert mock_health.call_count == 2  # original failed check + post-rollback re-check

    def test_health_check_failure_rollback_succeeds_but_post_health_still_fails(self, tmp_path):
        """revert_last_commit returns True, post-rollback check_health() (2nd call) also returns False."""
        proposal = "## Summary\nfix\n```diff\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\n"
        with mock.patch("scripts.nightly_debug.run_codex", side_effect=[proposal, "판정: SAFE"]), \
             mock.patch("scripts.nightly_debug.pre_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.diff_check_applies", return_value=True), \
             mock.patch("scripts.nightly_debug.apply_diff_to_worktree", return_value=True), \
             mock.patch("scripts.nightly_debug.Path.read_text", return_value="def x():\n    return 1\n"), \
             mock.patch("scripts.nightly_debug.get_worktree_changed_files", return_value=["x.py"]), \
             mock.patch("scripts.nightly_debug.post_apply_check", return_value=mock.MagicMock(auto_apply_eligible=True, reasons=[])), \
             mock.patch("scripts.nightly_debug.verification_passed", return_value=True), \
             mock.patch("scripts.nightly_debug.commit_worktree_changes", return_value=True), \
             mock.patch("scripts.nightly_debug.restart_prod", return_value=True) as mock_restart, \
             mock.patch("scripts.nightly_debug.check_health", side_effect=[False, False]) as mock_health, \
             mock.patch("scripts.nightly_debug.revert_last_commit", return_value=True) as mock_revert, \
             mock.patch("scripts.nightly_debug.get_current_commit", return_value="abc1234"), \
             mock.patch("scripts.nightly_debug.time.sleep"):
            result = process_issue(tmp_path, "x.py", "context", "prompt", dry_run=False)
        assert result["applied"] is True
        assert "롤백" in result["health_status"]
        assert "실패" in result["health_status"]
        assert "수동" in result["health_status"]
        mock_revert.assert_called_once()
        assert mock_restart.call_count == 2
        assert mock_health.call_count == 2

    def test_header_body_mismatched_diff_is_not_applied_end_to_end(self, tmp_path):
        """Integration guard for the Critical fix: a diff whose 'diff --git' header
        names a safe-looking file but whose --- / +++ lines name a denylisted file
        must be rejected before ever attempting to apply it. Uses the REAL pre_check
        (not mocked) so the fix itself is exercised, not a stand-in for it."""
        proposal = (
            "## Summary\nfix\n"
            "```diff\n"
            "diff --git a/allowed.py b/allowed.py\n"
            "--- a/app/agents/sql_agent.py\n"
            "+++ b/app/agents/sql_agent.py\n"
            "@@ -1 +1 @@\n-a\n+b\n"
            "```\n"
        )
        with mock.patch("scripts.nightly_debug.run_codex", return_value=proposal), \
             mock.patch("scripts.nightly_debug.diff_check_applies") as mock_check, \
             mock.patch("scripts.nightly_debug.apply_diff_to_worktree") as mock_apply, \
             mock.patch("scripts.nightly_debug.commit_worktree_changes") as mock_commit:
            result = process_issue(tmp_path, "allowed.py", "context", "prompt", dry_run=False)
        assert result["applied"] is False
        assert "header" in result["exclusion_reason"] or "path" in result["exclusion_reason"]
        mock_check.assert_not_called()
        mock_apply.assert_not_called()
        mock_commit.assert_not_called()


class TestUntrackedFileBypassDefenseInDepth:
    def test_multi_section_diff_smuggling_untracked_file_is_rejected_and_fully_reset(self, tmp_path):
        """End-to-end reproduction of Construction A: a diff whose 'diff --git'
        header and first --- / +++ pair both name the safe-looking 'allowed.py'
        (so real pre_check passes it — this is the same shape a re-reviewer
        found bypasses the header/body corroboration check), but which
        contains a second, unheadered '--- /dev/null' / '+++ b/app/core/
        backdoor.py' section that `git apply` would actually apply too,
        creating a brand-new UNTRACKED file invisible to `git diff
        --name-only`.

        get_worktree_changed_files is mocked to return what the FIXED
        function (git status --porcelain) now correctly reports post-apply:
        both allowed.py and the smuggled backdoor.py. process_issue's
        defense-in-depth check must reject this (actual_changed != {target_file})
        and must use the new full-reset cleanup (reset_worktree_fully) rather
        than the old single-file discard_worktree_changes, since an unknown/
        unbounded set of files may be dirty in the worktree."""
        construction_a_diff = (
            "diff --git a/allowed.py b/allowed.py\n"
            "--- a/allowed.py\n"
            "+++ b/allowed.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
            "--- /dev/null\n"
            "+++ b/app/core/backdoor.py\n"
            "@@ -0,0 +1 @@\n"
            "+import os  # attacker code\n"
        )
        proposal = "## Summary\nfix\n```diff\n" + construction_a_diff + "```\n"

        with mock.patch("scripts.nightly_debug.run_codex", return_value=proposal), \
             mock.patch("scripts.nightly_debug.diff_check_applies", return_value=True), \
             mock.patch("scripts.nightly_debug.apply_diff_to_worktree", return_value=True), \
             mock.patch("scripts.nightly_debug.Path.read_text", return_value="a\n"), \
             mock.patch("scripts.nightly_debug.get_worktree_changed_files",
                         return_value=["allowed.py", "app/core/backdoor.py"]), \
             mock.patch("scripts.nightly_debug.reset_worktree_fully") as mock_reset, \
             mock.patch("scripts.nightly_debug.discard_worktree_changes") as mock_discard, \
             mock.patch("scripts.nightly_debug.commit_worktree_changes") as mock_commit:
            result = process_issue(tmp_path, "allowed.py", "context", "prompt", dry_run=False)

        assert result["applied"] is False
        assert "app/core/backdoor.py" in result["exclusion_reason"] or "allowed.py" in result["exclusion_reason"]
        mock_reset.assert_called_once_with(tmp_path)
        mock_discard.assert_not_called()
        mock_commit.assert_not_called()


class TestMainIssueLoopExceptionIsolation:
    """main()'s per-issue loop wraps process_issue in try/except so one crashing
    issue (e.g. get_current_commit raising RuntimeError) doesn't lose the whole
    run's report/state. main() itself reads real files/global constants (REPO_DIR,
    STATE_PATH, LOG_PATH) at module scope, so instead of mocking out the entire
    filesystem/state layer to call main() directly, this test exercises the exact
    try/except loop pattern used inside main() against a mocked process_issue,
    verifying the isolation behavior the fix is meant to guarantee.
    """

    def test_one_issue_raising_does_not_lose_other_issues_results(self):
        issues = [
            {"file": "x.py", "context": "ctx1", "prompt": "p1"},
            {"file": "y.py", "context": "ctx2", "prompt": "p2"},
        ]
        success_result = {"file": "y.py", "applied": True, "summary": "ok",
                           "commit": "abc1234", "health_status": "헬스체크 통과"}
        with mock.patch("scripts.nightly_debug.process_issue",
                         side_effect=[Exception("boom"), success_result]) as mock_process:
            applied, reported = [], []
            for issue in issues:
                try:
                    result = mock_process(None, issue["file"], issue["context"], issue["prompt"], dry_run=False)
                except Exception as e:
                    result = {"file": issue["file"], "summary": "처리 중 예외 발생", "applied": False,
                               "cause": str(e)[:200], "exclusion_reason": f"process_issue 예외: {type(e).__name__}"}
                (applied if result.get("applied") else reported).append(result)

        assert mock_process.call_count == 2
        assert len(applied) == 1
        assert applied[0]["file"] == "y.py"
        assert applied[0]["applied"] is True
        assert len(reported) == 1
        assert reported[0]["file"] == "x.py"
        assert reported[0]["applied"] is False
        assert "예외" in reported[0]["exclusion_reason"]
        assert "boom" in reported[0]["cause"]


class TestCollectors:
    """Real (not stand-in) tests for collect_log_issues / collect_diff_issues —
    these previously had zero direct test coverage."""

    def test_collect_log_issues_returns_issue_for_new_error_line(self, tmp_path):
        log_path = tmp_path / "pm2-prod-error.log"
        log_path.write_text('{"event": "sql_generation_failed", "level": "error"}\n', encoding="utf-8")
        state = {"last_log_offset": 0}

        issues, new_offset = collect_log_issues(tmp_path, log_path, state)

        assert len(issues) == 1
        assert issues[0]["file"] == "(server log)"
        assert "sql_generation_failed" in issues[0]["context"]
        assert "sql_generation_failed" in issues[0]["prompt"]
        assert new_offset == len(log_path.read_text(encoding="utf-8"))

    def test_collect_log_issues_missing_log_file_returns_empty(self, tmp_path):
        log_path = tmp_path / "does_not_exist.log"
        state = {"last_log_offset": 5}

        issues, new_offset = collect_log_issues(tmp_path, log_path, state)

        assert issues == []
        assert new_offset == 5

    def test_collect_diff_issues_returns_issue_for_changed_file(self, tmp_path):
        repo = _init_repo(tmp_path)
        first_commit = get_current_commit(repo)
        (repo / "sample.py").write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "sample.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=repo, check=True)
        state = {"last_git_commit": first_commit}

        issues = collect_diff_issues(repo, state)

        assert len(issues) == 1
        assert issues[0]["file"] == "sample.py"
        assert "sample.py" in issues[0]["context"]
        assert "+    return a + b + 1" in issues[0]["context"]
        assert "sample.py" in issues[0]["prompt"]
