# SKIN1004 AI Server — Windows 자동 시작 설정
# 관리자 권한으로 실행: powershell -ExecutionPolicy Bypass -File scripts/setup_autostart.ps1
#
# 등록 항목:
#   1. SKIN1004-PM2-AutoStart: 로그인 시 PM2 프로세스 자동 시작
#   2. SKIN1004-Watchdog: 로그인 시 watchdog 자동 시작 (PM2 죽어도 복구)

$ErrorActionPreference = "Stop"
$ProjectDir = "C:\Users\DB_PC\Desktop\python_bcj\AI_Agent"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " SKIN1004 AI Server 자동 시작 설정" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Task 1: PM2 Auto Start ---
$taskName1 = "SKIN1004-PM2-AutoStart"
$pm2Path = (Get-Command pm2 -ErrorAction SilentlyContinue).Source
if (-not $pm2Path) {
    $pm2Path = "$env:APPDATA\npm\pm2.cmd"
}

# PM2 resurrect: dump.pm2에서 저장된 프로세스 복원
$action1 = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"cd /d $ProjectDir && `"$pm2Path`" resurrect && `"$pm2Path`" save`"" `
    -WorkingDirectory $ProjectDir

$trigger1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings1 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# 기존 태스크 제거 후 재등록
Unregister-ScheduledTask -TaskName $taskName1 -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName1 -Action $action1 -Trigger $trigger1 `
    -Settings $settings1 -Description "SKIN1004 AI: PM2 프로세스 자동 복원 (로그인 시)"

Write-Host "[OK] $taskName1 등록 완료" -ForegroundColor Green

# --- Task 2: Watchdog ---
$taskName2 = "SKIN1004-Watchdog"
$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source

$action2 = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "-X utf8 $ProjectDir\scripts\server_watchdog.py" `
    -WorkingDirectory $ProjectDir

# 로그인 후 30초 딜레이 (PM2가 먼저 시작되도록)
$trigger2 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger2.Delay = "PT30S"

$settings2 = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Unregister-ScheduledTask -TaskName $taskName2 -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName2 -Action $action2 -Trigger $trigger2 `
    -Settings $settings2 -Description "SKIN1004 AI: 서버 감시 + 자동 복구 (30초 주기)"

Write-Host "[OK] $taskName2 등록 완료" -ForegroundColor Green

# --- 현재 PM2 상태 저장 ---
Write-Host ""
Write-Host "PM2 프로세스 목록 저장 (pm2 save)..." -ForegroundColor Yellow
& $pm2Path save
Write-Host "[OK] dump.pm2 저장 완료" -ForegroundColor Green

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 설정 완료!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "등록된 작업:" -ForegroundColor White
Write-Host "  1. $taskName1 — 로그인 시 PM2 프로세스 자동 복원"
Write-Host "  2. $taskName2 — 로그인 30초 후 watchdog 시작 (30초 주기 감시)"
Write-Host ""
Write-Host "확인: schtasks /query /tn SKIN1004*" -ForegroundColor Gray
Write-Host "제거: schtasks /delete /tn SKIN1004-PM2-AutoStart /f" -ForegroundColor Gray
Write-Host "      schtasks /delete /tn SKIN1004-Watchdog /f" -ForegroundColor Gray
