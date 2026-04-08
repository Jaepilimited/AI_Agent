module.exports = {
  apps: [
    // ═══ PRODUCTION (3000) — 절대 kill 금지 ═══
    {
      name: "skin1004-prod",
      script: "python",
      args: "-X utf8 -m uvicorn app.main:app --host 0.0.0.0 --port 3000 --reload",
      cwd: "C:/Users/DB_PC/Desktop/python_bcj/AI_Agent",
      interpreter: "none",
      windowsHide: true,
      env: {
        PORT: "3000",
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
      },
      autorestart: true,
      max_memory_restart: "2G",
      restart_delay: 3000,
      max_restarts: 50,           // 10 → 50: PM2 daemon 크래시 복구 시 충분한 여유
      min_uptime: "10s",
      exp_backoff_restart_delay: 1000,  // 1s → 2s → 4s → ... (최대 15s)
      out_file: "logs/pm2-prod-out.log",
      error_file: "logs/pm2-prod-error.log",
      merge_logs: true,
      time: true,
    },
    // ═══ DEVELOPMENT (3001) — 테스트/개발용 ═══
    {
      name: "skin1004-dev",
      script: "python",
      args: "-X utf8 -m uvicorn app.main:app --host 0.0.0.0 --port 3001 --reload",
      cwd: "C:/Users/DB_PC/Desktop/python_bcj/AI_Agent",
      interpreter: "none",
      windowsHide: true,
      env: {
        PORT: "3001",
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
      },
      autorestart: true,
      max_memory_restart: "2G",
      restart_delay: 3000,
      max_restarts: 50,
      min_uptime: "10s",
      exp_backoff_restart_delay: 1000,
      out_file: "logs/pm2-dev-out.log",
      error_file: "logs/pm2-dev-error.log",
      merge_logs: true,
      time: true,
    },
  ]
};
