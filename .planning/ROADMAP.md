### Phase 12: Production Go-Live: deploy the W Chats API and Celery workers to a public managed host and publish the embeddable widget so a hiring manager can chat with the live agent on bantuson.vercel.app; env and interface driven for a later cloud-native AWS flip

**Goal:** A hiring manager opens bantuson.vercel.app, launches the W Chats widget, and gets a grounded, cited, live answer from the deployed agent (fe230a9d) — served by an always-on $0 Oracle Cloud ARM VM (uvicorn + runtime Celery worker as systemd services) behind Caddy TLS, with a cloud-native AWS cutover ADR for the future flip.
**Requirements:** D-01 through D-15 (CONTEXT.md locked decisions; no formal REQ-IDs mapped to this phase)
**Depends on:** Phase 11
**Plans:** 6 plans, 3 waves

Plans:
**Wave 1**
- [ ] 12-01-PLAN.md — Wave 1: Live-answer hardening (D-09/D-10/D-11/D-13) — max_turns=3 + retrieve-cap prompt + timeout=90 in agent.py, Redis query-embed cache, two regression tests
- [ ] 12-02-PLAN.md — Wave 1: Widget publish (D-06/D-07/D-08) — pnpm bundle freshness + copy embed files to apps/admin/public/wchats/ for Vercel
- [ ] 12-03-PLAN.md — Wave 1: Cloud-native cutover ADR (D-14/D-15) — docs/adr/0001-cloud-native-cutover.md (Nygard, AWS target + trigger threshold)
- [ ] 12-04-PLAN.md — Wave 1: Deploy artifacts in-repo (D-02/D-05) — systemd units, Caddy DuckDNS DNS-01 Caddyfile, deploy/README runbook, scripts/smoke_vm.sh

**Wave 2** *(blocked on Wave 1 completion)*
- [ ] 12-05-PLAN.md — Wave 2: VM provisioning + deploy (D-01/D-02/D-04/D-05/D-12, autonomous:false) — Oracle ARM VM, systemd services, Neon+Upstash reuse, Caddy TLS

**Wave 3** *(blocked on Wave 2 completion)*
- [ ] 12-06-PLAN.md — Wave 3: Final E2E gate (D-05/D-06/D-07/D-09/D-10/D-11/D-12, autonomous:false) — wire live snippet, run smoke_vm.sh, hiring-manager Q&A success gate

---

*Roadmap created: 2026-05-12*
*Last updated: 2026-05-29 — Phase 12 planned: 6 plans / 3 waves. Wave 1 (parallel, VM-independent): 12-01 code hardening, 12-02 widget publish, 12-03 ADR, 12-04 deploy artifacts. Wave 2: 12-05 VM provisioning (autonomous:false). Wave 3: 12-06 final E2E + hiring-manager Q&A gate (autonomous:false). All D-01..D-15 covered.*
