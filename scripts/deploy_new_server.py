"""로컬 코드 → 신규 서버(WAS/APP) 배포.

신규 서버는 git 저장소가 아니라 SFTP 전송본이다. 따라서 로컬을 수정해도
자동 반영되지 않으며, 반영하려면 이 스크립트를 실행해야 한다.

사용:
    set CRAVER_SSH_PW=...
    python scripts/deploy_new_server.py was          # WAS 배포 + 서비스 재기동
    python scripts/deploy_new_server.py app          # APP 배포 (크론용, 재기동 없음)
    python scripts/deploy_new_server.py was --dry    # 전송 목록만 확인

주의:
  · 기존 프로덕션(172.16.1.250)과 신규 서버는 별개 배포다.
    `pm2 restart skin1004-prod` 는 기존 프로덕션만 반영한다.
  · 패키지(requirements) 가 바뀐 경우엔 휠을 다시 받아 올려야 한다.
    이 스크립트는 코드만 동기화한다.
"""
from __future__ import annotations

import io
import os
import sys
import time
import subprocess
import tarfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
REMOTE = "/home/jeffrey/AI_Agent"
HOSTS = {"was": "10.1.150.5", "app": "10.1.150.105", "web": "10.1.100.5"}
USER = "jeffrey"

# 서버에 올리지 않는 것 — 과거 백업·개발 산출물·대용량 잔재
EXCLUDE_DIRS = {
    ".git", ".worktrees", "node_modules", "logs", "backup", "__pycache__",
    "venv", "sshenv", ".pytest_cache", "remotion-guide", "qdrant_db",
    "qdrant_db_learning", ".agents", ".codex", ".multiagent",
    "open-webui-backup", "craver_design_clone", "_docker_recovery_temp",
    "custom_frontend", "backup_before_custom_frontend", "temp_pdf_preview",
    "docs", "tests", "wheels",
}
# ⚠️ EXCLUDE_DIRS 는 **디렉토리 이름**으로 거른다. 이름이 겹치는 소스 패키지가
# 같이 빠지므로, 최상위 산출물 디렉토리는 반드시 EXCLUDE_PATHS(경로 기준)에 쓸 것.
#   - knowledge_map: 이름으로 걸렀더니 소스 패키지 `app/knowledge_map/` 까지 빠져
#     APP 서버의 03:00 그래프 빌드가 매일 ModuleNotFoundError 로 죽었다 (2026-08-05 발견).
# app/static/charts 는 서버사이드 차트 시절 PNG 5천여개(687MB) 잔재. 현재 미사용.
#   - data/reports: 채팅으로 생성된 보고서 HTML(원가·마진·거래처명 포함). 서버마다 따로
#     쌓이는 산출물이고 DB payload 로 재생성되므로 올리지 않는다.
#   - data/sql_results: 사용자별 조회 결과 CSV 보관분(매출·원가 포함). 서버마다
#     따로 쌓이는 산출물이고 TTL 로 스스로 지워진다 — 올리지 않는다
SUITE_HINT_SECONDS = 70   # 실측 66초 (2026-09-09, 3,253문항)

EXCLUDE_PATHS = {"app/static/charts", "knowledge_map", "data/reports", "qa_artifacts",
                 "data/sql_results"}
# qa_artifacts 는 **릴리스 검증 산출물**이다 (verification.json · selected.json ·
#   release.diff · 스테이징 사본). 프로덕션에 올릴 이유가 없다.
#   ⚠️ 그래서 **백업으로도 못 쓴다** — 여기 있는 사본은 배포본이 아니다.
#      2026-09-08 에 미커밋 로그인 경로 코드를 찾을 때 이 안의 스테이징 사본이
#      "이미 사본이 있다" 로 보였는데, 같은 디스크에 있는 untracked 파일이라
#      트리가 죽으면 함께 죽는다.
#   유래: docs/ENTRA_DIRECTORY_RELEASE_20260908.md
# 영상은 코드가 아니다 — 배포 전송에 끼면 **매번** 함께 올라간다.
#   셀라 기원 MV 는 720p 로 줄여도 40MB 다 (전체 전송량이 37.8MB 다).
#   서버에는 한 번만 올린다: python scripts/upload_media.py
#   원본에서 다시 만들려면 (ffmpeg):
#     -vf scale=-2:720 -c:v libx264 -crf 24 -preset medium
#     -c:a aac -b:a 128k -movflags +faststart
EXCLUDE_EXT = {".pyc", ".pyo", ".log", ".sql", ".pdf", ".xlsx",
               ".mp4", ".mov", ".webm", ".mkv"}
