# W Chats — End-to-End Test & Resume Notes

> **Purpose:** Snapshot of the full backend provision → deploy E2E test run on **2026-05-29**, so you can return later and continue where we left off — specifically once **(a)** the Voyage rate-limit blocker is gone and **(b)** the full deployment pipeline (Phase 12) is in place.
>
> Companion artifacts: `.planning/phases/12-production-go-live-…/12-CONTEXT.md` (the deployment plan decisions) and the auto-memory `project_portfolio_agent_e2e.md`.

---

## 1. What was tested

Drove the entire backend workflow **from the command line** against the real cloud infra (Neon control DB sa-east-1 + Upstash Redis), with services running locally (uvicorn + one Celery worker on `pipeline,runtime`, solo pool):

`provision tenant → create agent → set soul → ingest (URL + 4 READMEs) → embed → strategy → eval suite → deployment checklist → acknowledge warnings → approve → embed snippet → live chat smoke test`

**Goal of the agent:** a portfolio assistant that tells hiring managers about Bantuson (Mfanafuthi Mhlanga), grounded in his real projects.

---

## 2. Artifacts created (reusable — already live in the control DB / Neon)

| Thing | Value |
|---|---|
| Tenant | "Bantuson Portfolio" — `2ac7488b-a612-4851-b1b0-5469e46f954a` |
| **Tenant API key** | in `apps/api/_runlogs/state.env` (**SECRET — keep out of git**; plaintext shown only at creation) |
| **Agent** | "bantuson portfolio assistant" — `fe230a9d-09f0-4043-b2f1-4506a2ef0059` |
| Neon project | `nameless-fog-19651218` |
| Corpus | 5 docs (https://bantuson.vercel.app/ + 4 Downloads READMEs: salga-trust-engine, w-chats-marketplace, one-for-all, whatsup-voice) → **195 chunks, 195 embeddings, 263 entities** |
| Eval run | `a7af2018-1b3b-4429-8f53-3076d355f6d9` — faithfulness **0.978**, answer_relevancy **0.965**, context_precision **0.92** |
| Checklist run | `6c7e9905-ae1d-4e6c-bddd-21943d47668e` — **ship_with_warnings** (3 warnings acknowledged) |
| Deployment | `is_deployed = True` |
| Embed snippet emitted | `<script src="https://widget.veridian.app/widget.js" data-agent="fe230a9d-…" async></script>` — ⚠️ placeholder domain, NOT live (see §5) |

State file with IDs + API key: **`apps/api/_runlogs/state.env`**

---

## 3. Blockers found (the real value of the test)

1. **Voyage AI free tier = 3 RPM / 10K TPM** (no payment method). Aborted bulk-embedding the large URL doc (`RateLimitError`) and throttles the live `retrieve` tool → customer-agent turns **time out / fail**. Volume is fine (200M free voyage-3 tokens); only the **rate** is capped.
   - *Worked around in the test* by a throttled batch embedder (`apps/api/_runlogs/throttled_embed.py`).
   - *Resume fix (free):* cap retrieves-per-turn + raise the turn guard (Phase 12 D-10/D-11), **or** add a Voyage payment method.

2. **M6 eval harness broken** — `apps/api/app/services/eval_service.py::run_ragas_eval` is incompatible with installed `ragas 0.4.3` + `claude-haiku-4-5`:
   - collections metrics can't be passed to the legacy `evaluate()`;
   - `AnswerRelevancy` now requires an `embeddings` arg;
   - collections call `agenerate()` → need an **async** client (code passes sync `anthropic.Anthropic()`);
   - the model rejects `temperature` + `top_p` together (instructor sends both).
   - *Worked around* by a direct-Anthropic LLM-judge: `apps/api/_runlogs/run_eval_prod.py`.

3. **M8 checklist eval query wrong columns** — `deployment_service._fetch_eval_summary_sync` selects `metric_name`/`run_id`, but `eval_results` columns are `metric`/`eval_run_id`. Swallowed by the task try/except → eval signal silently dropped.

4. **M8 checklist orchestrator never produces a report** — `deployment_service._run_orchestrator_loop` uses `ClaudeSDKClient` + a `submit_report` tool, but the Claude Agent SDK never emits tool_use blocks (same bug fixed in M9 for `strategy_service`). The real checklist task always fails.
   - *Worked around* by a direct-Anthropic tool_use orchestrator: `apps/api/_runlogs/run_checklist_prod.py`.

➡️ **#2, #3, #4 should be fixed via `/gsd-debug`** (they are code/version-drift bugs, separate from Phase 12).

---

## 4. Corrected harnesses (in `apps/api/_runlogs/` — throwaway, not app source)

Run from repo root with env loaded (`set -a; source ./.env; set +a`) and `PYTHONPATH=apps/api`:

| Script | What it does |
|---|---|
| `throttled_embed.py` | Embeds any chunks missing embeddings in small token-budgeted batches ~35s apart (beats Voyage 3 RPM). |
| `run_eval_prod.py` | Direct-Haiku LLM-judge eval (faithfulness / answer_relevancy / context_precision) over `eval_scenarios`; persists `eval_runs` + `eval_results` to production. |
| `run_checklist_prod.py` | Gathers real signals + runs the deployment-readiness orchestrator via **direct Anthropic tool_use**; persists a `checklist_run`. |
| `state.env` | Saved IDs + tenant API key (**secret**). |

> Note: `gsd-sdk` global shim was repaired this session (npx cache was wiped) to delegate to `~/.claude/get-shit-done/bin/gsd-tools.cjs` v1.41.2. Originals backed up `*.broken.bak`.

---

## 5. Why it isn't "live" yet (the gap to close)

- The emitted snippet points at **`widget.veridian.app`**, which **isn't deployed**.
- The **real** widget is the iframe form at `apps/widget/embed/` (drafted this session: `widget.js` loader + `index.html` host + `widget.iife.js` + `widget.css` + README). It reads `?agent_id=&api=`.
- For a browser on `bantuson.vercel.app` to reach the agent, the **API must be public over HTTPS** (it's `localhost:8000` now). Widget routes already send `Access-Control-Allow-Origin: *`, so the Vercel origin is fine.
- The live agent additionally needs the **Voyage rate limit** addressed (blocker #1).

---

## 6. How to RESUME (come back here later)

### A. Restart the local stack (no Docker — native processes)
```
# Redis = Upstash (remote), DB = Neon (remote) — already in .env
# Terminal 1 (API):
cd apps/api && uvicorn app.main:app --reload --port 8000
# Terminal 2 (worker, both queues, solo pool for Windows):
cd apps/api && celery -A app.worker.celery_app worker -Q pipeline,runtime --pool=solo --loglevel=info
# (or: pwsh scripts/start_native.ps1)
```

### B. Re-verify the agent is still there
```
source apps/api/_runlogs/state.env   # loads API_KEY, AGENT_ID, TENANT_ID
curl -s http://localhost:8000/api/v1/agents/$AGENT_ID -H "X-API-Key: $API_KEY" | jq '{name,status,is_deployed:.is_deployed,neon_project_id}'
```

### C. Live chat smoke test (the thing that currently fails on Voyage rate limit)
```
JWT=$(curl -s http://localhost:8000/widget/$AGENT_ID/config | jq -r .jwt)
curl -s -X POST http://localhost:8000/widget/$AGENT_ID/chat \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"message":"Why should I hire Bantuson for a senior AI/ML role?"}'   # → job_id
curl -s "http://localhost:8000/api/v1/jobs/<job_id>" -H "X-API-Key: $API_KEY" | jq '.events[]|{event_type,text:.payload.text}'
```
**Expected once unblocked:** a grounded `agent.response` with non-empty text (today it returns empty/`agent.failed` due to Voyage 3 RPM + the 30s turn guard).

### D. Re-run eval / checklist if needed
```
set -a; source ./.env; set +a
PYTHONPATH=apps/api python apps/api/_runlogs/run_eval_prod.py
PYTHONPATH=apps/api python apps/api/_runlogs/run_checklist_prod.py
```

---

## 7. Continue where we left off — two threads

**Thread 1 — "no Voyage blockers":**
Either add a Voyage payment method (lifts 3 RPM; still free on volume) **or** implement Phase 12's free fix: cap retrieves-per-turn in `run_agent_turn` + raise the wall-clock guard 30s→~90s + keep the runtime worker warm. Then §6.C should return a real answer.

**Thread 2 — "full deployment pipeline":**
Execute **Phase 12: Production Go-Live** — see `.planning/phases/12-production-go-live-deploy-the-w-chats-api-and-celery-workers/12-CONTEXT.md`. Summary: host API + runtime worker on an **Oracle Cloud Always Free VM** (systemd, HTTPS), publish `apps/widget/embed/` to Vercel `public/wchats/`, wire the snippet's `data-api` to the VM URL, verify a live hiring-manager Q&A. Plus a cutover ADR for the later cloud-native AWS flip.
```
/gsd-plan-phase 12   →   /gsd-execute-phase 12   →   /gsd-verify-work 12
```

**Also queue:** `/gsd-debug` for the M6/M8 bugs (§3 #2–#4) so the in-app eval + checklist work without the `_runlogs/` harnesses.

---

*Saved 2026-05-29. Agent `fe230a9d-09f0-4043-b2f1-4506a2ef0059` is provisioned, ingested, evaluated, and approved — the only thing standing between it and "live on bantuson.vercel.app" is the Voyage rate limit + public hosting (Phase 12).*
