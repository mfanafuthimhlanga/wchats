# Phase 20: Frontend cutover — Gotham console — Context

**Gathered:** 2026-07-15
**Status:** Ready for planning
**Source:** Design-session checkpoint (`.planning/.continue-here.md`) + ROADMAP v1.2 + `prototypes/gotham/`

<domain>
## Phase Boundary

Phase 20 is a **pure frontend re-skin + re-information-architecture** of `apps/admin`. It retires the "dusk / Hillbrow-at-dusk" skyline+glass design (Phase 11) and replaces it with the **Gotham "Bone on Graphite"** design system already built as static prototypes in `prototypes/gotham/`.

**IN SCOPE:** token replacement in `globals.css`; landing + provisioning + operations-room pages rebuilt as routed Next.js pages; three.js specimen (landing/auth only); widget preview kept; deletion of dusk/skyline/amber-console styles; a11y + reduced-motion + no-horizontal-overflow parity.

**OUT OF SCOPE (Phase 21):** any new backend/table/endpoint. The operations room is stood up with **honest empty states** for regions Phase 21 will back with real data. Do NOT invent backend calls.

**NON-NEGOTIABLE:** the working provisioning flow (create → provision tenant DB → ingest → deploy) and every live endpoint the dusk pages already call must NOT regress.
</domain>

<decisions>
## Implementation Decisions (LOCKED)

### Design system
- Canonical design contract = `prototypes/gotham/` (11 HTML pages + `tokens.css` + `app.css` + `scene.js` + `MESH.md`). Port these; do not redesign.
- Palette name: "Bone on Graphite" (graphite base, bone chrome). The four `--ch-1..4` channel luminances + the `data-gate` shutter mechanism live in `tokens.css` — port them into `apps/admin/app/globals.css`.
- "Colour is a verdict": green = pass, red = fail/gate-shut are the ONLY hues. Eval channels are values of bone by luminance; red appears only on failure. Colour is never decoration.

### Information architecture
- **Routed Next.js pages, NOT a single-surface `console.html` fold.** (Open question resolved by ROADMAP UI2-02..05.) `console.html` was an exploration; the shipped IA is routed pages.
- Provisioning and operations are DIFFERENT interfaces. `agent-new` = provisioning wizard (steps 2–4 locked until step 1 done — a lifecycle with an END). The agent operations room = a flywheel with no end (live perf, retrieval health, failure-triage, judgement, adversary, prompt versions).
- three.js confined to landing + auth only (design law).

### Anti-patterns (discovered through failure — do not repeat)
- Nested `<a>` inside a card `<a>` wrapper: the browser ejects the inner anchor. Use `<span>`/`<button>` for card actions.
- After ANY token rename, grep the old token repo-wide. CSS fails SILENTLY on undefined vars (white-on-white). OUTSTANDING: a repo-wide `--brass-*` audit was never done, and "brass armature" prose remains in `gotham/MESH.md:46` — check before/at cutover.
- Never put decoration in an artifact's functional slot (e.g. a decorative orb in the live-prompt slot).
- Never write UI copy that explains the design's own metaphor to the user.

### Copy
- Product copy voice is neutral/literal (verified / check), not a themed metaphor.
</decisions>

<canonical_refs>
## Canonical References (MUST read before planning/implementing)

### Design contract (the thing being ported)
- `prototypes/gotham/MESH.md` — design language + 10-system lineage
- `prototypes/gotham/tokens.css` — canonical token values (Bone on Graphite, `--ch-1..4`, `data-gate`)
- `prototypes/gotham/app.css` — component styles
- `prototypes/gotham/scene.js` — three.js specimen + physical gate shutter (`window.gotham` / `mountGotham` API)
- `prototypes/gotham/index.html` — landing
- `prototypes/gotham/agents.html` — agents dashboard
- `prototypes/gotham/agent-new.html` — provisioning wizard
- `prototypes/gotham/agent.html` — operations room (six regions)
- `prototypes/gotham/{soul,ingest,eval,deploy,settings,console}.html`

### Target (current dusk-skin admin to re-skin/re-IA)
- `apps/admin/app/` — `globals.css`, `layout.tsx`, `page.tsx`, `agents/`, `agents/[id]/{deploy,eval,ingest,settings,soul}`, `agents/new`, `components/`, `sign-in`, `sign-up`
- Current routes to map: `/` (landing), `/agents`, `/agents/[id]` (+ tabs deploy/eval/ingest/settings/soul), `/agents/new`, `/sign-in`, `/sign-up`

### Scope + rules
- `.planning/ROADMAP.md` Phase 20 (UI2-01..08 + success criteria)
- `.planning/AGENT-MGMT-GAPS.md` — which operations-room regions are backed vs empty (drives honest empty states)
- `.claude/skills/wchats-design/` — design system source (assets, ui_kits)
- `CLAUDE.md` — project rules (no Docker; polished UI required)
- `.planning/.continue-here.md` — the design-session decisions
</canonical_refs>

<specifics>
## Specific Ideas
- `agent.html` ops-room mechanisms drawn from: VITALS (live perf), DARKROOM+TERRARIUM (failure triage), ORRERY (eval provenance ledger), WARDEN (adversary), MERIDIAN (prompt versions). These are UI regions in Phase 20; their backends are Phase 21 (OPS-01..16) and render as honest empty states here.
- Widget preview (Preact, <20 KB gzipped) stays embedded in the console.
</specifics>

<deferred>
## Deferred Ideas
- `console.html` single-surface fold (explored, NOT chosen for the cutover — routed pages win).
- All Phase 21 backends (turn_metrics, retrieval_metrics, traces, red-team programme tables, prompt_versions, new endpoints).
- Deleting the sibling `../design` firn template #11 (housekeeping, not wchats).
- Removing the empty locked `prototypes/assay/` dir (cosmetic).
</deferred>

---
*Phase: 20-frontend-cutover*
*Context: design-session checkpoint + ROADMAP v1.2, 2026-07-15*