# .env 는 서버별 값이 다르므로 덮어쓰지 않는다 (최초 1회만 수동 구성)
EXCLUDE_FILES = {".env"}


def collect() -> list[Path]:
    files = []
    for root, dirs, names in os.walk(PROJ):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        rel = Path(root).relative_to(PROJ)
        rp = str(rel).replace("\\", "/")
        if any(p in EXCLUDE_DIRS for p in rel.parts):
            continue
        if any(rp == x or rp.startswith(x + "/") for x in EXCLUDE_PATHS):
            continue
        for n in names:
            p = Path(root) / n
            if n.startswith(".") or p.suffix in EXCLUDE_EXT or n in EXCLUDE_FILES:
                continue
            try:
                if p.stat().st_size > 40 * 1024 * 1024:
                    continue
            except OSError:
                continue
            files.append(p)
    return files


def preflight(skip: bool = False) -> bool:
    """보내기 전에 트리가 성한지 본다. 문제가 있으면 **보내지 않는다**.

    ⛔ **왜 있나** (2026-09-03): 같은 작업트리를 쓰는 다른 세션의 편집 중간
       상태가 배포됐다. 클래스에서 메서드 5개가 빠져나가 direct 라우트가 전부
       `AttributeError` 였는데 — 파일은 문법이 멀쩡했고, 서비스는 `active`,
       `/health` 는 **200**, 이 스크립트도 "기동 에러 0건" 이라고 찍었다.
       13분간 아무 신호도 없었다.
    ⚠️ 이 작업트리는 여러 세션이 함께 쓴다. 내 변경이 성해도 **트리가 성한지는
       별개**다 — 보내는 것은 트리 전체이기 때문이다.
    ⛔ 비상 우회로(`--skip-preflight`)를 둔다. 게이트가 롤백을 막으면 그때
       필요한 것은 게이트가 아니다. 대신 **크게 찍는다**.
    """
    sys.path.insert(0, str(PROJ))
    try:
        from app.core.deploy_preflight import run
    except Exception as e:                    # noqa: BLE001
        print(f"  [점검] 불러오지 못했습니다 ({str(e)[:80]}) - 건너뜁니다")
        return True
    ok, problems = run(PROJ)
    if ok:
        print("  [점검] 통과")
        return True
    print(f"  [점검] !! 문제 {len(problems)}건 - 보내지 않습니다")
    for line in problems[:15]:
        # ⚠️ 콘솔이 cp949 라 비ASCII 기호에서 죽는다 (CLAUDE.md). 진단이 죽으면
        #    "왜 막혔는지" 를 못 보고, 그러면 다음 사람은 그냥 우회한다
        print("        ", line.encode("cp949", "replace").decode("cp949"))
    if len(problems) > 15:
        print(f"         ... 외 {len(problems) - 15}건")
    # ⛔ 걸린 이유가 **내 잘못이 아닐 수 있다**. 이 트리는 세션 서넛이 공유해서,
    #    2026-09-09 에는 같은 스위트가 4분 사이에 76건 → 1건이 됐다 (둘 다 정확히
    #    잰 것이고 트리가 그 사이에 달라진 것이다). 최근에 바뀐 파일을 함께 찍어
    #    "기다릴 일" 이라는 판단을 즉시 할 수 있게 한다. ⚠️ 누가인지는 말하지 않는다.
    for line in recent_edits_notice():
        print(line.encode("cp949", "replace").decode("cp949"))
    if skip:
        print("  [점검] --skip-preflight 로 무시하고 보냅니다 !! 이 상태가 그대로 뜹니다")
        return True
    print("  고친 뒤 다시 실행하세요. 정말 이대로 보내야 하면 --skip-preflight")
    return False


