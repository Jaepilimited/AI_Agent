@echo off
chcp 65001 > nul
setlocal

REM ── 경로 설정 ──────────────────────────────────────────────────────────────
set PROJECT_DIR=C:\Users\hbjeon\Desktop\hbjeon\python\Craver-chatbot\db
set PYTHON=C:\Users\hbjeon\AppData\Local\Programs\Python\Python311\python.exe
set LOG_DIR=%PROJECT_DIR%\logs

REM ── 로그 디렉토리 생성 ──────────────────────────────────────────────────────
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ── 스크립트 실행 ──────────────────────────────────────────────────────────
cd /d "%PROJECT_DIR%"
set PYTHONIOENCODING=utf-8
"%PYTHON%" scripts\daily_update.py >> "%LOG_DIR%\scheduler.log" 2>&1

endlocal
