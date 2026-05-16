param(
    [string]$AgentPath = "C:\Users\User\airflow-project\compute_node_health_agent.py",
    [string]$PythonExecutable = "",
    [string]$TaskName = "Dash Compute Health Agent",
    [string]$NodeId = "windows_airflow",
    [string]$NodeName = "Windows Airflow Workstation",
    [string]$AirflowQueue = "system1",
    [string]$NodeRoles = "preprocessing,training",
    [int]$Port = 8899
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $AgentPath)) {
    throw "Health agent script not found at $AgentPath"
}

if (-not $PythonExecutable) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $pythonCommand) {
        throw "Python was not found. Pass -PythonExecutable with the full path to python.exe."
    }
    $PythonExecutable = $pythonCommand.Source
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$logPath = Join-Path $env:ProgramData "dash-compute-health-agent.log"
$taskScriptPath = Join-Path $env:ProgramData "dash-compute-health-agent.ps1"
$taskScript = @"
`$ErrorActionPreference = "Stop"
`$env:COMPUTE_NODE_ID = "$NodeId"
`$env:COMPUTE_NODE_NAME = "$NodeName"
`$env:AIRFLOW_QUEUE = "$AirflowQueue"
`$env:NODE_ROLES = "$NodeRoles"
Add-Content -Path "$logPath" -Value "Starting health agent at `$(Get-Date -Format o) with $PythonExecutable"
& "$PythonExecutable" "$AgentPath" --host 0.0.0.0 --port $Port 2>&1 | Tee-Object -FilePath "$logPath" -Append
"@

Set-Content -Path $taskScriptPath -Value $taskScript -Encoding UTF8

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$taskScriptPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed and started task: $TaskName for $currentUser"
Write-Host "Python: $PythonExecutable"
Write-Host "Health endpoint: http://localhost:$Port/health"
Write-Host "Log file: $logPath"
