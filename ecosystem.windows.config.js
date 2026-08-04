module.exports = {
  apps: [
    {
      name: "skin1004-prod",
      script: "C:\\Users\\DB_PC\\AppData\\Local\\Programs\\Python\\Python311\\python.exe",
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 3000",
      cwd: "C:\\Users\\DB_PC\\Desktop\\python_bcj\\AI_Agent",
      interpreter: "none",
      env: {
        PORT: "3000",
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
      },
      autorestart: true,
      max_memory_restart: "2G",
      restart_delay: 3000,
      max_restarts: 50,
      min_uptime: "10s",
      out_file: "logs/pm2-prod-out.log",
      error_file: "logs/pm2-prod-error.log",
      merge_logs: true,
      time: true,
    },
    {
      name: "skin1004-dev",
      script: "C:\\Users\\DB_PC\\AppData\\Local\\Programs\\Python\\Python311\\python.exe",
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 3001",
      cwd: "C:\\Users\\DB_PC\\Desktop\\python_bcj\\AI_Agent",
      interpreter: "none",
      env: {
        PORT: "3001",
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
        // dev-only A/B: template-first fast answer (BQ_FAST_ANSWER) — 2026-07-15.
        // 이전 실험(tool-use)은 BQ_TOOL_LOOP: "1"로 되돌릴 수 있음 (속도 동급이라 보류).
        // BQ_TOOL_LOOP는 pm2가 이전 env를 병합하므로 "0"으로 명시해 꺼야 함.
        BQ_FAST_ANSWER: "1",
        BQ_TOOL_LOOP: "0",
        // dev 는 .env 를 prod 와 공유하므로 이관 리다이렉트까지 상속받는다.
        // 빈 값으로 덮어써야 dev 에서 실제 화면을 띄워 UI 작업을 할 수 있다.
        // (환경변수가 .env 보다 우선한다 — pydantic-settings 기본 동작)
        MIGRATED_REDIRECT_URL: "",
      },
      autorestart: true,
      max_memory_restart: "2G",
      restart_delay: 3000,
      max_restarts: 50,
      min_uptime: "10s",
      out_file: "logs/pm2-dev-out.log",
      error_file: "logs/pm2-dev-error.log",
      merge_logs: true,
      time: true,
    },
    // NOTE: server_watchdog.py runs OUTSIDE pm2, via the Windows Task Scheduler
    // task "SKIN1004-Watchdog" (pythonw.exe, always-on). It used to also be
    // defined here as a pm2 app ("skin1004-watchdog"), but a pm2-managed
    // watchdog can't reliably recover a broken pm2 daemon it itself depends on
    // (see logs/pm2-watchdog-error.log, 2026-07-12: WinError 1455 mid-recovery).
    // Do not re-add a pm2 app for it — `pm2 start` without --only would then run
    // two independent watchdog loops against the same targets at once.
  ]
};
