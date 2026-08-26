# 오늘의 환율 릴레이 — DB_PC 예약 작업 (매일 08:40, 09:10 두 번)
#
# 왜 DB_PC 인가: 서버는 환율 API 에 붙지 못한다 (프록시 화이트리스트에 없다, 2026-08-26 실측).
# ⛔ 왜 두 번인가: `open.er-api.com` 이 **09:02 KST 에 갱신**된다.
#    08:40 실행은 그 시점의 최신(=전일 고시)을 채워 09:00 브리핑이 빈칸이 되지 않게 하고,
#    09:10 실행이 그날 고시를 넣는다. 날짜는 API 가 준 고시일로 저장되므로 둘이 섞이지 않는다.
#
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_fx_relay_task.ps1 `
#       -SshPassword '<노션 AI Craver 비밀번호>' -RelayToken '<WAS .env 와 같은 값>'
#
# 되돌리기:
#   schtasks /delete /tn SKIN1004-FX-Relay /f

param(
    [Parameter(Mandatory = $true)][string]$SshPassword,
    [Parameter(Mandatory = $true)][string]$RelayToken,
    [string]$Time = "08:40",
    [string]$TaskName = "SKIN1004-FX-Relay"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo "sshenv\Scripts\python.exe"
$relay = Join-Path $repo "scripts\fx_relay.py"
$runner = Join-Path $repo "scripts\_run_fx_relay.cmd"

if (-not (Test-Path $python)) {
    throw "sshenv 가 없습니다. 먼저: python -m venv sshenv; .\sshenv\Scripts\python -m pip install paramiko"
}
if (-not (Test-Path $relay)) { throw "릴레이 스크립트를 찾지 못했습니다: $relay" }

# 비밀값은 예약 작업 정의(명령줄)에 남기지 않는다 — 작업 목록을 보는 사람에게 그대로 보인다.
$logDir = Join-Path $repo "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }
$lines = @(
    "@echo off",
    "set CRAVER_SSH_PW=$SshPassword",
    "set BRIEFING_RELAY_TOKEN=$RelayToken",
    "`"$python`" `"$relay`" >> `"$repo\logs\fx_relay.log`" 2>&1"
)
Set-Content -Path $runner -Value $lines -Encoding ascii
icacls $runner /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

# /ri 30 /du 01:00 → 08:40 과 09:10 두 번 돈다 (소스가 09:02 에 갱신되므로).
schtasks /create /tn $TaskName /tr "`"$runner`"" /sc daily /st $Time /ri 30 /du 01:00 /f | Out-Null
Write-Output "등록 완료: $TaskName · 매일 $Time 부터 30분 간격 1시간 (08:40, 09:10) · $runner"
Write-Output "지금 한 번 확인: schtasks /run /tn $TaskName  →  logs\fx_relay.log"
