# W Chats — VM Deployment Runbook

Ordered checklist for deploying the W Chats API and runtime Celery worker to an
Oracle Cloud Always Free ARM (aarch64) VM. Plan 05 executes these steps.

---

## Prerequisites

- Oracle Cloud account (credit card required at signup; $1 hold, no ongoing charge for Always Free)
- DuckDNS account (free, no credit card) — register a subdomain, note your token
- Repo cloned locally with all secrets in `.env`

---

## Step A: Provision the VM (OCI A1.Flex, Ubuntu 22.04 aarch64)

1. Log in to the OCI Console → Compute → Instances → Create Instance.
2. Choose shape: **VM.Standard.A1.Flex**, 2 OCPU / 12 GB RAM (from the 4 OCPU / 24 GB Always Free pool).
3. Choose image: **Canonical Ubuntu 22.04 (aarch64 Minimal)**.
4. Upload your SSH public key.
5. Click Create.

**Capacity note:** OCI ARM capacity errors (`Out of host capacity`) are common in US regions.
If the instance fails to provision, use the retry loop:

```bash
# Run as a cron job (every 5 min) until the instance is created:
# https://github.com/hitrov/oci-arm-host-capacity
# EU-Frankfurt-1 and EU-Amsterdam-1 tend to have better availability.
```

6. Once the instance is `RUNNING`, note the **public IP address**.

---

## Step B: Open Port 443 (HTTPS only — port 80 is NOT required)

DNS-01 ACME (used by Caddy + DuckDNS) does not need port 80. Only 443 must be open.

### OCI VCN Security List (cloud firewall)

OCI Console → Networking → Virtual Cloud Networks → your VCN →
Security Lists → Default Security List → Add Ingress Rule:

- Source CIDR: `0.0.0.0/0`
- IP Protocol: TCP
- Destination Port: `443`

**Do NOT open port 80** — DNS-01 never uses it, and keeping it closed reduces attack surface.

### Host-level iptables (Ubuntu on Oracle images blocks everything except SSH by default)

```bash
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo apt install -y iptables-persistent
sudo netfilter-persistent save
```

Verify: `sudo iptables -L INPUT -n | grep 443` — should show ACCEPT.

---

## Step C: Clone Repo + Install Python

```bash
sudo useradd -m -s /bin/bash wchats
sudo mkdir -p /opt/wchats
sudo chown wchats:wchats /opt/wchats

sudo -u wchats bash -c "
  git clone https://github.com/bantuson/wchats.git /opt/wchats/apps/api
  cd /opt/wchats/apps/api
  python3 -m venv /opt/wchats/venv
  /opt/wchats/venv/bin/pip install uv
  /opt/wchats/venv/bin/uv pip install '.[dev]'
"
```

**Note:** `claude-agent-sdk==0.1.81` bundles the aarch64 Claude Code binary — no separate
Node.js install required. The Linux aarch64 wheel is available on PyPI.

---

## Step D: Place the .env File

