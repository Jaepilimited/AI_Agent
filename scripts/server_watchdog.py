"""Server Watchdog — PM2 + 포트 3000/3001 감시, 자동 복구.

PM2 daemon이 죽거나 프로세스가 없어도 서버를 되살린다.
Windows Task Scheduler에 등록해서 부팅 시 자동 실행.

감시 대상:
  - skin1004-prod (포트 3000) — 프로덕션
  - skin1004-dev  (포트 3001) — 개발

감시 주기: 30초
복구 로직:
  1. PM2 daemon 살아있는지 확인 (pm2 ping)
  2. 각 프로세스가 online인지 확인
  3. 각 포트에 HTTP 200 응답하는지 확인
  → 어느 하나라도 실패하면 자동 복구
  4. 고아 프로세스 감지: 포트는 200을 응답하는데 PM2 프로세스가 online이 아니거나
     restart 카운터(↺)가 직전 체크 대비 10회 넘게 튄 경우 → PM2가 관리하지 않는
     프로세스가 포트를 점유 중인 것으로 간주하고 delete → 포트 강제 종료 → 재시작
     (2026-07-06 실제 발생: 고아 프로세스가 3000번 포트를 점유한 채 PM2가 새 프로세스를
     2555회 크래시 루프를 돌면서도 아무도 눈치채지 못한 장애)
  5. 부팅 유예(BOOT_GRACE_SECONDS): 이 앱은 CLIP/InsightFace/LangChain/BQ·MariaDB
     초기화 때문에 정상 부팅에도 35~40초가 걸린다. pm2가 보고하는 마지막 (재)시작
     시각 기준 이 시간 안의 health 실패는 "아직 부팅 중"으로 보고 실패 카운트에
     넣지 않는다. 그렇지 않으면 수동/자동 재시작이 아직 끝나기도 전에 watchdog가
     "이상 감지"로 판단해 자기 것대로 pm2 restart를 하나 더 얹고, 그 결과 두 프로세스가
     같은 포트를 동시에 bind하려다 충돌해 크래시 루프가 되는 사고가 난다 (2026-07-22
     실제 발생·재현: 수동 restart와 watchdog의 복구 restart가 겹쳐 WSAEADDRINUSE로
     반복 크래시). 단, 이 유예를 계속 받더라도 최초 이상 감지로부터
     MAX_UNHEALTHY_SECONDS를 넘기면 진짜 크래시 루프로 보고 무조건 복구를 시도한다.
"""

