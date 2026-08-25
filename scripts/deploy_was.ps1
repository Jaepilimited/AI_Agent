# WAS 배포 — 비밀번호를 입력받아 실행한다.
#
# 왜 이 파일이 있나:
#   `deploy_new_server.py` 는 `CRAVER_SSH_PW` 환경변수를 요구한다. 그래서 매번
#   명령줄에 비밀번호를 적게 되는데, 그러면 그 값이 **셸 기록·대화 기록에 남는다.**
#   여기서는 `Read-Host -AsSecureString` 으로 받아 화면에도 기록에도 남기지 않고,
#   프로세스 환경변수로만 넘긴다 (끝나면 지운다).
#
# 사용법 (PowerShell 창에서):
#   cd C:\Users\DB_PC\Desktop\python_bcj\AI_Agent
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_was.ps1
#
# 대상을 바꾸려면: -Target app   (배치 크론 서버)

param(
    [ValidateSet("was", "app")]
    [string]$Target = "was"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root "sshenv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "[ERROR] sshenv 가 없습니다. 먼저 만들어 주세요:" -ForegroundColor Red
    Write-Host "  python -m venv sshenv" -ForegroundColor Yellow
    Write-Host "  .\sshenv\Scripts\python -m pip install paramiko" -ForegroundColor Yellow
    exit 1
}

if ([string]::IsNullOrEmpty($env:CRAVER_SSH_PW)) {
    $secure = Read-Host "SSH 비밀번호 (jeffrey@10.1.150.5)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $env:CRAVER_SSH_PW = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    $clearAfter = $true
}
else {
    Write-Host "환경변수 CRAVER_SSH_PW 를 사용합니다 (입력 생략)" -ForegroundColor DarkGray
    $clearAfter = $false
}

Write-Host ""
Write-Host "=== 배포 시작: $Target ===" -ForegroundColor Cyan
try {
    & $python "scripts\deploy_new_server.py" $Target
    $code = $LASTEXITCODE
}
finally {
    if ($clearAfter) { Remove-Item Env:\CRAVER_SSH_PW -ErrorAction SilentlyContinue }
}

Write-Host ""
if ($code -eq 0) {
    Write-Host "=== 배포 완료 (exit 0) ===" -ForegroundColor Green
    Write-Host "확인할 것:" -ForegroundColor DarkGray
    Write-Host "  python scripts\verify_migration.py" -ForegroundColor DarkGray
    Write-Host "  (SSH 후) journalctl -u ai-craver -n 50 --no-pager" -ForegroundColor DarkGray
}
else {
    Write-Host "=== 배포 실패 (exit $code) ===" -ForegroundColor Red
    Write-Host "위 로그를 그대로 복사해 주시면 원인을 짚겠습니다." -ForegroundColor DarkGray
}
exit $code
