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
