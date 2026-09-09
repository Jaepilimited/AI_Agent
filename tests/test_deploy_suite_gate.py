# -*- coding: utf-8 -*-
"""배포 직전 스위트 관문 — "뜨는가" 말고 "도는가". 2026-09-09.

⛔ **왜 있나.** 같은 날 두 사고가 서로 다른 것을 보여줬다:

        09:04  import 만 있고 모듈이 없었다  → 프로세스가 안 뜬다   (시끄럽다)
        09:15  인증이 반쯤 배선돼 있었다      → 뜨는데 401 을 낸다   (조용하다)

   앞의 관문들(`syntax_errors`·`unresolved_imports`·`missing_self_methods`)은
   전부 **"프로세스가 뜨는가"** 를 본다. 09:15 상태는 그것을 다 통과하고
   `/health` 도 200 이었는데 **스위트가 76건 실패**했다 — COA 찾기 41건이
   죽어 있었다. 사용자에게 조용히 도달하는 쪽이다.

⛔ **누르기 직전에 돌린다.** 그날 배포 20분 전 결과를 믿었다가 그 사이 트리가
   바뀌어 사고가 났다. 실측으로 같은 스위트가 4분 사이에 76건 → 1건이 됐다 —
   이 트리에서 스위트 결과는 **움직이는 값**이다.

⚠️ 이 파일은 **pytest 를 부르지 않는다.** 출력 해석과 판정만 검사한다 —
   서버(자가 점검)에는 pytest 도 `tests/` 도 없어서, 판정 모듈이 스스로
   pytest 를 부르면 안 되기 때문이다.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.deploy_preflight import (  # noqa: E402
    TEST_TARGETS,
    TEST_TIMEOUT_SECONDS,
    format_suite_notice,
    parse_pytest_output,
    suite_ok,
    test_command as build_test_command,   # ⚠️ 이름이 test_ 로 시작해
)                                        #    그대로 두면 pytest 가 테스트로 수집한다

DEPLOY = ROOT / "scripts" / "deploy_new_server.py"

PASS_OUT = "3253 passed, 21 skipped, 11 warnings in 63.23s (0:01:03)"
FAIL_OUT = """FAILED tests/test_router.py::TestAPIEndpoints::test_list_models - assert 401 ...
FAILED tests/test_work_briefing.py::test_the_two_kind_tables_never_drift - As...
2 failed, 3253 passed, 21 skipped, 11 warnings in 63.23s"""


# ---------------------------------------------------------------------------
# 출력 해석
# ---------------------------------------------------------------------------

def test_a_passing_run_is_read_correctly():
    r = parse_pytest_output(PASS_OUT)
    assert r["parsed"] and r["failed"] == 0 and r["passed"] == 3253
    assert suite_ok(r, 0)


def test_a_failing_run_is_read_correctly():
    r = parse_pytest_output(FAIL_OUT)
    assert r["parsed"] and r["failed"] == 2 and r["passed"] == 3253
    assert not suite_ok(r, 1)


def test_the_failing_files_are_named():
    """⚠️ 건수만 주면 "내가 뭘 깼나" 를 뒤지게 된다."""
    r = parse_pytest_output(FAIL_OUT)
    assert dict(r["files"]) == {
        "tests/test_router.py": 1, "tests/test_work_briefing.py": 1}


def test_errors_count_as_failure():
    r = parse_pytest_output("1 passed, 2 errors in 1.0s")
    assert not suite_ok(r, 0)


def test_unreadable_output_is_not_a_pass():
    """⛔ **여기가 가장 중요하다.** pytest 가 수집 단계에서 죽으면 요약 줄이
    아예 없는데, 그때 `failed=0` 으로 읽어 통과시키면 **이 관문이 가장 필요한
    순간에 침묵한다.**"""
    for text in ("", "ImportError while loading conftest", "Traceback (most recent call last):"):
        r = parse_pytest_output(text)
        assert not r["parsed"]
        assert not suite_ok(r, 0), f"못 읽은 출력을 통과로 쳤다: {text!r}"


def test_a_nonzero_exit_is_not_a_pass():
    """⚠️ 요약이 통과라고 해도 종료코드가 0이 아니면 통과가 아니다."""
    assert not suite_ok(parse_pytest_output(PASS_OUT), 1)


def test_error_lines_also_name_files():
    r = parse_pytest_output("ERROR tests/test_x.py::t - boom\n0 failed, 1 passed, 1 errors in 1s")
    assert ("tests/test_x.py", 1) in r["files"]


# ---------------------------------------------------------------------------
# 문구
# ---------------------------------------------------------------------------

def test_the_pass_notice_is_one_line():
    """⚠️ 통과할 때 길게 찍으면 매번 뜨는 소음이 된다."""
    lines = format_suite_notice(parse_pytest_output(PASS_OUT), 63.2)
    assert len(lines) == 1
    assert "3253" in lines[0] and "63초" in lines[0]


def test_the_failure_notice_names_files_and_refuses():
    body = "\n".join(format_suite_notice(parse_pytest_output(FAIL_OUT), 63.2))
    assert "보내지 않습니다" in body
    assert "tests/test_router.py" in body


def test_the_unreadable_notice_says_so_and_offers_the_hatch():
    body = "\n".join(format_suite_notice(parse_pytest_output("boom"), 1.0))
    assert "읽지 못했습니다" in body
    assert "--skip-tests" in body


def test_the_failure_list_is_capped():
    out = "\n".join(f"FAILED tests/test_{i}.py::t - x" for i in range(20))
    body = format_suite_notice(parse_pytest_output(out + "\n20 failed, 1 passed in 1s"))
    listed = [l for l in body if ".py" in l]
    assert len(listed) == 8
    assert any("외 12개" in l for l in body)


# ---------------------------------------------------------------------------
# 배선
# ---------------------------------------------------------------------------

def test_the_command_targets_the_same_suite_we_measured():
    cmd = build_test_command("python")
    assert cmd[:3] == ["python", "-m", "pytest"]
    assert "tests/" in cmd and "--ignore=tests/frontend" in cmd


def test_the_target_list_lives_in_one_place():
    """⛔ 호출부가 목록을 다시 적으면 관문이 다른 것을 잰다."""
    src = DEPLOY.read_text(encoding="utf-8")
    assert "test_command()" in src
    assert "--ignore=tests/frontend" not in src, "배포 스크립트가 목록을 사본으로 갖고 있다"


def test_the_gate_runs_before_the_transfer():
    src = DEPLOY.read_text(encoding="utf-8")
    i_gate = src.index("if not suite_gate(")
    i_send = src.index("files = collect()")
    assert i_gate < i_send


def test_the_gate_runs_after_preflight():
    """⚠️ 문법이 깨진 트리에서 스위트를 66초 돌릴 이유가 없다."""
    src = DEPLOY.read_text(encoding="utf-8")
    assert src.index("if not dry and not preflight(") < src.index("if not suite_gate(")


def test_a_dry_run_does_not_pay_the_cost():
    """⚠️ `--dry` 는 아무것도 보내지 않는다 — 빠른 확인용이다."""
    src = DEPLOY.read_text(encoding="utf-8")
    body = src[src.index("def suite_gate"):src.index("def recent_edits_notice")]
    assert "if dry:\n        return True" in body


def test_the_escape_hatch_exists():
    """⛔ 롤백을 막는 관문이 되면 안 된다."""
    src = DEPLOY.read_text(encoding="utf-8")
    assert "--skip-tests" in src
    body = src[src.index("def suite_gate"):src.index("def recent_edits_notice")]
    assert "if skip:" in body


def test_a_failure_also_shows_who_is_editing():
    """⛔ 실패 이유가 내 잘못이 아닐 수 있다 — 같은 스위트가 4분 사이에
    76건 → 1건이 됐다."""
    src = DEPLOY.read_text(encoding="utf-8")
    body = src[src.index("def suite_gate"):src.index("def recent_edits_notice")]
    assert "recent_edits_notice()" in body


def test_being_unable_to_run_does_not_stop_the_deploy():
    """⚠️ pytest 가 없다고 배포가 죽으면 안 된다 — 다만 **타임아웃은 실패다**
    (오래 걸리는 것은 대개 진짜 문제다)."""
    src = DEPLOY.read_text(encoding="utf-8")
    body = src[src.index("def suite_gate"):src.index("def recent_edits_notice")]
    assert "except subprocess.TimeoutExpired" in body
    # ⚠️ `except Exception` 이 둘이다 — 앞은 import 실패용, 뒤는 실행 실패용.
    #    타임아웃은 **실행 쪽**보다 앞에 와야 잡힌다 (뒤에 두면 Exception 이 먼저 먹는다)
    run_catch = body.rindex("except Exception")
    assert body.index("except subprocess.TimeoutExpired") < run_catch


def test_the_timeout_is_generous():
    """⚠️ 실측 66초. 상한이 빠듯하면 느린 날에 정상 스위트가 실패로 잡힌다."""
    assert TEST_TIMEOUT_SECONDS >= 600


def test_console_encoding_is_handled():
    src = DEPLOY.read_text(encoding="utf-8")
    body = src[src.index("def suite_gate"):src.index("def recent_edits_notice")]
    assert body.count("cp949") >= 2


def test_the_gate_is_separate_from_the_notices():
    """⛔ 막는 것과 적는 것을 섞지 마라 — `run()` 은 스위트를 부르지 않는다."""
    import inspect

    from app.core import deploy_preflight

    assert "pytest" not in inspect.getsource(deploy_preflight.run)


def test_the_module_never_runs_pytest_itself():
    """⛔ 서버(자가 점검)에는 pytest 도 tests/ 도 없다."""
    src = (ROOT / "app" / "core" / "deploy_preflight.py").read_text(encoding="utf-8")
    assert "subprocess.run" in src, "git 호출은 있어야 한다"
    for bad in ("subprocess.run(test_command", "pytest.main"):
        assert bad not in src, f"판정 모듈이 스스로 스위트를 돌린다: {bad}"


@pytest.mark.parametrize("target", TEST_TARGETS)
def test_targets_are_what_we_actually_measured(target):
    assert target in ("tests/", "--ignore=tests/frontend")
