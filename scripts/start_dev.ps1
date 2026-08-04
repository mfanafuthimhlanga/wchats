# start_dev.ps1 — Start all Veridian dev services in the background.
# Logs go to <repo-root>/logs/<service>.log and logs/<service>_err.log.
# Usage: .\start_dev.ps1 [-Stop]

param([switch]$Stop)

$root   = $PSScriptRoot
$logs   = "$root\logs"
$api    = "$root\apps\api"
$admin  = "$root\apps\admin"
$widget = "$root\apps\widget"
$redis  = "C:\Program Files\Redis\redis-server.exe"
$rconf  = "C:\Program Files\Redis\redis.windows.conf"
$uv     = "C:\Users\Bantu\AppData\Local\Programs\Python\Python312\Scripts\uvicorn.exe"
$cel    = "C:\Users\Bantu\AppData\Local\Programs\Python\Python312\Scripts\celery.exe"
$npm    = "C:\Program Files\nodejs\npm.cmd"

if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }

if ($Stop) {
    Write-Host "Stopping all dev services..."
    Get-Process -Name "redis-server","uvicorn","celery","node","cmd" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    # Also catch workers launched as "python -m celery" (register as python.exe)
    Get-WmiObject Win32_Process -Filter "Name like '%python%'" |
        Where-Object { $_.CommandLine -like '*celery*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "Done."
    exit 0
}

# Kill any stale processes from a previous run (including python.exe celery zombies)
Get-Process -Name "redis-server","uvicorn","celery" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Get-WmiObject Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -like '*celery*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host "Starting dev services (logs -> $logs)..."

# Redis
Start-Process $redis `
    -ArgumentList "`"$rconf`"" `
    -RedirectStandardOutput "$logs\redis.log" `
    -RedirectStandardError  "$logs\redis_err.log" `
    -WindowStyle Hidden

# FastAPI
Start-Process $uv `
    -ArgumentList "app.main:app","--reload","--port","8000" `
    -WorkingDirectory $api `
    -RedirectStandardOutput "$logs\api.log" `
    -RedirectStandardError  "$logs\api_err.log" `
    -WindowStyle Hidden

# Celery (pipeline + runtime queues)
Start-Process $cel `
    -ArgumentList "-A","app.worker.celery_app","worker","--loglevel=info","-Q","pipeline,runtime" `
    -WorkingDirectory $api `
    -RedirectStandardOutput "$logs\celery.log" `
    -RedirectStandardError  "$logs\celery_err.log" `
    -WindowStyle Hidden

# Admin — Next.js
Start-Process "cmd.exe" `
    -ArgumentList "/c","`"$npm`" run dev" `
    -WorkingDirectory $admin `
    -RedirectStandardOutput "$logs\admin.log" `
    -RedirectStandardError  "$logs\admin_err.log" `
    -WindowStyle Hidden

# Widget — Vite
Start-Process "cmd.exe" `
    -ArgumentList "/c","`"$npm`" run dev" `
    -WorkingDirectory $widget `
    -RedirectStandardOutput "$logs\widget.log" `
    -RedirectStandardError  "$logs\widget_err.log" `
    -WindowStyle Hidden

Start-Sleep -Seconds 6

$services = @(
    @{Name="Redis";   Process="redis-server"},
    @{Name="FastAPI"; Process="uvicorn"},
    @{Name="Celery";  Process="celery"},
    @{Name="Admin";   Log="$logs\admin.log";  Token="localhost:3000"},
    @{Name="Widget";  Log="$logs\widget.log"; Token="localhost:5173"}
)

foreach ($svc in $services) {
    if ($svc.Process) {
        $p = Get-Process -Name $svc.Process -ErrorAction SilentlyContinue
        $status = if ($p) { "RUNNING  (PID $($p.Id))" } else { "NOT RUNNING" }
    } else {
        $status = if (Select-String -Path $svc.Log -Pattern $svc.Token -Quiet -ErrorAction SilentlyContinue) {
            "RUNNING"
        } else { "starting..." }
    }
    Write-Host "  $($svc.Name.PadRight(8)) $status"
}

Write-Host ""
Write-Host "Logs: $logs\*.log"
Write-Host "Stop: .\start_dev.ps1 -Stop"
