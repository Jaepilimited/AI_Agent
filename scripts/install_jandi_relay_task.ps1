# 잔디 릴레이 — DB_PC 예약 작업 (평일 09:05부터 30분 간격, 18:35까지)
#
# ⛔ 하루 한 번으로 두지 마라. 출근 브리핑만 보낼 때는 09:05 한 번이면 됐지만,
#    이제 **셀라 알림도 함께** 나간다 (보고서 공유·의견 회신·공지).
#    알림이 다음 날 아침에 도착하면 알림이 아니다.
#
# 왜 DB_PC 인가: 서버(WAS/APP)는 프록시에서 wh.jandi.com 이 막혀 있고(403),
# DB_PC 는 잔디에 붙지만 서버에는 SSH(:22)로만 닿는다. 그래서 여기서 돈다.
# ⛔ DB_PC 를 끄면 잔디 알림도 멈춘다 (CRM 때문에 어차피 켜 두는 장비다).
#
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\install_jandi_relay_task.ps1 `
#       -SshPassword '<노션 AI Craver 비밀번호>' -RelayToken '<WAS .env 와 같은 값>'
#
# 되돌리기:
#   schtasks /delete /tn SKIN1004-Jandi-Briefing /f

param(
    [Parameter(Mandatory = $true)][string]$SshPassword,
    [Parameter(Mandatory = $true)][string]$RelayToken,
    [string]$Time = "09:05",
    [int]$EveryMinutes = 30,
    [string]$Duration = "09:30",
    [string]$TaskName = "SKIN1004-Jandi-Briefing"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo "sshenv\Scripts\python.exe"
$relay = Join-Path $repo "scripts\jandi_briefing_relay.py"
$runner = Join-Path $repo "scripts\_run_jandi_relay.cmd"

if (-not (Test-Path $python)) {
    throw "sshenv 가 없습니다. 먼저: python -m venv sshenv; .\sshenv\Scripts\python -m pip install paramiko"
}
if (-not (Test-Path $relay)) { throw "릴레이 스크립트를 찾지 못했습니다: $relay" }

# 비밀값은 명령줄(예약 작업 정의)에 남기지 않는다 — 작업 목록을 보는 사람에게 그대로 보인다.
# 대신 이 계정만 읽는 배치 파일에 넣고, 예약 작업은 그 파일만 부른다.
$lines = @(
    "@echo off",
    "set CRAVER_SSH_PW=$SshPassword",
    "set BRIEFING_RELAY_TOKEN=$RelayToken",
    "`"$python`" `"$relay`" >> `"$repo\logs\jandi_relay.log`" 2>&1"
)
$logDir = Join-Path $repo "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }
Set-Content -Path $runner -Value $lines -Encoding ascii

# 현재 사용자만 읽도록 상속을 끊는다.
icacls $runner /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

schtasks /create /tn $TaskName /tr "`"$runner`"" /sc daily /st $Time `
    /ri $EveryMinutes /du $Duration /f | Out-Null
Write-Output "등록 완료: $TaskName · 매일 $Time 부터 $EveryMinutes 분 간격 $Duration · $runner"
Write-Output "지금 한 번 확인: schtasks /run /tn $TaskName  →  logs\jandi_relay.log"
