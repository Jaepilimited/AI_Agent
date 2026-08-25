# 공개 노션 페이지 동기화를 DB_PC 예약 작업으로 등록한다.
#
# ⛔ 왜 DB_PC 인가 — WAS 에서는 돌 수 없다 (2026-08-25 실측):
#      · `notion.site` 가 프록시 화이트리스트에 없다 (curl 20초 타임아웃)
#      · Playwright 도, 브라우저 바이너리도 없다
#    DB_PC 는 둘 다 되고 Qdrant Cloud 에도 붙는다.
#
# ⚠️ 파이썬은 `sshenv` 격리 venv 를 쓴다. 전역 파이썬에는 qdrant_client 가 없고,
#    거기에 설치하면 별도 실행 중인 프로세스와 충돌한다 (CLAUDE.md 규칙).
#
# 사용:
#   powershell -ExecutionPolicy Bypass -File scripts\register_public_sync_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_public_sync_task.ps1 -Remove

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$TaskName = "SKIN1004-Notion-Public-Sync"
$Proj     = "C:\Users\DB_PC\Desktop\python_bcj\AI_Agent"
$Python   = Join-Path $Proj "sshenv\Scripts\pythonw.exe"
$Script   = Join-Path $Proj "scripts\sync_public_notion_pages.py"
$LogDir   = "C:\Users\DB_PC\Desktop\python_bcj\_tools\logs"
$Runner   = "C:\Users\DB_PC\Desktop\python_bcj\_tools\run_hidden.pyw"
$Log      = Join-Path $LogDir "notion-public-sync.log"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "제거됨: $TaskName"
    } else {
        Write-Output "없음: $TaskName"
    }
    exit 0
}

foreach ($p in @($Python, $Script)) {
    if (-not (Test-Path $p)) { throw "없는 경로: $p" }
}
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# ⚠️ 기존 SKIN1004 작업들과 같은 방식 — run_hidden.pyw 로 창 없이 돌리고 로그를 남긴다.
#    러너가 없으면 pythonw 로 직접 돌리되 로그를 리다이렉트한다.
if (Test-Path $Runner) {
    $exec = "C:\Users\DB_PC\AppData\Local\Programs\Python\Python311\pythonw.exe"
    $args = "`"$Runner`" --log `"$Log`" --cwd `"$Proj`" -- `"$Python`" `"$Script`" --apply"
} else {
    $exec = "cmd.exe"
    $args = "/c `"`"$Python`" `"$Script`" --apply >> `"$Log`" 2>&1`""
}

$action  = New-ScheduledTaskAction -Execute $exec -Argument $args -WorkingDirectory $Proj
# 05:00 파이프라인(WAS)과 겹치지 않게 05:40. 노션 규정은 하루 한 번이면 충분하다.
$trigger = New-ScheduledTaskTrigger -Daily -At 5:40AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "공개 노션 페이지(*.notion.site) 재수집 → Qdrant (Gemini 임베딩)" | Out-Null

Write-Output "등록됨: $TaskName (매일 05:40)"
Write-Output "  실행: $exec"
Write-Output "  로그: $Log"
Write-Output ""
Write-Output "즉시 시험: Start-ScheduledTask -TaskName '$TaskName'"