def suite_gate(skip: bool, dry: bool) -> bool:
    """보내기 직전에 **전체 스위트**를 돌린다. 깨져 있으면 보내지 않는다.

    ⛔ **왜 여기인가** (2026-09-09): 위의 `preflight` 는 "프로세스가 뜨는가" 를
       본다. 그날 09:15 상태는 그것을 전부 통과하고 `/health` 도 200 이었는데
       **스위트가 76건 실패**했다 — COA 찾기 41건이 죽어 있었고 그건 사용자에게
       조용히 도달한다. **"도는가" 는 스위트만 안다.**
    ⛔ **누르기 직전에 돌린다.** 그날 배포 20분 전에 돌린 결과를 믿었다가
       그 사이 트리가 바뀌어 사고가 났다. 실측으로 같은 스위트가 4분 사이에
       76건 → 1건이 됐다 — 이 트리에서 스위트 결과는 **움직이는 값**이다.
    ⚠️ `--dry` 는 아무것도 보내지 않으므로 돌리지 않는다 (빠른 확인용이다).
    ⚠️ `--skip-tests` 를 반드시 둔다 — 롤백을 막는 관문이 되면 안 된다.
    """
    if dry:
        return True
    sys.path.insert(0, str(PROJ))
    try:
        from app.core.deploy_preflight import (test_command, parse_pytest_output,
                                               suite_ok, format_suite_notice,
                                               TEST_TIMEOUT_SECONDS)
    except Exception as e:                        # noqa: BLE001
        print(f"  [스위트] 불러오지 못했습니다 ({str(e)[:60]}) - 건너뜁니다")
        return True
    if skip:
        print("  [스위트] --skip-tests 로 건너뜁니다 !! 깨진 채로 나갈 수 있습니다")
        return True

    print(f"  [스위트] 돌리는 중... (약 {SUITE_HINT_SECONDS}초)")
    started = time.time()
    try:
        proc = subprocess.run(test_command(), cwd=str(PROJ), capture_output=True,
                              text=True, timeout=TEST_TIMEOUT_SECONDS)
        out, rc = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
    except subprocess.TimeoutExpired:
        print(f"  [스위트] !! {TEST_TIMEOUT_SECONDS}초를 넘겨 멈췄습니다 - 보내지 않습니다")
        return False
    except Exception as e:                        # noqa: BLE001
        print(f"  [스위트] 돌리지 못했습니다 ({str(e)[:60]}) - 건너뜁니다")
        return True                               # 못 돌린 것으로 배포를 세우지 않는다

    took = time.time() - started
    result = parse_pytest_output(out)
    for line in format_suite_notice(result, took):
        print(line.encode("cp949", "replace").decode("cp949"))
    if suite_ok(result, rc):
        return True
    # ⛔ 실패 이유가 **내 잘못이 아닐 수 있다** — 이 트리는 세션 서넛이 공유한다
    for line in recent_edits_notice():
        print(line.encode("cp949", "replace").decode("cp949"))
    print("  고친 뒤 다시 실행하세요. 정말 이대로 보내야 하면 --skip-tests")
    return False


def recent_edits_notice() -> list:
    """관문이 걸렸을 때만 붙는 보조 설명. 판정은 `deploy_preflight` 한 곳에서 한다."""
    sys.path.insert(0, str(PROJ))
    try:
        from app.core.deploy_preflight import recently_touched, format_recent_edits_notice
        return format_recent_edits_notice(recently_touched(PROJ))
    except Exception:                             # noqa: BLE001
        return []                                 # 보조 설명이 배포를 세우지 않는다


