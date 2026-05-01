# MyGG — Startup Script
# Starts all dependencies (Ollama, Docker/Qdrant) then launches the bot.
#
# Run from the project root:
#   powershell -ExecutionPolicy Bypass -File .\start-gg.ps1

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "MyGG - Starting Up..."

# Resolve project root from this script's location (no hardcoded paths).
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$MainScript = Join-Path $ProjectRoot "main.py"

# Configurable Qdrant container name (must match what was used to `docker run`).
$QdrantContainer = if ($env:QDRANT_CONTAINER) { $env:QDRANT_CONTAINER } else { "mygg-qdrant" }

function Write-Status($msg, $color = "Cyan") {
    Write-Host "  $msg" -ForegroundColor $color
}

function Write-Header($msg) {
    Write-Host ""
    Write-Host "  $msg" -ForegroundColor Magenta
    Write-Host "  $('-' * 40)" -ForegroundColor DarkGray
}

# Banner
Clear-Host
Write-Host ""
Write-Host "  MyGG - Personal AI Assistant" -ForegroundColor Magenta
Write-Host "  ----------------------------" -ForegroundColor DarkGray
Write-Host ""

# --- 1. Ollama ---
Write-Header "Ollama (embeddings)"
$ollama = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if ($ollama) {
    Write-Status "Already running" "Green"
} else {
    Write-Status "Starting Ollama..."
    $ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
    if (Test-Path $ollamaExe) {
        Start-Process $ollamaExe -WindowStyle Hidden
    } else {
        Write-Status "Ollama not installed at default path. Install from https://ollama.com" "Yellow"
    }
    $retries = 0
    while ($retries -lt 30) {
        Start-Sleep -Seconds 2
        try {
            $null = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 -ErrorAction Stop
            break
        } catch { $retries++ }
    }
    if ($retries -ge 30) {
        Write-Status "Failed to reach Ollama on http://localhost:11434" "Red"
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Status "Running" "Green"
}

# --- 2. Docker + Qdrant ---
Write-Header "Docker + Qdrant (vector store)"
$docker = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
if (-not $docker) {
    $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerExe) {
        Write-Status "Starting Docker Desktop..."
        Start-Process $dockerExe -WindowStyle Minimized
    } else {
        Write-Status "Docker Desktop not found at default path." "Yellow"
        Write-Status "Install from https://www.docker.com/products/docker-desktop/" "Yellow"
    }
}

# Wait for Docker daemon
$retries = 0
while ($retries -lt 60) {
    $result = docker ps 2>&1
    if ($LASTEXITCODE -eq 0) { break }
    if ($retries -eq 0) { Write-Status "Waiting for Docker daemon..." "Yellow" }
    Start-Sleep -Seconds 3
    $retries++
}
if ($retries -ge 60) {
    Write-Status "Docker failed to start!" "Red"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Status "Docker ready" "Green"

# Check Qdrant container
$qdrantRunning = docker ps --filter "name=$QdrantContainer" --format "{{.Names}}" 2>$null
if ($qdrantRunning -match $QdrantContainer) {
    Write-Status "Qdrant already running ($QdrantContainer)" "Green"
} else {
    Write-Status "Starting Qdrant container ($QdrantContainer)..."
    docker start $QdrantContainer 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Status "Qdrant container '$QdrantContainer' does not exist." "Yellow"
        Write-Status "Create it once with:" "Yellow"
        Write-Status "  docker run -d --name $QdrantContainer -p 6333:6333 -p 6334:6334 -v ${PWD}\qdrant_storage:/qdrant/storage:z qdrant/qdrant" "Yellow"
        Read-Host "Press Enter to exit"
        exit 1
    }
    Start-Sleep -Seconds 3
}

# Verify Qdrant reachable
$retries = 0
while ($retries -lt 20) {
    try {
        $null = Invoke-RestMethod -Uri "http://localhost:6333/collections" -TimeoutSec 3 -ErrorAction Stop
        break
    } catch { $retries++; Start-Sleep -Seconds 2 }
}
if ($retries -ge 20) {
    Write-Status "Qdrant not reachable on http://localhost:6333" "Red"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Status "Qdrant ready (port 6333)" "Green"

# --- 3. MyGG Bot ---
Write-Header "MyGG Bot"

if (-not (Test-Path $VenvPython)) {
    Write-Status "Python venv not found at: $VenvPython" "Red"
    Write-Status "Create it first:" "Yellow"
    Write-Status "  python -m venv venv" "Yellow"
    Write-Status "  venv\Scripts\pip install -r requirements.txt" "Yellow"
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Status "Launching..."
$Host.UI.RawUI.WindowTitle = "MyGG - Running"

Write-Host ""
Write-Host "  Starting MyGG..." -ForegroundColor Cyan
Write-Host "  Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

Set-Location $ProjectRoot
& $VenvPython $MainScript

Write-Host ""
Write-Host "  MyGG has stopped (exit code: $LASTEXITCODE)" -ForegroundColor Yellow
Write-Host ""
