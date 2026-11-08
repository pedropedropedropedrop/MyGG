# Register the supervisor at sign-in for this folder and the current Windows user.
[CmdletBinding()]
param([switch]$Bridge, [switch]$Updates, [switch]$Ollama, [switch]$Remove)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root 'venv\Scripts\python.exe'
$script = Join-Path $root 'supervise.py'
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant())
    $suffix = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').Substring(0, 12)
} finally { $sha.Dispose() }
$name = "MyGG-$suffix"
if ($Remove) {
    if (Test-Path -LiteralPath $python) { & $python $script --stop }
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Removed $name."
    return
}
if (-not (Test-Path -LiteralPath $python)) { throw 'Install MyGG in this folder first.' }
& $python (Join-Path $root 'validate.py')
if ($LASTEXITCODE -ne 0) { throw 'Fix the configuration reported by validate.py before installing the task.' }
$pythonw = Join-Path $root 'venv\Scripts\pythonw.exe'
$arguments = '"' + $script + '"'
if ($Bridge) { $arguments += ' --bridge' }
if ($Updates) { $arguments += ' --updates' }
if ($Ollama) { $arguments += ' --ollama' }
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Trigger $trigger -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Registered $name for your next sign-in. To run it now: Start-ScheduledTask -TaskName $name"
