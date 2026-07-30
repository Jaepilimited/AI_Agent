// AI Craver — Linux WAS (10.1.150.5) PM2 설정
// 사용법: pm2 start deploy/ecosystem.linux.config.js --only skin1004-prod
// 전제: 앱 경로 /home/jeffrey/AI_Agent, 전용 venv /home/jeffrey/AI_Agent/venv
// (경로가 다르면 APP_DIR/PY 두 상수만 바꾸면 됨)
const APP_DIR = "/home/jeffrey/AI_Agent";
const PY = `${APP_DIR}/venv/bin/python`;

module.exports = {
  apps: [
    {
      name: "skin1004-prod",
      script: PY,
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 3000",
      cwd: APP_DIR,
      interpreter: "none",
      env: {
        PORT: "3000",
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
        // Proxy(10.1.50.5)가 forward-proxy 방식일 때만 주석 해제.
        // NAT/라우팅 방식이면 불필요 (docs/MIGRATION_AI_CRAVER.md 참고).
        // HTTPS_PROXY: "http://10.1.50.5:3128",
        // NO_PROXY: "localhost,127.0.0.1,10.1.200.5,10.1.150.105",
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
      script: PY,
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 3001",
      cwd: APP_DIR,
      interpreter: "none",
      env: {
        PORT: "3001",
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
        BQ_FAST_ANSWER: "1",
        BQ_TOOL_LOOP: "0",
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
    // server_watchdog.py는 Windows에선 Task Scheduler로 pm2 밖에서 돌렸음.
    // Linux에선 pm2 데몬 자체가 죽는 경우를 대비해 systemd 타이머/크론으로
    // 실행하는 것을 권장 (pm2 안에 넣지 말 것 — Windows에서의 결론과 동일).
  ],
};
