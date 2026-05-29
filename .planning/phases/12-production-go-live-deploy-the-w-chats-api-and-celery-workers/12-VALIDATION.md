---
phase: 12
slug: production-go-live-deploy-the-w-chats-api-and-celery-workers
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-05-29
revised: 2026-05-29
---

# Phase 12 — Validation Strategy (revised: Cloudflare Tunnel pivot)

> Per-phase validation contract. Phase 12 has no formal REQ-IDs — coverage is the
> LOCKED decisions D-01…D-15 (see 12-CONTEXT.md, as amended by `<decision_revision>`).
> **Host pivot:** Oracle VM (D-01/02/05) superseded by **local PC + Cloudflare Tunnel**.
> The success bar is unchanged: a real hiring-manager Q&A through the public widget on
> bantuson.vercel.app → tunnel → local agent, live during a demo window.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (existing) + a bash smoke script (`scripts/smoke_vm.sh`, honors `API_HOST`) |
| **Config file** | `apps/api/pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `cd apps/api && pytest tests/unit/test_agent_task.py -x -q` |
| **Full suite command** | `cd apps/api && pytest tests/ -q` |
| **Live smoke** | `API_HOST=https://<tunnel-url> bash scripts/smoke_vm.sh` (run during a demo window) |

The two in-repo code changes (D-10/D-11/D-13) are unit-tested and already green (12-01).
Everything host-related is verified by the smoke script against the live tunnel + a manual
Q&A gate — there is no VM/systemd to check anymore.

---

## Sampling Rate

- **After every task commit:** `cd apps/api && pytest tests/unit/test_agent_task.py -x -q`
- **After every plan wave:** `cd apps/api && pytest tests/ -q`
- **Phase gate:** live tunnel smoke (`smoke_vm.sh` against the tunnel URL) + manual hiring-manager Q&A, before `/gsd-verify-work`
- **Max feedback latency:** ~30s (unit); live smoke bounded by the 90s turn guard (single `--max-time 95` SSE curl)

---

## Per-Decision Verification Map

| Decision | Behavior to verify | Test Type | Command / Method | Status |
|----------|--------------------|-----------|------------------|--------|
| D-10 | `max_turns=3` in `run_agent_turn` | unit | `pytest tests/unit/test_agent_task.py -k max_turns -x` | ✅ done (12-01) |
| D-11 | `asyncio.wait_for(..., timeout=90)` | unit | `pytest tests/unit/test_agent_task.py -k timeout -x` | ✅ done (12-01) |
| D-13 | query-embed cache key in `agent_tools.py` | source | `grep "qembed:" apps/api/app/services/agent_tools.py` | ✅ done (12-01) |
| D-06 | widget loader reachable on Vercel | smoke | `curl -sfI https://bantuson.vercel.app/wchats/widget.js` (200) | ✅ done (12-02) |
| D-15 | cutover ADR exists | file | `test -f docs/adr/0001-cloud-native-cutover.md` | ✅ done (12-03) |
| D-01R | tunnel up + API reachable over external HTTPS | smoke | `API_HOST=https://<tunnel-url> bash scripts/smoke_vm.sh` §1 | ⬜ replan (12-05) |
| D-02R | local stack launches (uvicorn + runtime worker + cloudflared) | script | `scripts/start_demo.ps1` brings all three up | ⬜ replan (12-05) |
| CORS | `Access-Control-Allow-Origin: *` on widget routes via tunnel | smoke | `curl -I <tunnel-url>/widget/<agent>/config` | ⬜ replan (12-05) |
| SSE | `agent.response` arrives within ~95s (buffered-flush through quick tunnel) | smoke | `smoke_vm.sh` §5 (single `--max-time 95` curl) | ⬜ replan (12-05) |
| D-10 runtime | ≤2 retrieve calls in the live turn | smoke | `smoke_vm.sh` §6 | ⬜ replan (12-06) |
| D-12 | warm worker — 2nd turn < ~20s | manual | send a follow-up message, measure latency | ⬜ replan (12-06) |
| D-14 | no secrets in committed code | grep | `grep -rn "sk-ant\|voyage-\|postgresql://" apps/api/app/` (0) | ✅ existing |
| D-07 | widget `data-api` points at the live tunnel URL | manual/source | the per-session api-base value = current tunnel URL | ⬜ replan (12-06) |
| **End-to-end** | **hiring-manager Q&A live (PHASE SUCCESS GATE)** | manual (human) | open bantuson.vercel.app → chat → ask a Bantuson question → grounded, cited answer, no error | ⬜ replan (12-06) |

*Status: ⬜ pending · ✅ green/done · ❌ red*

---

## Wave 0 Requirements (for the re-plan)

- [ ] `scripts/start_demo.ps1` — launches uvicorn + `runtime` celery worker (solo) + `cloudflared` quick tunnel; prints the tunnel URL. Authored in 12-05. (Analog: existing `scripts/start_native.ps1`.)
- [ ] `scripts/smoke_vm.sh` §5 adaptation — replace the per-poll `--max-time 6` with a single `--max-time 95` SSE curl to accommodate the quick-tunnel buffered-flush behavior. Adapted in 12-05 (or authored as `scripts/smoke_tunnel.sh`).
- [ ] api-base wiring — the widget reads its API base from a per-session-updatable value (the planner locates the `data-api` source in `apps/admin/` via `grep -r "data-agent"`). Wired in 12-06.

*Already satisfied: `apps/api/tests/unit/test_agent_task.py` exists and is green (12-01); `scripts/smoke_vm.sh` exists and honors `API_HOST` (12-04).*

---

## Manual-Only Verifications

| Behavior | Decision | Why Manual | Test Instructions |
|----------|----------|------------|-------------------|
| SSE not severed before 90s | D-11/SSE | quick-tunnel timeout for in-flight streams is undocumented (research A1) | Start `start_demo.ps1`, send a chat message, confirm `agent.response` arrives (no `onerror`). If it fails early, lower D-11 to ~55s or switch to serveo/localhost.run |
| No mixed-content block | D-05R | needs a real browser on the https Vercel page | Open bantuson.vercel.app → DevTools → no mixed-content errors when the widget calls the tunnel |
| Worker stays warm | D-12 | latency comparison across two real turns | second message responds < ~20s |
| **Live hiring-manager Q&A (PHASE SUCCESS GATE)** | end-to-end | the whole point — a human asking a real question | open bantuson.vercel.app, click chat, ask "What is W Chats?" / a Bantuson question, receive a grounded answer with citations, no error |

---

## Validation Sign-Off

- [x] In-repo code tasks (D-10/D-11/D-13) have automated unit verify — green (12-01)
- [x] Host tasks have a script (`smoke_vm.sh` against the tunnel) or an explicit manual checkpoint
- [x] No watch-mode flags
- [x] `nyquist_compliant: true` set in frontmatter (strategy defined; live gates are inherently manual)
- [ ] `scripts/start_demo.ps1` authored (12-05)
- [ ] Live tunnel smoke passes against the real tunnel URL (12-06)
- [ ] SSE-through-tunnel empirically confirmed within the 90s guard (12-05/12-06)
- [ ] Live Q&A human gate passed before milestone sign-off

**Approval:** pending (live tunnel smoke + hiring-manager Q&A are the remaining gates — run in 12-05/12-06 during a demo window)