import json
import logging
import os
import subprocess
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CHECK_INTERVAL = 30        # 초
HEALTH_TIMEOUT = 10        # 초
# 스크립트 위치 기준으로 프로젝트 루트를 유도 (환경변수로 오버라이드 가능).
# scripts/server_watchdog.py → 프로젝트 루트는 한 단계 위.
PROJECT_DIR = os.environ.get(
    "WATCHDOG_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
ECOSYSTEM_FILE = os.path.join(PROJECT_DIR, "ecosystem.windows.config.js")
LOG_FILE = os.path.join(PROJECT_DIR, "logs", "server_watchdog.log")
MAX_CONSECUTIVE_FAILURES = 3
COOLDOWN_AFTER_RESTART = 60  # 복구 후 대기 시간 (서버 부팅 대기)
ORPHAN_RESTART_JUMP_THRESHOLD = 10  # restart 카운터가 이 값보다 많이 튀면 고아 의심
BOOT_GRACE_SECONDS = 60      # 마지막 (재)시작 이후 이 시간 안의 health 실패는 정상 부팅 중으로 간주 (실측 35~40초 + 여유)
MAX_UNHEALTHY_SECONDS = 180  # 부팅 유예를 반복 받아도 최초 이상 감지로부터 이 시간을 넘기면 무조건 복구 시도

TARGETS = [
    {"name": "skin1004-prod", "port": 3000, "label": "PROD"},
    {"name": "skin1004-dev",  "port": 3001, "label": "DEV"},
]

PM2_WINDOWS_PIPE_ENV = {
    "PM2_DAEMON_RPC_PORT": r"\\.\pipe\skin1004-db-pc-rpc.sock",
    "PM2_DAEMON_PUB_PORT": r"\\.\pipe\skin1004-db-pc-pub.sock",
    "PM2_INTERACTOR_RPC_PORT": r"\\.\pipe\skin1004-db-pc-interactor.sock",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logger = logging.getLogger("server_watchdog")
logger.setLevel(logging.INFO)

fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(fh)

ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(ch)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_pm2_subprocess_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a subprocess environment isolated from Windows' global PM2 pipes."""
    env = dict(os.environ if base_env is None else base_env)
    for key, value in PM2_WINDOWS_PIPE_ENV.items():
        env.setdefault(key, value)
    return env


def run_cmd(cmd: str, timeout: int = 15) -> tuple[int, str]:
    """Run a shell command, return (returncode, stdout+stderr).

    shell=True 실행 시 Windows는 cmd.exe를 통해 명령을 해석하므로 PATH 상의
    pm2.cmd/pm2.ps1도 pm2와 동일하게 해석된다 (별도 shutil.which 불필요).
    """
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=PROJECT_DIR,
            encoding="utf-8", errors="replace",
            env=build_pm2_subprocess_env(),
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:
        return -1, str(e)


def check_pm2_daemon() -> bool:
    """PM2 daemon이 응답하는지 확인."""
    code, out = run_cmd("pm2 ping")
    return code == 0 and "pong" in out.lower()


def ensure_pm2_daemon() -> bool:
    """PM2 daemon이 없으면 시작. 성공하면 True."""
    if check_pm2_daemon():
        return True
    logger.info("PM2 daemon 비활성 → pm2 ping으로 시작 시도")
    run_cmd("pm2 ping")
    time.sleep(3)
    if check_pm2_daemon():
        logger.info("PM2 daemon 시작 완료")
        return True
    logger.error("PM2 daemon 시작 실패")
    return False


def get_pm2_process_info(pm2_name: str) -> tuple[str, int, float]:
    """PM2 프로세스 상태 + restart 카운터(↺) + 마지막 (재)시작 이후 경과초 반환.

    상태: 'online', 'stopped', 'errored', 'missing'
    restart 카운터/경과초를 못 구하면 각각 0을 반환한다.
    """
    code, out = run_cmd("pm2 jlist")
    if code != 0:
        return "missing", 0, 0.0
    try:
        processes = json.loads(out)
        for proc in processes:
            if proc.get("name") == pm2_name:
                pm2_env = proc.get("pm2_env", {})
                status = pm2_env.get("status", "unknown")
                restarts = pm2_env.get("restart_time", 0)
                pm_uptime_ms = pm2_env.get("pm_uptime", 0)
                elapsed = max(0.0, time.time() - pm_uptime_ms / 1000.0) if pm_uptime_ms else 0.0
                return status, restarts, elapsed
        return "missing", 0, 0.0
    except Exception:
        return "missing", 0, 0.0


def get_pm2_process_status(pm2_name: str) -> str:
    """하위 호환용 래퍼: 상태 문자열만 필요할 때."""
    status, _, _ = get_pm2_process_info(pm2_name)
    return status


def check_health(port: int) -> bool:
    """HTTP health check."""
    try:
        url = f"http://127.0.0.1:{port}/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_pid_by_port(port: int) -> str | None:
    """netstat -ano 출력을 파싱해 해당 포트를 LISTENING 중인 PID를 찾는다."""
    code, out = run_cmd(f"netstat -ano | findstr :{port}")
    if code != 0 or not out:
        return None
    for line in out.splitlines():
        parts = line.split()
        # 형식: TCP    0.0.0.0:3000    0.0.0.0:0    LISTENING    12345
        if len(parts) >= 5 and parts[3].upper() == "LISTENING":
            return parts[-1]
    return None


def recover_orphan(target: dict) -> bool:
    """포트를 점유한 고아(비-PM2 관리) 프로세스 복구.

    PM2가 관리하지 않는(또는 PM2 쪽에서 계속 crash-loop 중인) 프로세스가
    포트를 선점하고 있어 새로 뜬 PM2 프로세스가 bind 하지 못하는 상황을 정리한다.
    """
    pm2_name = target["name"]
    port = target["port"]
    label = target["label"]

    logger.warning(f"=== [{label}] 고아 프로세스 복구 시작 (port {port}) ===")

    code, out = run_cmd(f"pm2 delete {pm2_name}")
    logger.info(f"[{label}] pm2 delete 결과: code={code} {out[:200]}")

    pid = find_pid_by_port(port)
    if pid:
        logger.warning(f"[{label}] 포트 {port} 점유 PID {pid} 발견 → taskkill")
        code, out = run_cmd(f"taskkill /PID {pid} /F")
        logger.info(f"[{label}] taskkill 결과: code={code} {out[:200]}")
        time.sleep(2)
    else:
        logger.warning(f"[{label}] 포트 {port} 점유 PID를 찾지 못함 (이미 해제되었을 수 있음)")

    code, out = run_cmd(f'pm2 start "{ECOSYSTEM_FILE}" --only {pm2_name}')
    if code != 0:
        logger.error(f"[{label}] pm2 start 실패: {out[:200]}")
        return False
    logger.info(f"[{label}] pm2 start 성공")

    run_cmd("pm2 save")

    logger.info(f"[{label}] 서버 부팅 대기 ({COOLDOWN_AFTER_RESTART}초)...")
    time.sleep(COOLDOWN_AFTER_RESTART)

    if check_health(port):
        logger.info(f"=== [{label}] 고아 프로세스 복구 성공! health check OK ===")
        return True
    else:
        logger.error(f"=== [{label}] 고아 프로세스 복구 후에도 health check 실패 ===")
        return False


def recover_target(target: dict) -> bool:
    """단일 대상 복구 (포트 미응답). 성공하면 True."""
    pm2_name = target["name"]
    port = target["port"]
    label = target["label"]

    logger.warning(f"=== [{label}] 서버 복구 시작 (port {port}) ===")

    # Step 1: PM2 daemon 확보
    if not ensure_pm2_daemon():
        return False

    # Step 2: pm2 resurrect 시도
    proc_status, _, _ = get_pm2_process_info(pm2_name)
    if proc_status == "missing":
        logger.info(f"[{label}] {pm2_name} 미등록 → pm2 resurrect 시도")
        code, out = run_cmd("pm2 resurrect")
        if code == 0:
            logger.info(f"[{label}] pm2 resurrect 성공: {out[:100]}")
            time.sleep(5)
            proc_status, _, _ = get_pm2_process_info(pm2_name)
        else:
            logger.warning(f"[{label}] pm2 resurrect 실패: {out[:100]}")

    # Step 3: 여전히 없으면 ecosystem.windows.config.js로 시작
    if proc_status in ("missing", "errored", "stopped"):
        logger.info(f"[{label}] 상태: {proc_status} → {ECOSYSTEM_FILE}로 시작")
        code, out = run_cmd(
            f'pm2 start "{ECOSYSTEM_FILE}" --only {pm2_name}'
        )
        if code != 0:
            logger.error(f"[{label}] pm2 start 실패: {out[:200]}")
            return False
        logger.info(f"[{label}] pm2 start 성공")

    # Step 4: online이지만 health 실패 → restart
    elif proc_status == "online":
        logger.info(f"[{label}] online이지만 health 실패 → pm2 restart")
        code, out = run_cmd(f"pm2 restart {pm2_name}")
        if code != 0:
            logger.error(f"[{label}] pm2 restart 실패: {out[:200]}")
            return False

    # Step 5: pm2 save
    run_cmd("pm2 save")

    # Step 6: health check 대기
    logger.info(f"[{label}] 서버 부팅 대기 ({COOLDOWN_AFTER_RESTART}초)...")
    time.sleep(COOLDOWN_AFTER_RESTART)

    if check_health(port):
        logger.info(f"=== [{label}] 서버 복구 성공! health check OK ===")
        return True
    else:
        logger.error(f"=== [{label}] 서버 복구 실패: health check 여전히 실패 ===")
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=" * 60)
    logger.info("Server Watchdog 시작")
    logger.info(f"  PROJECT_DIR: {PROJECT_DIR}")
    logger.info(f"  ECOSYSTEM_FILE: {ECOSYSTEM_FILE}")
    for t in TARGETS:
        logger.info(f"  [{t['label']}] {t['name']} → port {t['port']}")
    logger.info(f"  주기: {CHECK_INTERVAL}초")
    logger.info("=" * 60)

    # 대상별 연속 실패 카운터
    fail_counts: dict[str, int] = {t["name"]: 0 for t in TARGETS}
    # 대상별 직전 체크의 PM2 restart 카운터 (고아 프로세스 감지용)
    prev_restarts: dict[str, int | None] = {t["name"]: None for t in TARGETS}
    # 대상별 "최초로 health 실패를 관측한 시각" (부팅 유예를 반복 받아도 이 시각 기준
    # MAX_UNHEALTHY_SECONDS를 넘기면 무조건 복구하기 위한 안전장치). health_ok가 True가
    # 되면 즉시 초기화된다.
    unhealthy_since: dict[str, float | None] = {t["name"]: None for t in TARGETS}

    while True:
        try:
            for target in TARGETS:
                name = target["name"]
                port = target["port"]
                label = target["label"]

                health_ok = check_health(port)
                proc_status, restart_count, uptime_elapsed = get_pm2_process_info(name)

                # 고아 프로세스 감지: 포트는 응답하는데 PM2 상태가 online이 아니거나
                # restart 카운터가 직전 체크 대비 큰 폭으로 튀었으면 PM2 밖의
                # 프로세스가 포트를 점유 중인 것으로 간주.
                # 단, "waiting restart"/"restarting"/"launching"은 정상적인 pm2 restart
                # 도중에 잠깐 스치는 전이 상태라 고아로 오인하면 안 된다 (2026-07-08: 이 오인이
                # 불필요한 delete+재생성을 유발해 오히려 재시작 카운터를 튀게 만드는 걸 확인).
                _TRANSIENT_PM2_STATUSES = {"waiting restart", "restarting", "launching", "one-launch-status"}
                orphan_detected = False
                if health_ok:
                    if proc_status != "online" and proc_status not in _TRANSIENT_PM2_STATUSES:
                        orphan_detected = True
                    elif (
                        prev_restarts[name] is not None
                        and restart_count - prev_restarts[name] > ORPHAN_RESTART_JUMP_THRESHOLD
                    ):
                        orphan_detected = True

                if orphan_detected:
                    logger.warning(
                        f"[{label}] 고아 프로세스 의심: health=200 이지만 "
                        f"pm2_status={proc_status}, restart_count={restart_count} "
                        f"(이전 체크 {prev_restarts[name]})"
                    )
                    recover_orphan(target)
                    fail_counts[name] = 0
                    unhealthy_since[name] = None
                elif health_ok:
                    if fail_counts[name] > 0:
                        logger.info(f"[{label}] 정상 복귀 (이전 {fail_counts[name]}회 실패)")
                    fail_counts[name] = 0
                    unhealthy_since[name] = None
                else:
                    if unhealthy_since[name] is None:
                        unhealthy_since[name] = time.time()
                    total_unhealthy = time.time() - unhealthy_since[name]

                    # 마지막 (재)시작이 BOOT_GRACE_SECONDS 이내면 정상 부팅 중일 수 있다 —
                    # 이 앱은 실측 35~40초가 걸리므로, 이 구간의 health 실패는 실패로 세지
                    # 않는다. 다만 이 유예를 계속 받아도 총 불량 지속시간이
                    # MAX_UNHEALTHY_SECONDS를 넘기면 진짜 반복 크래시로 보고 강제 진행한다.
                    still_booting = proc_status == "online" and uptime_elapsed < BOOT_GRACE_SECONDS

                    if still_booting and total_unhealthy < MAX_UNHEALTHY_SECONDS:
                        logger.info(
                            f"[{label}] 부팅 유예 구간 (마지막 시작 후 {uptime_elapsed:.0f}s, "
                            f"이상 지속 {total_unhealthy:.0f}s) — health 실패를 카운트하지 않음"
                        )
                    else:
                        fail_counts[name] += 1
                        pm2_ok = check_pm2_daemon()
                        logger.warning(
                            f"[{label}] 이상 감지 #{fail_counts[name]}: "
                            f"pm2={pm2_ok}, proc={proc_status}, health={health_ok}, "
                            f"uptime={uptime_elapsed:.0f}s, unhealthy_for={total_unhealthy:.0f}s"
                        )

                        if fail_counts[name] >= MAX_CONSECUTIVE_FAILURES:
                            recover_target(target)
                            fail_counts[name] = 0
                            unhealthy_since[name] = None
                            # 복구 직후 상태 다시 읽어서 restart 카운터 기준을 갱신
                            _, restart_count, _ = get_pm2_process_info(name)

                prev_restarts[name] = restart_count

        except KeyboardInterrupt:
            logger.info("Watchdog 종료 (KeyboardInterrupt)")
            break
        except Exception as e:
            logger.error(f"Watchdog 루프 에러: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
