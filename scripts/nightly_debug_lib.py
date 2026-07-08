"""Pure, side-effect-light logic for the nightly debug/auto-fix system.

Orchestration with real side effects (subprocess calls that mutate the repo
or restart prod) lives in scripts/nightly_debug.py, which imports from here.
See docs/superpowers/specs/2026-07-07-nightly-debug-system-design.md.
"""

import ast
import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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


# --- Git helpers ---


def get_current_commit(repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {result.stderr}")
    return result.stdout.strip()


def has_uncommitted_changes(repo_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode != 0:
        # Fail closed: if we can't determine cleanliness, treat as dirty so
        # auto-apply is skipped rather than proceeding on a false "clean".
        return True
    return bool(result.stdout.strip())


def get_changed_files(repo_dir: Path, since_commit: Optional[str]) -> list:
    """Files changed between since_commit and HEAD. Empty list if since_commit is falsy (first run)."""
    if not since_commit:
        return []
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{since_commit}..HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _strip_porcelain_quotes(path: str) -> str:
    """git quotes paths containing unusual characters in C-style double quotes.
    Strip that quoting if present; otherwise return the path unchanged."""
    path = path.strip()
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        return path[1:-1]
    return path


def get_worktree_changed_files(repo_dir: Path) -> list:
    """Files with ANY uncommitted change in the working tree — tracked
    modifications AND untracked (newly created) files alike. Used as a
    defense-in-depth check right after `git apply` — verifies the full set of
    files actually touched on disk matches what pre_check believed the target
    to be.

    Deliberately uses `git status --porcelain`, NOT `git diff --name-only`:
    `git diff` only reports changes to already-tracked files, so it is blind
    to a diff that creates a brand-new file (e.g. a second `--- /dev/null` /
    `+++ b/<path>` section smuggled into a multi-section diff, or the new
    path left behind by a rename) — exactly the kind of file this check
    exists to catch.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode != 0:
        return []
    paths: list = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        # Porcelain v1 format: "XY PATH" — two status-code chars, a space,
        # then the path (or "old -> new" for renames/copies). Be liberal in
        # what we accept: if a line doesn't look like the expected shape,
        # still extract *something* rather than silently dropping it —
        # silently dropping a line is exactly the bypass this function exists
        # to close.
        if len(line) > 3 and line[2] == " ":
            path_part = line[3:]
        elif len(line) > 2:
            path_part = line[2:].lstrip()
        else:
            path_part = line.strip()
        if " -> " in path_part:
            old_path, new_path = path_part.split(" -> ", 1)
            paths.append(_strip_porcelain_quotes(old_path))
            paths.append(_strip_porcelain_quotes(new_path))
        else:
            paths.append(_strip_porcelain_quotes(path_part))
    return [p for p in paths if p]


def get_file_diff(repo_dir: Path, since_commit: str, file_path: str) -> str:
    result = subprocess.run(
        ["git", "diff", f"{since_commit}..HEAD", "--", file_path], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    return result.stdout


def diff_check_applies(repo_dir: Path, diff_text: str) -> bool:
    """Dry-run check: would this unified diff apply cleanly to the current worktree?"""
    result = subprocess.run(
        ["git", "apply", "--check", "-"], input=diff_text.encode("utf-8"), cwd=str(repo_dir),
        capture_output=True, timeout=30,
    )
    return result.returncode == 0


def apply_diff_to_worktree(repo_dir: Path, diff_text: str) -> bool:
    """Apply a unified diff to the working tree (NOT committed yet)."""
    result = subprocess.run(
        ["git", "apply", "-"], input=diff_text.encode("utf-8"), cwd=str(repo_dir),
        capture_output=True, timeout=30,
    )
    return result.returncode == 0


def discard_worktree_changes(repo_dir: Path, file_path: str) -> None:
    """Discard uncommitted changes to a single file (restore to last commit)."""
    subprocess.run(
        ["git", "checkout", "--", file_path], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def reset_worktree_fully(repo_dir: Path) -> None:
    """Best-effort full worktree reset: restore all tracked modifications and
    remove all untracked files/directories.

    Used at the defense-in-depth mismatch site in process_issue, where the
    set of files a `git apply` actually touched is unknown/unbounded (it may
    include files smuggled in beyond the believed target) — a single-file
    `discard_worktree_changes` isn't enough in that case because we don't
    know the full set of what changed.

    Best-effort: never raises. This is a cleanup path, not one whose success
    gates anything downstream.
    """
    try:
        subprocess.run(
            ["git", "checkout", "--", "."], cwd=str(repo_dir),
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["git", "clean", "-fd"], cwd=str(repo_dir),
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except Exception:
        pass


def commit_worktree_changes(repo_dir: Path, file_path: str, commit_message: str) -> bool:
    add = subprocess.run(
        ["git", "add", file_path], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if add.returncode != 0:
        return False
    commit = subprocess.run(
        ["git", "commit", "-m", commit_message], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    return commit.returncode == 0


def revert_last_commit(repo_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "revert", "--no-edit", "HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    return result.returncode == 0


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


def extract_diff_body_paths(diff_text: str) -> "tuple[Optional[str], Optional[str]]":
    """Return (minus_path, plus_path) from the first '--- a/<path>' / '+++ b/<path>'
    lines in a unified diff. `git apply` resolves the actual patch target from
    these lines, NOT from the 'diff --git' header — so the header must never be
    trusted alone for a security-relevant decision (e.g. the denylist check).

    '--- /dev/null' / '+++ /dev/null' (new/deleted file) yields None for that side.
    """
    minus_match = re.search(r"^--- (\S+)", diff_text, re.MULTILINE)
    plus_match = re.search(r"^\+\+\+ (\S+)", diff_text, re.MULTILINE)

    def _strip(raw: Optional[str]) -> Optional[str]:
        if raw is None or raw == "/dev/null":
            return None
        if raw.startswith("a/") or raw.startswith("b/"):
            return raw[2:]
        return raw

    minus_path = _strip(minus_match.group(1) if minus_match else None)
    plus_path = _strip(plus_match.group(1) if plus_match else None)
    return minus_path, plus_path


def pre_check(diff_text: str) -> RiskVerdict:
    """Checks possible before actually applying the diff: target file (read from
    the diff itself, never from a caller-supplied label) + diff size.

    The target file must be corroborated by the 'diff --git' header AND the
    '--- ' / '+++ ' body lines agreeing on the same path — `git apply` actually
    resolves the patch target from the body lines, not the header, so a diff
    whose header names a safe-looking file while the body lines name a
    denylisted one must be rejected rather than denylist-checked on the header
    path alone.
    """
    reasons = []
    target_file = extract_diff_target_file(diff_text)
    minus_path, plus_path = extract_diff_body_paths(diff_text)
    body_paths = [p for p in (minus_path, plus_path) if p is not None]
    paths_agree = (
        target_file is not None and bool(body_paths) and all(p == target_file for p in body_paths)
    )

    if target_file is None:
        reasons.append("could not determine target file from diff")
    elif not body_paths:
        # A diff with no '--- '/'+++ ' body lines at all (e.g. a pure rename:
        # 'rename from' / 'rename to' headers only) gives us nothing to
        # corroborate the header path against. Treating "no body paths" as
        # vacuous agreement (the old `all([])` behavior) would let the header
        # be trusted alone — refuse instead.
        reasons.append(
            "diff has no --- / +++ body lines to verify against the header — "
            "refusing to trust header alone"
        )
    elif not paths_agree:
        reasons.append("diff header path does not match --- / +++ path — refusing to trust either")
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
_RISK_MARKERS = ("RISK", "INCOMPLETE", "FAIL", "UNSAFE", "NOT SAFE", "UNRESOLVED", "NOT RESOLVED")


def verification_passed(verification_text: str) -> bool:
    """Parse codex's adversarial-verification output for a pass/fail signal.

    Fail-closed: any risk marker present -> False. Requires an explicit safe
    marker to return True (silence/ambiguity is not consent).
    """
    upper = verification_text.upper()
    if any(marker in upper for marker in _RISK_MARKERS):
        return False
    return any(marker in upper for marker in _SAFE_MARKERS)


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
