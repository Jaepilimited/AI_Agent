@echo off
REM Daily git auto-commit and push — runs at 22:00 via Task Scheduler
REM Logs to logs\git-push-daily.log

setlocal enabledelayedexpansion

set "REPO=C:\Users\DB_PC\Desktop\python_bcj\AI_Agent"
set "LOG=%REPO%\logs\git-push-daily.log"
set "GIT=C:\Program Files\Git\bin\git.exe"

cd /d "%REPO%" || exit /b 1

echo. >> "%LOG%"
echo ======================================== >> "%LOG%"
echo [%date% %time%] Daily git push starting >> "%LOG%"
echo ======================================== >> "%LOG%"

REM Check for changes
"%GIT%" status --porcelain > "%TEMP%\git_status_check.txt" 2>&1
for /f %%A in ("%TEMP%\git_status_check.txt") do set size=%%~zA
if "%size%"=="0" (
    echo [%date% %time%] No changes to commit, skipping >> "%LOG%"
    del "%TEMP%\git_status_check.txt" 2>nul
    exit /b 0
)
del "%TEMP%\git_status_check.txt" 2>nul

REM Stage all tracked + untracked changes (excluding .gitignore'd)
"%GIT%" add -A >> "%LOG%" 2>&1

REM Get date for commit message
for /f "tokens=1-3 delims=-/. " %%a in ('echo %date%') do (
    set "YYYY=%%a"
    set "MM=%%b"
    set "DD=%%c"
)

REM Create commit with timestamp
"%GIT%" commit -m "chore: daily auto-commit %YYYY%-%MM%-%DD%" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] Commit failed or nothing to commit >> "%LOG%"
    exit /b 0
)

REM Push to both remotes (origin + jaepilimited)
echo [%date% %time%] Pushing to origin... >> "%LOG%"
"%GIT%" push origin HEAD >> "%LOG%" 2>&1
set ORIGIN_EXIT=!errorlevel!

echo [%date% %time%] Pushing to jaepilimited... >> "%LOG%"
"%GIT%" push jaepilimited HEAD >> "%LOG%" 2>&1
set JAEP_EXIT=!errorlevel!

if !ORIGIN_EXIT! equ 0 if !JAEP_EXIT! equ 0 (
    echo [%date% %time%] SUCCESS: Pushed to both remotes >> "%LOG%"
    exit /b 0
) else (
    echo [%date% %time%] PARTIAL/FAIL: origin=!ORIGIN_EXIT! jaep=!JAEP_EXIT! >> "%LOG%"
    exit /b 1
)