def untracked_notice(files) -> list:
    """전송 목록 중 git 이 모르는 소스를 알린다. **막지 않는다.**

    ⛔ 판정은 `app/core/deploy_preflight` 한 곳에서 한다 — 여기에 경로 규칙을
       다시 적으면 언젠가 갈린다. 그리고 `collect()` 결과를 그대로 넘겨
       **경고와 실제 전송이 같은 것을 보게** 한다.
    ⚠️ git 이 없거나 조회가 실패하면 조용히 넘어간다 — 배포를 세울 일이 아니다.
    """
    sys.path.insert(0, str(PROJ))
    try:
        from app.core.deploy_preflight import (untracked_in_payload,
                                                format_untracked_notice, recently_touched)
        rows = untracked_in_payload(PROJ, files)
        # ⛔ "방금 생긴 것" 을 이 목록 안에서 말한다 — 블록을 따로 만들면 같은 파일이
        #    두 번 적히고, 두 번 적힌 것은 곧 한 번도 안 읽힌다 (2026-09-09)
        ages = {rel: age for rel, age, _ in recently_touched(PROJ)}
        return format_untracked_notice(rows, ages)
    except Exception as e:                        # noqa: BLE001
        return [f"  [보존] 확인하지 못했습니다 ({str(e)[:60]})"]


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in HOSTS:
        print(__doc__)
        return 1
    target = sys.argv[1]
    host = HOSTS[target]
    dry = "--dry" in sys.argv

    if not dry and not preflight("--skip-preflight" in sys.argv):
        return 1
    if not suite_gate("--skip-tests" in sys.argv, dry):
        return 1

    files = collect()
    total = sum(f.stat().st_size for f in files)
    print(f"대상: {target} ({host})")
    print(f"전송 파일: {len(files)}개 / {total / 1024 / 1024:.1f} MB")
    for line in untracked_notice(files):
        # ⚠️ 콘솔이 cp949 다 (CLAUDE.md) — 진단이 죽으면 아무도 못 본다
        print(line.encode("cp949", "replace").decode("cp949"))

    if dry:
        for f in sorted(files)[:25]:
            print("   ", f.relative_to(PROJ))
        print(f"    ... (총 {len(files)}개)  [--dry 이므로 전송 안 함]")
        return 0

    pw = os.getenv("CRAVER_SSH_PW", "")
    if not pw:
        print("CRAVER_SSH_PW 환경변수가 필요합니다. (노션 AI Craver 페이지 참조)")
        return 1

    try:
        import paramiko
    except ImportError:
        print("paramiko 필요: pip install paramiko")
        return 1

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for f in files:
            tf.add(f, arcname=str(f.relative_to(PROJ)).replace("\\", "/"))
    blob = buf.getvalue()

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=USER, password=pw, timeout=30, banner_timeout=30)
    sftp = c.open_sftp()

    print(f"업로드 중... ({len(blob) / 1024 / 1024:.1f} MB 압축)")
    with sftp.open(f"{REMOTE}/_deploy.tar.gz", "wb") as f:
        f.write(blob)

    def run(cmd, t=600):
        _, o, e = c.exec_command(cmd, timeout=t)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        if out.strip():
            print(out.rstrip())
        if err.strip() and "sudo" not in err:
            print("[err]", err.strip()[:300])

    run(f"cd {REMOTE} && tar xzf _deploy.tar.gz && rm -f _deploy.tar.gz && echo '  전개 완료'")

    if target == "was":
        run(f"echo '{pw}' | sudo -S -p '' systemctl restart ai-craver; sleep 12; "
            f"systemctl is-active ai-craver | sed 's/^/  서비스: /'")
        run("curl -s -o /dev/null -w '  /health HTTP %{http_code}\\n' --max-time 15 "
            "http://127.0.0.1:3000/health")
        run("journalctl -u ai-craver --since '-1min' --no-pager 2>/dev/null "
            "| grep -ciE 'error|traceback' | xargs -I{} echo '  기동 오류: {}건'")
    else:
        run(f"cd {REMOTE} && crontab -l 2>/dev/null | grep -c . "
            f"| xargs -I{{}} echo '  크론 항목: {{}}건 (재기동 불필요)'")

    sftp.close()
    c.close()
    print("배포 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
