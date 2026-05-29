### Phase 12: Production Go-Live: deploy the W Chats API and Celery workers to a public managed host and publish the embeddable widget so a hiring manager can chat with the live agent on bantuson.vercel.app; env and interface driven for a later cloud-native AWS flip

**Goal:** A hiring manager opens bantuson.vercel.app, launches the W Chats widget, and gets a grounded, cited, live answer from the deployed agent (fe230a9d) — served live-on-demand from the user's local Windows PC (uvicorn + runtime Celery worker) exposed over HTTPS via a Cloudflare quick tunnel (cloudflared), on $0/no-card infra, with a cloud-native AWS cutover ADR for the future flip. (Host pivoted from the original Oracle ARM VM + Caddy TLS path — no credit card — see 12-CONTEXT.md decision_revision; the VM/systemd/Caddy artifacts are retained in-repo as the AWS-VM reference.)
**Requirements:** D-01 through D-15 (CONTEXT.md locked decisions, as amended by decision_revision; no formal REQ-IDs mapped to this phase)
**Depends on:** Phase 11
**Plans:** 4/6 plans executed

Plans:
**Wave 1**
- [x] 12-01-PLAN.md — Wave 1: Live-answer hardening (D-09/D-10/D-11/D-13) — max_turns=3 + retrieve-cap prompt + timeout=90 in agent.py, Redis query-embed cache, two regression tests
- [x] 12-02-PLAN.md — Wave 1: Widget publish (D-06/D-07/D-08) — pnpm bundle freshness + copy embed files to apps/admin/public/wchats/ for Vercel
- [x] 12-03-PLAN.md — Wave 1: Cloud-native cutover ADR (D-14/D-15) — docs/adr/0001-cloud-native-cutover.md (Nygard, AWS target + trigger threshold)
- [x] 12-04-PLAN.md — Wave 1: Deploy artifacts in-repo (D-02/D-05) — systemd units, Caddy DuckDNS DNS-01 Caddyfile, deploy/README runbook, scripts/smoke_vm.sh (now the AWS-VM reference paired with ADR 0001; smoke_vm.sh reused for the tunnel)

**Wave 2** *(blocked on Wave 1 completion)*
- [ ] 12-05-PLAN.md — Wave 2: Tunnel bring-up (D-01/D-02/D-04/D-05/D-12, autonomous:false) — author scripts/start_demo.ps1 (uvicorn 0.0.0.0 + runtime worker + cloudflared quick tunnel), adapt smoke_vm.sh §5 for buffered-flush SSE, wire bantuson.vercel.app landing page data-api; then live tunnel up + empirical SSE-survival checkpoint

**Wave 3** *(blocked on Wave 2 completion)*
- [ ] 12-06-PLAN.md — Wave 3: Final live gate (D-05/D-07/D-09/D-10/D-11/D-12, autonomous:false) — set data-api to the real trycloudflare URL + Vercel deploy, run smoke_vm.sh against the tunnel, hiring-manager Q&A success gate

---

*Roadmap created: 2026-05-12*
*Last updated: 2026-05-29 — Phase 12 host pivot (no credit card): Oracle ARM VM + Caddy/DuckDNS TLS (D-01/D-02/D-05) superseded by local Windows PC + Cloudflare quick tunnel. 12-05/12-06 re-planned in place (tunnel bring-up + live gate); 12-01/02/03/04 unchanged. The VM/systemd/Caddy deploy artifacts (12-04) are retained as the AWS-VM reference for ADR 0001. Still 6 plans / 3 waves; all D-01..D-15 (as amended) covered.*