Copy your local `.env` to the VM. The file must contain all secrets:
`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `NEON_API_KEY`, `NEON_ENCRYPTION_KEY`,
`CONTROL_DB_URL`, `CONTROL_DB_SYNC_URL`, `REDIS_URL` (Upstash TLS URL),
`JWT_SECRET`, `CLERK_WEBHOOK_SIGNING_SECRET`, `CLERK_JWKS_URL`.

```bash
# From your local machine:
scp .env ubuntu@<vm-ip>:/tmp/app.env
ssh ubuntu@<vm-ip> "sudo mv /tmp/app.env /opt/wchats/apps/api/.env && sudo chown wchats:wchats /opt/wchats/apps/api/.env && sudo chmod 600 /opt/wchats/apps/api/.env"
```

Verify: `sudo -u wchats stat /opt/wchats/apps/api/.env` — permissions must be `600`.

**DUCKDNS_TOKEN is NOT placed in .env** — it goes in Caddy's systemd drop-in (Step F).

---

## Step E: Install and Enable the Two systemd Units

```bash
sudo cp /opt/wchats/apps/api/deploy/systemd/wchats-api.service /etc/systemd/system/
sudo cp /opt/wchats/apps/api/deploy/systemd/wchats-celery-runtime.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wchats-api wchats-celery-runtime
```

Verify:

```bash
systemctl is-active wchats-api         # should print: active
systemctl is-active wchats-celery-runtime  # should print: active
curl http://127.0.0.1:8000/health      # should return: {"status":"ok"}
```

If a service fails, inspect logs: `journalctl -u wchats-api -n 50 --no-pager`

---

## Step F: Install Caddy with the DuckDNS Plugin + Configure TLS

### 1. Register your DuckDNS subdomain

Go to https://www.duckdns.org — log in, create a subdomain (e.g. `wchats-api`),
and point it to the VM public IP. Note your **DuckDNS token**.

### 2. Update the Caddyfile subdomain

Edit `deploy/caddy/Caddyfile` (in the repo) and replace `wchats-api.duckdns.org`
with your actual subdomain. Commit the change, then `git pull` on the VM.

### 3. Build Caddy with the DuckDNS DNS provider plugin

Standard `apt install caddy` does NOT include the DuckDNS plugin. Build it with xcaddy:

```bash
# Download xcaddy (Go binary, ARM64 pre-built)
curl -fsSL https://github.com/caddyserver/xcaddy/releases/latest/download/xcaddy_linux_arm64.tar.gz \
    | sudo tar -xz -C /usr/local/bin xcaddy

# Build Caddy with the DuckDNS DNS provider
xcaddy build --with github.com/caddy-dns/duckdns --output /usr/local/bin/caddy

# Verify the plugin is included
caddy list-modules | grep duckdns
```

Alternatively, visit https://caddyserver.com/download, select the
`github.com/caddy-dns/duckdns` DNS provider, and download the ARM64 binary.

### 4. Install the Caddyfile

```bash
sudo mkdir -p /etc/caddy
sudo cp /opt/wchats/apps/api/deploy/caddy/Caddyfile /etc/caddy/Caddyfile
```

### 5. Set DUCKDNS_TOKEN in Caddy's systemd environment

The token goes in Caddy's own drop-in, NOT the app `.env`:

```bash
sudo mkdir -p /etc/systemd/system/caddy.service.d
sudo tee /etc/systemd/system/caddy.service.d/override.conf <<EOF
[Service]
Environment=DUCKDNS_TOKEN=<your-duckdns-token>
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now caddy
```

### 6. Verify TLS

```bash
# From the VM (loopback):
curl http://127.0.0.1:8000/health

# From an external machine or your local terminal:
curl -I https://wchats-api.duckdns.org/health
# Expected: HTTP/2 200 with a valid Let's Encrypt certificate
```

**Caddy auto-renews the TLS certificate via DuckDNS DNS-01.** No cron job needed.

---

## Step G: Verification Commands

```bash
# Systemd service status
systemctl is-active wchats-api
systemctl is-active wchats-celery-runtime

# API health (internal)
curl http://127.0.0.1:8000/health

# API health (external, validates TLS)
curl -I https://wchats-api.duckdns.org/health

# Run deployment smoke test (plan 06 gate — runs against live host)
API_HOST=https://wchats-api.duckdns.org bash /opt/wchats/apps/api/scripts/smoke_vm.sh
```

---

## Security Notes

- Port 80 is NOT open. DNS-01 ACME never needs it.
- Port 443 only: OCI Security List + host iptables.
- uvicorn binds `127.0.0.1` only — never `0.0.0.0`. Caddy is the only public listener.
- `.env` is `chmod 600 wchats:wchats`. Not checked into git.
- `DUCKDNS_TOKEN` is in Caddy's drop-in, isolated from app secrets.
- systemd units use `EnvironmentFile=` only — no secret values are inlined in unit files.
- The pipeline Celery worker is NOT hosted (D-03). Ingestion runs locally on-demand.
