# Windows: install the API as a scheduled task that starts at boot (no third-party tools).
# Run in an elevated PowerShell:
#   py -m venv C:\financial-model\venv
#   C:\financial-model\venv\Scripts\pip install "financial-model[api,charts]"
#   .\install-service.ps1 -Port 8000
# Settings: C:\financial-model\financial.yaml (auto-loaded from the working directory) or FIN_* env vars.
param(
    [string]$InstallDir = "C:\financial-model",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$TaskName = "FinancialModelAPI"
)
$exe = Join-Path $InstallDir "venv\Scripts\financial-model.exe"
if (-not (Test-Path $exe)) { throw "financial-model not found at $exe - install it first (see header)." }

$action = New-ScheduledTaskAction -Execute $exe -Argument "serve --host $HostAddress --port $Port" -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\LOCAL SERVICE" -LogonType ServiceAccount
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Started $TaskName on http://${HostAddress}:$Port (check: Invoke-RestMethod http://${HostAddress}:$Port/health)"
