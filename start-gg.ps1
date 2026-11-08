# Launch MyGG from its own virtual environment. Optionally start local memory services.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'Create the virtual environment first. See ONBOARDING.md, Install MyGG.'
    exit 1
}

$configScript = @"
import json, os
from dotenv import load_dotenv
load_dotenv('.env')
from tools import memory_enabled
print(json.dumps({
    'memory': memory_enabled(),
    'local': os.getenv('LLM_BACKEND', 'anthropic') == 'local',
    'ollama': os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434'),
    'host': os.getenv('QDRANT_HOST', 'localhost'),
    'container': os.getenv('QDRANT_CONTAINER', 'mygg-qdrant')
}))
"@
$settings = & $python -c $configScript
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Check your settings and install requirements.txt with the virtual environment Python.'
    exit 1
}
$settings = $settings | ConvertFrom-Json
$loopback = @('localhost', '127.0.0.1', '::1')

# Existing installations can keep using the launcher to start their local services.
if ($settings.local -or ($settings.memory -and ([uri]$settings.ollama).Host -in $loopback)) {
    $ollamaApp = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
    if (-not (Get-Process -Name ollama -ErrorAction SilentlyContinue) -and (Test-Path $ollamaApp)) {
        Start-Process -FilePath $ollamaApp -WindowStyle Hidden
    }
}

if ($settings.memory -and $settings.host -in $loopback -and (Get-Command docker -ErrorAction SilentlyContinue)) {
    $dockerApp = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Test-Path $dockerApp)) {
        Start-Process -FilePath $dockerApp -WindowStyle Hidden
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Seconds 2
            docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { break }
        }
    }
    docker start $settings.container | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Check the Qdrant setup in ONBOARDING.md to use memory.' }
}

& $python main.py
exit $LASTEXITCODE
