# start_demo.ps1 — W Chats Demo Mode (local PC + Cloudflare Quick Tunnel)
#
# Supersedes the Oracle Cloud VM / Caddy / DuckDNS / systemd path documented in
# 12-CONTEXT.md (decision_revision). That path required a credit card; this script
# runs on the user's local Windows PC with zero paid services.
#
# What this script does:
#   1. Loads .env into process environment so child windows inherit all secrets
#      (Neon, Upstash, Anthropic, Voyage, JWT, Clerk) — D-04.
#   2. Launches uvicorn app.main:app --host 0.0.0.0 --port 8000 (NO --reload;
#      0.0.0.0 required so cloudflared can connect) — D-01/D-02.
#   3. Launches the runtime Celery worker (--pool=solo --concurrency=1
#      --queues=runtime ONLY — no pipeline worker, no beat) — D-02/D-03/D-12.
#   4. Launches cloudflared quick tunnel (--no-autoupdate prevents a version
#      change mid-demo) — D-05. The tunnel window prints the random
#      https://<name>.trycloudflare.com URL; copy it manually.
#   5. Prints a === Next steps === block for the per-session data-api update.
#
# Closing the TUNNEL WINDOW ends the public HTTPS session.
# Secrets are loaded into the process environment only — NEVER echoed.
#
# Usage:
#   .\scripts\start_demo.ps1          # launch all three windows
#   .\scripts\start_demo.ps1 -WhatIf  # parse/syntax check only (no windows opened)

[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = "Stop"
$Root    = Split-Path $PSScriptRoot -Parent
$ApiDir  = Join-Path $Root "apps\api"
$Env:PYTHONPATH = $ApiDir

# ---------------------------------------------------------------------------
# Load .env into this process so all child windows inherit the environment.
# Values are set into the process environment; they are NEVER printed.
# ---------------------------------------------------------------------------
foreach ($line in Get-Content (Join-Path $Root ".env")) {
    if ($line -match "^\s*#" -or $line -notmatch "=") { continue }
    $key, $val = $line -split "=", 2
    [System.Environment]::SetEnvironmentVariable($key.Trim(), $val.Trim(), "Process")
}

# ---------------------------------------------------------------------------
# Helper: open a new PowerShell window running $Cmd from $ApiDir.
# The -NoExit flag keeps the window visible so the user can see service output.
# ---------------------------------------------------------------------------
function Start-Service($Title, $Cmd) {
    $escaped = $Cmd -replace '"', '\"'
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        "cd '$ApiDir'; $escaped"
    ) -WindowStyle Normal
    Write-Host "Started: $Title"
}

# ---------------------------------------------------------------------------
# -WhatIf guard: short-circuit BEFORE any Start-Process / Start-Service call.
# SupportsShouldProcess sets $WhatIfPreference=$true when -WhatIf is passed,
# which causes this early return to fire — the script parses but launches nothing.
# ---------------------------------------------------------------------------
if ($WhatIfPreference) { return }

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== W Chats Demo Mode ==="
Write-Host "    local PC + Cloudflare Quick Tunnel"
Write-Host ""

# ---------------------------------------------------------------------------
# Service 1: uvicorn API (0.0.0.0:8000, no --reload, no --concurrency flag)
# 0.0.0.0 is required so cloudflared can proxy to this port (D-02).
# ---------------------------------------------------------------------------
Start-Service "API" "uvicorn app.main:app --host 0.0.0.0 --port 8000"
Start-Sleep -Seconds 3

# ---------------------------------------------------------------------------
# Service 2: Celery runtime worker (solo pool, runtime queue only)
# pipeline worker and beat are intentionally excluded (D-03, 4 GB RAM).
# solo pool is required on Windows (avoids billiard pipe_handle bug).
# ---------------------------------------------------------------------------
Start-Service "Worker: runtime" "celery -A app.worker.celery_app worker --queues=runtime --hostname=runtime@%h --loglevel=info --pool=solo --concurrency=1"
Start-Sleep -Seconds 2

# ---------------------------------------------------------------------------
# Cloudflare Quick Tunnel
# --no-autoupdate: prevents cloudflared from restarting mid-demo on an update.
# The tunnel window prints the https://<random>.trycloudflare.com URL.
# There is no reliable programmatic way to capture this from a spawned window;
# the user copies it manually from the TUNNEL WINDOW.
# ---------------------------------------------------------------------------
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Write-Host ''; Write-Host '=== TUNNEL WINDOW ==='; Write-Host 'Copy the https://*.trycloudflare.com URL from the lines below:'; Write-Host ''; cloudflared tunnel --url http://localhost:8000 --no-autoupdate"
) -WindowStyle Normal
Write-Host "Started: Cloudflare Tunnel (URL will appear in TUNNEL WINDOW)"

# ---------------------------------------------------------------------------
# Next-steps instructions — no secret value is printed here or anywhere above.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Next steps ==="
Write-Host "1. Wait ~5-10s for all three windows to finish starting."
Write-Host "2. Copy the https://*.trycloudflare.com URL from the TUNNEL WINDOW."
Write-Host "3. Open apps/admin/app/page.tsx and set WCHATS_TUNNEL_API_BASE to that URL."
Write-Host "4. Run: git add apps/admin/app/page.tsx && git commit -m 'demo: set tunnel URL' && git push"
Write-Host "5. Wait ~60-90s for Vercel to auto-deploy (watch https://vercel.com/bantuson)."
Write-Host "6. Share bantuson.vercel.app with your hiring manager."
Write-Host ""
Write-Host "KEEP ALL WINDOWS OPEN during the demo."
Write-Host "Close the TUNNEL WINDOW (or press Ctrl+C in it) to end the session."
Write-Host ""
