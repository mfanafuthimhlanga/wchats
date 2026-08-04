# API Coverage — W Chats agent-operations API (Phase 21 surface)

> Full coverage by default. Opt-outs are explicit, reasoned decisions.

**Why this document exists, stated honestly.** The `api-coverage` gate fired at
`verify:pre` on a keyword signal — the verbs *wire* / *wiring* co-occurring with the noun
*endpoints* in this phase's title and scope. That detector is built for **external**
third-party API integrations, and this is not one: every endpoint below is first-party,
shipped by Phase 21 in this same repository, and covered by `21-SECURITY.md`'s 33/33
threat closure. On the detector's own terms this is a false positive.

The matrix was produced anyway, and not as a formality. The gate's stated purpose is to
make the API surface *visible and decided* rather than letting "we integrated the API"
silently mean "we integrated whatever the first use case exercised." That is a precise
description of the defect this phase exists to fix: Phase 21 shipped sixteen routes,
Phase 20 shipped a console, and a grep for six of those route paths across all of
`apps/admin` and `apps/widget` returned **zero files**. Nobody decided the gaps were
acceptable, because nobody enumerated them. Enumerating them is the right artifact here
even though the trigger that demanded it misfired.

**Surface:** the 16 routes in `apps/api/app/api/v1/{metrics,traces,prompt_versions,red_team,evals}.py`
plus the widget feedback route. Decisions below were derived by grepping the actual call
sites in `apps/admin/app` and `apps/widget/src`, not from the plans.

| capability | decision | reason |
|---|---|---|
| `GET /agents/{id}/metrics` | INTEGRATE | |
| `GET /agents/{id}/retrieval-health` | INTEGRATE | |
| `GET /agents/{id}/traces` | INTEGRATE | |
| `POST /agents/{id}/traces/{trace_id}/grade` | INTEGRATE | |
| `GET /agents/{id}/prompt-versions` | INTEGRATE | |
| `GET /agents/{id}/prompt-versions/diff` | INTEGRATE | |
| `POST /agents/{id}/prompt-versions/canary` | INTEGRATE | |
| `POST /agents/{id}/prompt-versions/rollback` | INTEGRATE | |
| `GET /agents/{id}/red-team-runs` | INTEGRATE | |
| `POST /agents/{id}/red-team-runs` | INTEGRATE | |
| `GET /agents/{id}/red-team-runs/{run_id}` | OPT-OUT | nothing in the console reads a single historical run in detail; the Adversary region reads live open_findings instead. A per-run view would reintroduce the frozen-verdict path 23-06 fixed. |
| `GET /agents/{id}/red-team/programme` | INTEGRATE | |
| `POST /agents/{id}/red-team/findings/{finding_id}/contain` | INTEGRATE | |
| `GET /agents/{id}/eval-runs` | INTEGRATE | |
| `GET /agents/{id}/eval-runs/{run_id}/results` | INTEGRATE | |
| `POST /agents/{id}/eval-runs/trigger` | INTEGRATE | |
| `POST /widget/agents/{id}/feedback` | INTEGRATE | |

**16 INTEGRATE · 1 OPT-OUT.**

### Note on the single OPT-OUT

`GET /agents/{id}/red-team-runs/{run_id}` returns one historical red-team run in full.
Nothing in the console calls it (grep for `red-team-runs/${...}` across `apps/admin/app`
returns nothing), and that is deliberate rather than an oversight.

The Adversary region deliberately does **not** read per-run state. Until 23-06 the deploy
gate derived `redTeamBlocked` and its severity counts from a frozen per-run JSONB snapshot
that a `contain` action never updated — so containing a finding left the gate either stuck
blocked or falsely cleared. `23-06` replaced that with `isGateBlocked(openFindings)`
computed fresh from the live `/red-team/programme` read (`page.tsx:384`). Integrating a
per-run detail view now would put the superseded read path back into the console, next to
the live one, with no rule saying which wins.

If a run-history drill-down is genuinely wanted later, the right shape is a read-only
historical view that is explicitly barred from feeding the gate — a decision for the phase
that builds it, recorded here so the absence reads as a choice rather than a gap.

---

## Call-site evidence

Each INTEGRATE row was confirmed against a real call site, not a plan claim:

| capability | call site |
|---|---|
| `/metrics` | `app/agents/[id]/components/LivePanel.tsx` |
| `/retrieval-health` | `app/agents/[id]/components/RetrievalHealthPanel.tsx` |
| `/traces`, `/traces/{id}/grade` | `app/agents/[id]/components/BenchPane.tsx` |
| all four `/prompt-versions*` | `app/agents/[id]/components/PromptVersionPanel.tsx` |
| `GET /red-team-runs` | `app/agents/[id]/page.tsx:310` |
| `POST /red-team-runs` | `app/agents/[id]/page.tsx:339` |
| `/red-team/programme`, `/red-team/findings/{id}/contain` | `app/agents/[id]/components/AdversaryPanel.tsx` |
| `/eval-runs`, `/eval-runs/{id}/results` | `app/agents/[id]/page.tsx` (Judgement ledger, WIRE-02) |
| `POST /eval-runs/trigger` | `app/agents/[id]/eval/page.tsx` |
| `POST /widget/.../feedback` | `apps/widget/src/api.js:20` |

The standing gate `apps/admin/scripts/check-ops-room-wiring.mjs` enforces the
region-level half of this table permanently (11/11, exit 0) — it is the milestone
audit's own grep made into a gate, so a future phase cannot silently un-wire a region.

---

## Partial coverage within an integrated capability

One case where the route is integrated but its payload is not fully surfaced, recorded
rather than left for a later audit to rediscover:

- **`GET /agents/{id}/red-team/programme`** returns `probe_message` and `agent_response`
  on each open finding. Both are typed on `OpenFinding` in `AdversaryPanel.tsx` but
  neither is rendered, so an operator currently cannot read what the adversarial probe
  actually said or how the agent replied. This was surfaced by the Phase 23 security
  audit (see `23-SECURITY.md`, Non-blocking observation 2). It makes threats
  `T-23-ADV-04` and `T-23-ADV-05` trivially closed — there is no injection or disclosure
  risk in text that never reaches the DOM — but it is a product gap, not a design
  intent. A follow-up phase should decide whether the transcript belongs on screen.

## Out of scope for this matrix

Routes outside the Phase 21 operations surface — `/documents`, `/ingest`, `/soul`,
`/widget-config`, `/alerts`, `/checklist-runs`, `/approve-deployment`,
`/capability-envelopes`, `/pending-confirmations` — belong to Phases 11, 12, 18 and 22
and were already reachable before this phase. They are not part of the surface this gate
fired on and are not enumerated here.
