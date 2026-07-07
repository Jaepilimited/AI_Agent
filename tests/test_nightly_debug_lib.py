"""Tests for scripts/nightly_debug_lib.py — pure logic for the nightly debug system."""

import json

from scripts.nightly_debug_lib import DEFAULT_STATE, load_state, save_state, LogError, extract_new_errors


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
