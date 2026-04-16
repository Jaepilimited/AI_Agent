# Register SKIN1004-Graphify-Daily Task Scheduler entry.
# Mirrors SKIN1004-AD-Sync-Daily pattern.

param(
    [string]$TaskName = "SKIN1004-Graphify-Daily",
    [string]$TriggerTime = "03:00"
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = (Get-Command python).Source
$ScriptPath = Join-Path $ProjectRoot "scripts\build_knowledge_graph.py"
$LogPath = Join-Path $ProjectRoot "logs\knowledge_build.log"

if (-not (Test-Path $ScriptPath)) {
    Write-Error "Script not found: $ScriptPath"
    exit 1
}

# Ensure log directory exists
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null

$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At $TriggerTime

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "SKIN1004 Knowledge Map daily rebuild (Karpathy wiki + Graphify). Output: $ProjectRoot\knowledge_map\"

Write-Host "Registered $TaskName - daily at $TriggerTime" -ForegroundColor Green
Write-Host "Verify: schtasks /query /tn $TaskName"
