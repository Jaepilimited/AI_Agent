# DB_PC -> 로컬 PC 이관 전 네트워크 실측 (읽기만 한다 - 아무것도 바꾸지 않는다)
#
# 사용법: 새 PC 의 PowerShell 에서
#   powershell -ExecutionPolicy Bypass -File migration_probe_local_pc.ps1
# 결과(OK/BLOCKED)를 그대로 보안팀 요청서에 붙인다. BLOCKED 인 것만 요청하면 된다.
# 자격증명은 쓰지 않는다 - TCP 연결과 HTTPS 응답 코드만 본다.

$ErrorActionPreference = 'SilentlyContinue'

function Test-Tcp([string]$h, [int]$p, [int]$ms = 3000) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $t = $c.BeginConnect($h, $p, $null, $null)
        if ($t.AsyncWaitHandle.WaitOne($ms) -and $c.Connected) { return 'OK' }
        return 'BLOCKED(timeout)'
    } catch { return 'BLOCKED(' + $_.Exception.InnerException.Message + ')' }
    finally { $c.Close() }
}

function Test-Https([string]$url) {
    try {
        $r = Invoke-WebRequest -Uri $url -Method Head -UseBasicParsing -TimeoutSec 8
        return "OK($($r.StatusCode))"
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code) { return "OK($code)" }   # 4xx 도 '닿았다' 는 뜻이다
        return 'BLOCKED(' + $_.Exception.Message.Split("`n")[0] + ')'
    }
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 | ? { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }).IPAddress -join ', '
$dhcp = (Get-NetIPInterface -AddressFamily IPv4 | ? { $_.ConnectionState -eq 'Connected' -and $_.Dhcp -eq 'Enabled' }).InterfaceAlias -join ', '
"PC        : $env:COMPUTERNAME"
"IPv4      : $ip"
"DHCP 사용 : $(if ($dhcp) { $dhcp + '  <- 고정 IP(또는 DHCP 예약) 요청 필요' } else { '아니오(고정)' })"
""

$tcp = @(
    @('10.1.100.5',   22,  'SSH  AI Craver Web (배포)'),
    @('10.1.150.5',   22,  'SSH  AI Craver WAS (배포 + 잔디/환율 릴레이 터널)'),
    @('10.1.150.105', 22,  'SSH  AI Craver APP (배치 크론 배포)'),
    @('10.1.150.4',   22,  'SSH  CRM WAS (AD 브리지 -> 온프레미스 DB)'),
    @('172.16.1.13',  636, 'LDAPS AD (CRM AD 브리지)'),
    @('34.64.99.179', 22,  'SSH  GCP CRM 서버 (DB_PC 에서도 막혀 있다 - 참고용)')
)
"=== 사내/서버 구간 (TCP) ==="
foreach ($t in $tcp) { "{0,-18} {1,-5} {2,-22} {3}" -f $t[0], $t[1], (Test-Tcp $t[0] $t[1]), $t[2] }
""

$https = @(
    @('https://wh.jandi.com',                       '잔디 브리핑/알림 발송'),
    @('https://open.er-api.com/v6/latest/USD',      '환율 수집'),
    @('https://api.frankfurter.app/latest',         '환율 수집(보조)'),
    @('https://llmgw.cravercorp.com/v1/models',     '사내 LLM 게이트웨이 (401/405 면 정상)'),
    @('https://github.com',                         '코드 백업 push'),
    @('https://bigquery.googleapis.com',            'BigQuery (S&OP/CRM 배치)'),
    @('https://sheets.googleapis.com',              '구글 시트 (CRM/S&OP)'),
    @('https://oauth2.googleapis.com',              '구글 인증'),
    @('https://generativelanguage.googleapis.com',  'Gemini'),
    @('https://api.anthropic.com',                  'Claude (CRM)'),
    @('https://api.notion.com',                     '노션 공개 페이지 동기화'),
    @('https://api.resend.com',                     'CRM 메일 발송')
)
"=== 외부 HTTPS (443) ==="
foreach ($h in $https) { "{0,-46} {1,-24} {2}" -f $h[0], (Test-Https $h[0]), $h[1] }
""
"=== 들어오는 쪽은 여기서 못 잰다 ==="
"  track.skin1004.app 포워딩, 동료 PC -> 이 PC 의 3100/3200/5000/5001/8765/8790 접속은"
"  이관 당일 다른 PC 에서 확인해야 한다 (문서 4절)."
