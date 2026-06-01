# start_native.ps1 — Run W Chats without Docker on Windows
#
# Prerequisites:
#   1. Python 3.12 installed
#   2. pip install uv  (one-time)
#   3. From apps/api/: uv pip install --system ".[dev,pipeline]"  (one-time, ~10 min)
#   4. .env updated with Neon control DB URL + Upstash Redis URL
#   5. Alembic migrations run once: cd apps/api && alembic upgrade head
#
# Usage:
#   .\scripts\start_native.ps1
#   Stop with Ctrl+C in each terminal, or close the windows.
#
# Windows note: Celery workers use --pool=solo to avoid a billiard Windows bug
# where the prefork pool may spawn two child processes sharing the same pipe_handle,
# causing tasks to fail with "not enough values to unpack (expected 3, got 0)".
# solo pool runs tasks in the worker's main process (no subprocess spawning).

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$ApiDir = Join-Path $Root "apps\api"
$Env:PYTHONPATH = $ApiDir

# Load .env into this process so child processes inherit it
foreach ($line in Get-Content (Join-Path $Root ".env")) {
    if ($line -match "^\s*#" -or $line -notmatch "=") { continue }
    $key, $val = $line -split "=", 2
    [System.Environment]::SetEnvironmentVariable($key.Trim(), $val.Trim(), "Process")
}

# Create uploads dir if missing
$uploadsDir = $env:UPLOADS_DIR
if (-not $uploadsDir) { $uploadsDir = "C:\vrd-uploads" }
New-Item -ItemType Directory -Force -Path $uploadsDir | Out-Null
Write-Host "Uploads dir: $uploadsDir"

# Helper: open a new PowerShell window running a command from $ApiDir
function Start-Service($Title, $Cmd) {
    $escaped = $Cmd -replace '"', '\"'
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        "cd '$ApiDir'; $escaped"
    ) -WindowStyle Normal
    Write-Host "Started: $Title"
}

Write-Host ""
Write-Host "Starting W Chats services (native)..."
Write-Host ""

Start-Service "API" "uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
Start-Sleep -Seconds 2

Start-Service "Worker: pipeline" "celery -A app.worker.celery_app worker --queues=pipeline --hostname=pipeline@%h --loglevel=info --pool=solo"
Start-Sleep -Seconds 1

Start-Service "Worker: runtime" "celery -A app.worker.celery_app worker --queues=runtime --hostname=runtime@%h --loglevel=info --pool=solo"
Start-Sleep -Seconds 1

Start-Service "Beat" "celery -A app.worker.celery_app beat --loglevel=info"

Write-Host ""
Write-Host "All services started. API at http://localhost:8000"
Write-Host "Close each window to stop, or use Stop-Process."
