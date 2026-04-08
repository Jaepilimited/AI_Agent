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
PROJECT_DIR = r"C:\Users\DB_PC\Desktop\python_bcj\AI_Agent"
LOG_FILE = os.path.join(PROJECT_DIR, "logs", "server_watchdog.log")
MAX_CONSECUTIVE_FAILURES = 3
COOLDOWN_AFTER_RESTART = 60  # 복구 후 대기 시간 (서버 부팅 대기)

TARGETS = [
    {"name": "skin1004-prod", "port": 3000, "label": "PROD"},
    {"name": "skin1004-dev",  "port": 3001, "label": "DEV"},
]

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

def run_cmd(cmd: str, timeout: int = 15) -> tuple[int, str]:
    """Run a shell command, return (returncode, stdout+stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=PROJECT_DIR,
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


def get_pm2_process_status(pm2_name: str) -> str:
    """PM2 프로세스 상태 반환. 'online', 'stopped', 'errored', 'missing'."""
    code, out = run_cmd("pm2 jlist")
    if code != 0:
        return "missing"
    try:
        processes = json.loads(out)
        for proc in processes:
            if proc.get("name") == pm2_name:
                return proc.get("pm2_env", {}).get("status", "unknown")
        return "missing"
    except Exception:
        return "missing"


def check_health(port: int) -> bool:
    """HTTP health check."""
    try:
        url = f"http://127.0.0.1:{port}/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT) as resp:
            return resp.status == 200
    except Exception:
        return False


def recover_target(target: dict) -> bool:
    """단일 대상 복구. 성공하면 True."""
    pm2_name = target["name"]
    port = target["port"]
    label = target["label"]

    logger.warning(f"=== [{label}] 서버 복구 시작 (port {port}) ===")

    # Step 1: PM2 daemon 확보
    if not ensure_pm2_daemon():
        return False

    # Step 2: pm2 resurrect 시도
    proc_status = get_pm2_process_status(pm2_name)
    if proc_status == "missing":
        logger.info(f"[{label}] {pm2_name} 미등록 → pm2 resurrect 시도")
        code, out = run_cmd("pm2 resurrect")
        if code == 0:
            logger.info(f"[{label}] pm2 resurrect 성공: {out[:100]}")
            time.sleep(5)
            proc_status = get_pm2_process_status(pm2_name)
        else:
            logger.warning(f"[{label}] pm2 resurrect 실패: {out[:100]}")

    # Step 3: 여전히 없으면 ecosystem.config.js로 시작
    if proc_status in ("missing", "errored", "stopped"):
        logger.info(f"[{label}] 상태: {proc_status} → ecosystem.config.js로 시작")
        code, out = run_cmd(
            f"pm2 start {PROJECT_DIR}/ecosystem.config.js --only {pm2_name}"
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
    for t in TARGETS:
        logger.info(f"  [{t['label']}] {t['name']} → port {t['port']}")
    logger.info(f"  주기: {CHECK_INTERVAL}초")
    logger.info("=" * 60)

    # 대상별 연속 실패 카운터
    fail_counts: dict[str, int] = {t["name"]: 0 for t in TARGETS}

    while True:
        try:
            for target in TARGETS:
                name = target["name"]
                port = target["port"]
                label = target["label"]

                health_ok = check_health(port)

                if health_ok:
                    if fail_counts[name] > 0:
                        logger.info(f"[{label}] 정상 복귀 (이전 {fail_counts[name]}회 실패)")
                    fail_counts[name] = 0
                else:
                    fail_counts[name] += 1
                    pm2_ok = check_pm2_daemon()
                    proc_status = get_pm2_process_status(name) if pm2_ok else "unknown"
                    logger.warning(
                        f"[{label}] 이상 감지 #{fail_counts[name]}: "
                        f"pm2={pm2_ok}, proc={proc_status}, health={health_ok}"
                    )

                    if fail_counts[name] >= MAX_CONSECUTIVE_FAILURES:
                        recover_target(target)
                        fail_counts[name] = 0

        except KeyboardInterrupt:
            logger.info("Watchdog 종료 (KeyboardInterrupt)")
            break
        except Exception as e:
            logger.error(f"Watchdog 루프 에러: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
