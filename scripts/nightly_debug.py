"""Nightly debug/auto-fix orchestrator.

⛔ **2026-08-18 현재 이 스크립트는 동작하지 않는다. 되살리려면 세 곳을 먼저 고쳐야 한다.**
   서버 이관(2026-07-30) 이후 대상이 전부 구서버를 가리킨 채로 남았고,
   Windows 예약 작업 `SKIN1004-Nightly-Debug` 는 **2026-07-09 마지막 실행 뒤
   다음 실행이 잡히지 않았다**(트리거 만료). 상태가 "Ready" 라 화면상 살아 있어 보였고,
   `EXPECTED_JOBS` 에도 없어 그 침묵이 감시되지 않았다.

     1) 로그    `logs/pm2-prod-error.log`  → 실제 프로덕션은 WAS `journalctl -u ai-craver`
     2) 재기동  `pm2 restart skin1004-prod` → 리다이렉트 껍데기만 재기동된다
                                              (신규는 `systemctl restart ai-craver`)
     3) 헬스    `127.0.0.1:3000`            → WAS 10.1.150.5

   ⚠️ 되살리기 전에 **자동 적용(LLM diff → 프로덕션)** 을 유지할지 먼저 정할 것.
   그 사이 이 스크립트의 값이던 "로그 에러를 읽는 일"은 자가 점검으로 옮겼다
   (`self_check._check_new_log_errors` — 어제 로그에서 **직전 주에 없던 에러 유형**만
   보고한다). 실제로 오늘 일주일치 로그를 훑어 여섯 종을 찾았는데, 값은 자동 수정이
   아니라 **읽히는 것**에 있었다.

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
    get_worktree_changed_files,
    has_uncommitted_changes,
    load_state,
    post_apply_check,
    pre_check,
    reset_worktree_fully,
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

    # Defense-in-depth: even if pre_check's header/body-path validation has an
    # edge case it doesn't catch, verify what `git apply` actually touched on
    # disk matches exactly the file we believe we're patching. Never trust the
    # pre-apply target_file alone for what happened after apply.
    actual_changed = set(get_worktree_changed_files(repo_dir))
    if actual_changed != {target_file}:
        # The changed set here is unknown/unbounded — it may include files
        # smuggled in beyond target_file (e.g. a multi-section diff that
        # creates an extra untracked file). Discarding only the one known
        # file would leave the rest dirty in the worktree, so fully reset
        # instead of the single-file discard used elsewhere in this function.
        reset_worktree_fully(repo_dir)
        return {"file": target_file, "summary": summary, "applied": False,
                "cause": summary,
                "exclusion_reason": f"git apply 실제 수정 파일이 예상과 다름: {actual_changed} (예상: {target_file})"}

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

    revert_ok = revert_last_commit(repo_dir)
    if not revert_ok:
        return {"file": target_file, "summary": summary, "applied": True,
                "commit": commit_sha, "health_status": "헬스체크 실패 — 롤백(git revert) 자체가 실패함, 수동 개입 필요"}

    restart_prod()
    time.sleep(HEALTH_CHECK_WAIT_SECONDS)
    if check_health():
        return {"file": target_file, "summary": summary, "applied": True,
                "commit": commit_sha, "health_status": "헬스체크 실패 — 자동 롤백 완료, 롤백 후 헬스체크 통과"}
    return {"file": target_file, "summary": summary, "applied": True,
            "commit": commit_sha, "health_status": "헬스체크 실패 — 롤백 이후에도 헬스체크 실패, 수동 개입 필요"}


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
        try:
            result = process_issue(REPO_DIR, issue["file"], issue["context"], issue["prompt"], dry_run=skip_auto_apply)
        except Exception as e:
            result = {"file": issue["file"], "summary": "처리 중 예외 발생", "applied": False,
                       "cause": str(e)[:200], "exclusion_reason": f"process_issue 예외: {type(e).__name__}"}
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
