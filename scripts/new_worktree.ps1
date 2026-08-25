# 세션별 작업트리 만들기 — 두 에이전트가 한 트리를 공유하지 않게.
#
# 왜 필요한가 (2026-08-25 실측):
#   같은 작업트리에서 세션 둘이 돌면 한쪽의 `git commit -a` 가 **다른 쪽의 미커밋
#   변경을 자기 커밋에 쓸어 담는다.** 실제로 아키텍처 캔버스 작업이 붐따 #105 커밋
#   세 개(b06660b·17ecfe9·e4861fc)에 나뉘어 들어갔다. 코드는 멀쩡했지만 이력이
#   섞여서 "이 방어선이 왜 생겼나" 를 나중에 추적할 수 없게 된다.
#
# 사용법:
#   powershell -ExecutionPolicy Bypass -File scripts\new_worktree.ps1 -Name my-task
#   powershell -ExecutionPolicy Bypass -File scripts\new_worktree.ps1 -Name my-task -From master
#
# ⛔ 작업트리는 **파일만** 나눈다. DB·Qdrant·노션·BigQuery 는 여전히 공유다 —
#    두 세션이 같은 색인에 쓰면 그건 여전히 부딪힌다 (아래 안내 참고).

param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9-]{1,40}$')]
    [string]$Name,

    [string]$From = "master",

    [string]$Prefix = "codex"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$path = Join-Path $root ".worktrees\$Name"
$branch = "$Prefix/$Name"

if (Test-Path $path) {
    Write-Host "[ERROR] 이미 있습니다: $path" -ForegroundColor Red
    Write-Host "  지우려면: git worktree remove .worktrees\$Name" -ForegroundColor Yellow
    exit 1
}

Write-Host "작업트리 생성: .worktrees\$Name  (브랜치 $branch, 기준 $From)" -ForegroundColor Cyan
git worktree add $path -b $branch $From
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# ── gitignore 라서 따라오지 않는 것들 ──
# .env 가 없으면 앱도 스크립트도 못 뜬다 (DB·Gemini·Notion 키가 전부 여기 있다).
$envSrc = Join-Path $root ".env"
if (Test-Path $envSrc) {
    Copy-Item $envSrc (Join-Path $path ".env")
    Write-Host "  .env 복사됨" -ForegroundColor DarkGray
}
else {
    Write-Host "  [주의] 원본에 .env 가 없습니다 — 수동으로 넣어야 합니다" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== 완료 ===" -ForegroundColor Green
Write-Host "  cd .worktrees\$Name" -ForegroundColor Gray
Write-Host ""
Write-Host "이 트리에서 하면 안 되는 것:" -ForegroundColor Yellow
Write-Host "  · 배포 (deploy_new_server.py) — 트리를 통째로 SFTP 전송한다."
Write-Host "    이 브랜치 상태가 그대로 프로덕션이 된다. 배포는 메인 트리에서만."
Write-Host "    (sshenv 도 gitignore 라 여기엔 없다)"
Write-Host "  · 벡터 파이프라인·동기화 스크립트 — data/notion_vectors_gemini.json 을"
Write-Host "    다시 쓰고 Qdrant Cloud 에 upsert 한다. 두 트리에서 돌리면 서로 덮는다."
Write-Host ""
Write-Host "알아둘 것:" -ForegroundColor DarkGray
Write-Host "  · pm2 dev(3001) 는 메인 트리를 서빙한다 — 여기 프론트 수정은 3001 에 안 보인다."
Write-Host "  · DB·Qdrant·노션은 공유다. 작업트리는 파일만 나눈다."
Write-Host "  · 끝나면: git worktree remove .worktrees\$Name" -ForegroundColor Gray
