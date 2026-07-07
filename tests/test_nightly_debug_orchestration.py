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
