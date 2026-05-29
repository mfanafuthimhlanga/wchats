---
phase: 12
slug: production-go-live-deploy-the-w-chats-api-and-celery-workers
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-05-29
---

# Phase 12 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Phase 12 has no formal REQ-IDs — coverage is driven by LOCKED decisions D-01…D-15
> (see 12-CONTEXT.md) and the end-to-end success bar: a real hiring-manager Q&A
> through the public widget on bantuson.vercel.app.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (already in `apps/api` dev extras) |
| **Config file** | `apps/api/pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `cd apps/api && pytest tests/unit/test_agent_task.py -x -q` |
| **Full suite command** | `cd apps/api && pytest tests/ -q` |
| **Estimated runtime** | ~30 seconds (unit only; no live services) |

Note: Most of Phase 12 is infrastructure (VM, systemd, TLS, Vercel publish, ADR)
which is verified by **scripts + manual checkpoints**, not pytest. Only the two
in-repo code changes (D-10 retrieve cap, D-11 wall-clock guard) carry unit tests.

---

## Sampling Rate

- **After every task commit:** Run `cd apps/api && pytest tests/unit/test_agent_task.py -x -q` (the file already exists; plan 01 extends it)
- **After every plan wave:** Run `cd apps/api && pytest tests/ -q`
- **Before `/gsd-verify-work`:** Full suite green + manual E2E Q&A checkpoint passed
- **Max feedback latency:** ~30 seconds (unit); infra checks are script/manual

---

## Per-Task Verification Map

> Task IDs are assigned after planning; rows below are keyed by the LOCKED decision
> each task must satisfy. The planner/executor should backfill the Task ID column.

| Decision | Behavior to verify | Test Type | Automated Command | File Exists | Status |
|----------|--------------------|-----------|-------------------|-------------|--------|
| D-10 | `max_turns=3` (≤2 retrieve calls/turn) in `run_agent_turn` ClaudeAgentOptions | unit | `pytest tests/unit/test_agent_task.py -k max_turns -x` | ✅ exists (extended in 12-01) | ⬜ pending |
| D-11 | `asyncio.wait_for(..., timeout=90)` in `run_agent_turn` | unit | `pytest tests/unit/test_agent_task.py -k timeout -x` | ✅ exists (extended in 12-01) | ⬜ pending |
| D-02 | both systemd services active on VM | script (on VM) | `systemctl is-active wchats-api wchats-celery-runtime` | authored 12-04 / run 12-05 | ⬜ pending |
| D-02 | uvicorn responds locally on VM | script | `curl -sf http://127.0.0.1:8000/health` | run 12-05 | ⬜ pending |
| D-05 | API health reachable over external HTTPS w/ valid cert | script | `curl -sfI https://<api-host>/health` (expect 200) | smoke_vm.sh (12-04) / run 12-06 | ⬜ pending |
| D-06 | widget loader reachable on Vercel | script | `curl -sfI https://bantuson.vercel.app/wchats/widget.js` (expect 200) | smoke_vm.sh (12-04) / run 12-06 | ⬜ pending |
| D-08 | bundle rebuild is byte-stable (pnpm) | script (pre-deploy) | `pnpm --filter veridian-widget build` then diff sizes | n/a | ⬜ pending |
| D-09 | one chat turn completes with no Voyage rate-limit | smoke | observe SSE: `agent.response` received, no `agent.failed` | smoke_vm.sh (12-04) / run 12-06 | ⬜ pending |
| D-14 | no secrets committed in code | grep (existing pattern) | `grep -rn "sk-ant\|voyage-\|postgresql://" apps/api/app/` (expect none) | ✅ | ⬜ pending |
| D-15 | cutover ADR file written | file check | `test -f docs/adr/0001-cloud-native-cutover.md` | authored 12-03 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `apps/api/tests/unit/test_agent_task.py` — ALREADY EXISTS (~527 lines, 8 tests).
      No true Wave 0 scaffold is needed; Wave 1 plan 01 EXTENDS it (per the
      "if the test file already exists, extend it" rule below) with two parametric
      assertions on `ClaudeAgentOptions` construction in `run_agent_turn`:
      `max_turns == 3` (D-10) and the `asyncio.wait_for` timeout `== 90` (D-11).
      The SDK is mocked at the boundary (per [04-03]/[04-07] convention — SDK
      subprocess never spawned). Delivery: extended in Wave 1 (plan 01), not a Wave 0 dependency.
- [x] `scripts/smoke_vm.sh` — curl-based deployment smoke test: API `/health` over
      HTTPS (D-05), `widget.js` reachable on Vercel (D-06), and a single end-to-end
      `POST /widget/{id}/chat` → SSE `agent.response` (D-09). AUTHORED in Wave 1 (plan 04,
      `bash -n` syntax-checked there) and CONSUMED/run-live in Wave 3 (plan 06) — a valid
      author→consume chain, not a true Wave 0 prerequisite. Not a pytest test.

*If the agent task test file already exists, extend it rather than creating a new one.* (It does — see above.)

---

## Manual-Only Verifications

| Behavior | Decision | Why Manual | Test Instructions |
|----------|----------|------------|-------------------|
| VM SSH access | D-01 | VM must exist first; OCI capacity timing is unpredictable | `ssh ubuntu@<vm-ip>` succeeds; `systemctl status` shows both services |
| No mixed-content block | D-05 | Requires a real browser on the https Vercel page | Open bantuson.vercel.app → DevTools console → no mixed-content errors when widget calls the API |
| data-api wiring | D-07 | Requires inspecting live network traffic | Open widget → Network tab → first `/widget/.../config` request targets the HTTPS API host |
| Worker stays warm | D-12 | Latency comparison across two real turns | Send a second chat message; response latency < ~20s (SDK already resident) |
| **Live hiring-manager Q&A (PHASE SUCCESS GATE)** | end-to-end | The whole point — a human asking a real question | Open bantuson.vercel.app; click chat launcher; ask "What is W Chats?" / a Bantuson-portfolio question; receive a **grounded** answer with citations; no error. This is the canonical success criterion. |

---

## Validation Sign-Off

- [x] All in-repo code tasks (D-10, D-11) have `<automated>` verify or Wave 0 dependencies (test file pre-exists; plan 01 extends it with automated unit tests)
- [x] Infra tasks have a script (`smoke_vm.sh`) or an explicit manual checkpoint (smoke_vm.sh authored W1/plan 04, run live W3/plan 06; plus the manual phase-success-gate checkpoint)
- [x] Sampling continuity: no 3 consecutive in-repo code tasks without automated verify (only two in-repo code tasks total, both unit-tested)
- [x] Wave 0 covers `test_agent_task.py` + `smoke_vm.sh` (test file pre-exists/extended W1; smoke script authored W1, consumed W3 — valid author→consume chain, no true Wave 0 gap)
- [x] No watch-mode flags
- [x] Feedback latency < 30s (unit)
- [x] `nyquist_compliant: true` set in frontmatter
- [ ] Live Q&A human gate passed before milestone sign-off

**Approval:** pending (live Q&A human gate is the only remaining sign-off item — runs in plan 06)
