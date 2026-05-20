---
slug: admin-ui-journey-gaps
date: 2026-05-20
status: in-progress
---

# Admin UI Journey Gaps — Closure

Fix all gaps in the M1–M4 user journey so the UI matches what the landing page advertises.
Each task = one atomic commit.

## API contract (pre-researched — do not re-read documents.py)

POST /api/v1/agents/{agent_id}/documents
  - multipart/form-data
  - `files`: list of UploadFile (PDF/PNG/JPG/JPEG only, not DOCX)
  - `urls`: list of str via Form
  - Returns 202: { job_id, document_ids, status, events_url }
  - Requires agent.status == "ready" (returns 409 otherwise)

GET /api/v1/agents/{agent_id}/documents
  - Returns { documents: [{id, source_uri, source_type, title, parse_status, chunk_count, created_at}] }

SSE events (GET /api/v1/jobs/{job_id}/events — requires Bearer token):
  event stream lines: data: {"event": "job.started"|"parsing"|"chunking"|"embedding"|"job.complete"|"job.failed", ...}

Auth: always `Authorization: Bearer {token}` from Clerk getToken()

## Tasks

### C-01: new/page.tsx — poll timeout
- Add `pollCount` ref, increment each tick
- After 60 ticks (120s at 2s interval) clear interval, show timeout error:
  "This is taking longer than expected. Make sure your Celery worker is running:
   celery -A app.worker.celery_app worker -Q pipeline,runtime --loglevel=info"
- Show Retry CTA (onClick calls handleReset)
- The timeout error replaces the provisioning panel content

### C-02: agents/[id]/page.tsx — right panel dispatches on active step
- When step1 is active (not done): right panel shows provisioning status card
  - Display: agent ID in mono, status badge (same STATUS_COLORS map as AgentCard), auto-refresh GET /api/v1/agents/{id} every 3s
  - If status becomes 'ready': clear interval, setAgent(data), page re-derives to step2
  - Message: "Your dedicated database is being provisioned. This may take up to 30 seconds."
- When step1 is done (step2 active): show current configure content (unchanged)

### C-03: agents/[id]/page.tsx — step gating
- Derive `soulSaved = !!(agent?.soul_voice || (agent?.soul_do_list?.length ?? 0) > 0)`
- Soul StepSubtaskCard: `href` only when step1Done, else no href + state="idle" + description shows "Complete provisioning first"
- Ingest StepSubtaskCard: `href` only when soulSaved, else no href + state="idle"
- Eval StepSubtaskCard: always no href, ctaLabel="Available in M6", state="idle"
- Deploy StepSubtaskCard: href only when soulSaved, else no href + state="idle"

### C-04: soul/page.tsx — post-save navigation
- Replace `setTimeout(() => setSaveStatus('idle'), 2000)` with persistent saved state
- When saveStatus === 'saved': show "✓ Soul saved" confirmation + "Next: Upload documents →" Link to /agents/{id}/ingest
- Keep "Save Soul" button active (user can re-save)
- The "Next" CTA is a styled Link using var(--accent) colors

### C-05: ingest/page.tsx — wire M2 backend
Replace stub entirely. New UI:
- Load documents on mount: GET /api/v1/agents/{id}/documents
- Two tabs: "Upload File" | "Add URL"
- Upload File tab: file input (accept=".pdf,.png,.jpg,.jpeg"), submit button, POST as FormData with file in `files` field
- Add URL tab: text input for URL, validate it starts with http/https, submit button, POST as FormData with url in `urls` field (use `formData.append('urls', url)`)
- After POST: show SSE progress via EventSource on the events_url (/api/v1/jobs/{job_id}/events)
  - SSE requires auth header — use fetch+ReadableStream approach (EventSource doesn't support headers)
  - Parse lines: lines starting with "data: " → JSON.parse the remainder
  - Map event names to labels: job.started→"Starting...", parsing→"Parsing documents...", chunking→"Chunking text...", embedding→"Generating embeddings...", job.complete→"Done!", job.failed→"Failed"
- After job.complete: reload document list
- Document list: table/list showing title, source_type badge, parse_status, chunk_count, created_at

### C-06: settings/page.tsx stub
Create apps/admin/app/agents/[id]/settings/page.tsx
- "use client" + use(params) for id
- Simple page: "Settings" heading, "Agent settings coming soon." text, back link to /agents/{id}
- Match styling of eval/page.tsx exactly

### C-07: deploy/page.tsx — CDN note
Under the embed snippet pre block, add a paragraph:
"Note: The CDN URL above is a preview placeholder. Widget CDN deployment is not yet live and will be activated in a future release."
Style: fontSize 12px, color var(--text-4), fontStyle italic, marginTop 8px

### C-08: new/page.tsx — left panel layout (do this LAST, after C-01)
Redesign create-agent page:
- Import JourneyStepper from '../../components/JourneyStepper'
- Outer container: `display: flex, minHeight: calc(100vh - 56px)`
- Left panel: JourneyStepper with agentName="New Agent", agentRole="" and steps:
  - Step 1 Provision: state = phase==='form' ? 'active' : phase==='provisioning' ? 'active' : 'done'
  - Step 2 Configure: state = 'locked'
  - Step 3 Test: state = 'locked'
  - Step 4 Deploy: state = 'locked'
- Right panel: flex:1, padding 32px 40px, contains existing form/provisioning/error content
- Remove the "← Back to dashboard" Link (navigation is now via breadcrumb or TopNav)
- Keep all existing form logic unchanged — only wrap layout

## Commit message template
feat(admin): {description} [C-0N]

## Files to read before editing
- apps/admin/app/agents/new/page.tsx
- apps/admin/app/agents/[id]/page.tsx
- apps/admin/app/agents/[id]/soul/page.tsx
- apps/admin/app/agents/[id]/ingest/page.tsx
- apps/admin/app/agents/[id]/deploy/page.tsx
- apps/admin/app/components/JourneyStepper.tsx (already read — do not re-read)
- apps/admin/app/components/AgentCard.tsx (for STATUS_COLORS pattern)
