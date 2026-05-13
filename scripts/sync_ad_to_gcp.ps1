# sync_ad_to_gcp.ps1
# 로컬 AD sync -> ad_users 덤프 -> GCP MariaDB 복원
# 사용법: .\scripts\sync_ad_to_gcp.ps1

$ErrorActionPreference = "Stop"

$PROJECT_DIR = "C:\Users\DB_PC\Desktop\python_bcj\AI_Agent"
$GCP_USER    = "skin1004"
$GCP_HOST    = "34.64.99.179"
$GCP_SSH_KEY = "C:\Users\DB_PC\.ssh\gcp_skin1004"
$DB_NAME     = "skin1004_ai"
$DB_USER     = "skin1004"
$DB_PASS     = "skin1004!"
$DUMP_FILE   = "$env:TEMP\ad_users_$(Get-Date -Format 'yyyyMMdd_HHmm').sql"

Set-Location $PROJECT_DIR

Write-Host "`n[1/4] AD 동기화 실행..." -ForegroundColor Cyan
python scripts/sync_ad_users.py
if ($LASTEXITCODE -ne 0) { throw "AD sync 실패" }

Write-Host "`n[2/4] ad_users 테이블 덤프..." -ForegroundColor Cyan
& "C:\Program Files\MariaDB 11.7\bin\mysqldump.exe" `
    -h 127.0.0.1 -P 3306 `
    -u $DB_USER "-p$DB_PASS" `
    --no-tablespaces `
    $DB_NAME ad_users | Out-File -FilePath $DUMP_FILE -Encoding utf8
Write-Host "   덤프 완료: $DUMP_FILE"

Write-Host "`n[3/4] GCP로 전송..." -ForegroundColor Cyan
scp -i $GCP_SSH_KEY -o StrictHostKeyChecking=no `
    $DUMP_FILE "${GCP_USER}@${GCP_HOST}:/tmp/ad_users.sql"

Write-Host "`n[4/4] GCP MariaDB 복원..." -ForegroundColor Cyan
ssh -i $GCP_SSH_KEY -o StrictHostKeyChecking=no "${GCP_USER}@${GCP_HOST}" `
    "mysql -u $DB_USER -p'$DB_PASS' $DB_NAME < /tmp/ad_users.sql && rm /tmp/ad_users.sql"

Remove-Item $DUMP_FILE -ErrorAction SilentlyContinue
Write-Host "`n완료! GCP ad_users 업데이트됨." -ForegroundColor Green
