# start_demo.ps1 — W Chats Demo Mode (local PC + localhost.run SSH tunnel)
#
# Supersedes the Oracle Cloud VM / Caddy / DuckDNS / systemd path documented in
# 12-CONTEXT.md (decision_revision). That path required a credit card; this script
# runs on the user's local Windows PC with zero paid services.
#
# TUNNEL CHOICE — localhost.run, NOT Cloudflare quick tunnel:
#   The widget streams answers over SSE. Cloudflare *quick* tunnels buffer SSE and
#   never flush (verified: 95s curl = 0 bytes; cloudflared#1449). localhost.run
#   (an SSH reverse tunnel) DOES stream SSE incrementally (verified live this phase).
#   So the public URL is now https://<random>.lhr.life, opened via:
#       ssh -R 80:localhost:8000 nokey@localhost.run
#   nokey@ = anonymous (no account/card). accept-new auto-trusts the host key on
#   first use so the window does not hang on an interactive prompt.
#
# What this script does:
#   1. Loads .env into process environment so child windows inherit all secrets
#      (Neon, Upstash, Anthropic, Voyage, JWT, Clerk) — D-04.
#   2. Launches uvicorn app.main:app --host 0.0.0.0 --port 8000 (NO --reload) — D-01/D-02.
#   3. Launches the runtime Celery worker (--pool=solo --concurrency=1
#      --queues=runtime ONLY — no pipeline worker, no beat) — D-02/D-03/D-12.
#   4. Launches the localhost.run SSH tunnel. The tunnel window prints the random
#      https://<name>.lhr.life URL; copy it manually.
#   5. Prints a === Next steps === block for the per-session apiBase update.
#
# Closing the TUNNEL WINDOW ends the public HTTPS session.
# Secrets are loaded into the process environment only — NEVER echoed.
#
# Prerequisite: the Windows OpenSSH client (`ssh`) — bundled with Windows 11 by default.
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
Write-Host "    local PC + localhost.run SSH tunnel (streams SSE)"
Write-Host ""

# ---------------------------------------------------------------------------
# Service 1: uvicorn API (0.0.0.0:8000, no --reload, no --concurrency flag)
# Binds all interfaces so the local SSH tunnel forwards to this port (D-02).
# Cold import on a 4 GB box is ~108-144s — wait for "Application startup complete"
# before testing the public URL.
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
# localhost.run SSH reverse tunnel.
#   -R 80:localhost:8000      forward the public :80 back to local uvicorn :8000
#   nokey@localhost.run       anonymous tunnel (no account, no card)
#   StrictHostKeyChecking=accept-new  trust the host key on first use (no prompt)
#   ServerAliveInterval=30    keep the connection from idling out mid-demo
# The tunnel window prints the https://<random>.lhr.life URL; copy it manually
# (there is no reliable way to capture it from a spawned window).
# ---------------------------------------------------------------------------
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Write-Host ''; Write-Host '=== TUNNEL WINDOW ==='; Write-Host 'Copy the https://*.lhr.life URL from the lines below:'; Write-Host ''; ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -R 80:localhost:8000 nokey@localhost.run"
) -WindowStyle Normal
Write-Host "Started: localhost.run Tunnel (URL will appear in TUNNEL WINDOW)"

# ---------------------------------------------------------------------------
# Next-steps instructions — no secret value is printed here or anywhere above.
# The embed lives in the portfolio-dashboard repo (sibling of veridian); the
# per-session URL goes in wchats/config.json (NOT apps/admin/app/page.tsx).
# ---------------------------------------------------------------------------
$Portfolio = Join-Path (Split-Path $Root -Parent) "portfolio-dashboard"
Write-Host ""
Write-Host "=== Next steps ==="
Write-Host "1. Wait for the API window to log 'Application startup complete' (~2-3 min cold on 4 GB)."
Write-Host "2. Copy the https://*.lhr.life URL from the TUNNEL WINDOW."
Write-Host "3. Set it as apiBase in:  $Portfolio\wchats\config.json   ->  { ""apiBase"": ""https://XXXX.lhr.life"" }"
Write-Host "4. In the portfolio repo:  git add wchats/config.json; git commit -m 'demo: point wchats at live tunnel'; git push"
Write-Host "5. Wait ~60-90s for Vercel to auto-deploy (watch https://vercel.com/bantuson)."
Write-Host "6. Open bantuson.vercel.app, click the launcher, ask: 'What is W Chats and who is Bantuson?'"
Write-Host ""
Write-Host "KEEP ALL WINDOWS OPEN during the demo."
Write-Host "Close the TUNNEL WINDOW (or press Ctrl+C in it) to end the session."
Write-Host ""
