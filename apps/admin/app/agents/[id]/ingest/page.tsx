'use client'
import Link from 'next/link'
import { useState, useRef, useCallback, useEffect, use } from 'react'
import { useAuth } from '@clerk/nextjs'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import DocumentDetailModal from './DocumentDetailModal'
import Chip, { type ChipVerdict } from '../../../components/gotham/Chip'
import Ledger, { LedgerColHead, LedgerRowHead } from '../../../components/gotham/Ledger'
import EmptyState from '../../../components/gotham/EmptyState'
import { DocIcon } from '../../../components/gotham/icons'

/**
 * Ingest — `/agents/[id]/ingest` (UI-SPEC S6.6, UI2-04, ported from
 * prototypes/gotham/ingest.html). Two-tab panel (Upload file / Add URL) +
 * the knowledge-base `.ledger`.
 *
 * PRESERVED VERBATIM (non-regression, UI-SPEC S9): the real
 * `POST /api/v1/agents/{id}/documents` upload flow and the SSE progress
 * stream (`readSseProgress`) the previous dusk build already consumed. The
 * HIVE chunk swarm below decorates that real state — it is not a
 * replacement for it, and it does not run ingest.html's client-only
 * `chunksFor()` hash simulation.
 *
 * Colour fix (must-fix 2 / UI-SPEC S10 anti-pattern 3): the prototype
 * hardcodes the swarm dot fill to the retired brass gold literal
 * (ingest.html:442). This port resolves the dot fill to the CURRENT
 * `--live` bone value via `getComputedStyle` at draw time instead — see
 * `ChunkSwarm` below.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Document {
  id: string
  source_uri: string
  source_type: string
  title: string
  parse_status: string
  chunk_count: number
  created_at: string
}

type IngestTab = 'file' | 'url'

// A locally-tracked, in-flight document shown optimistically in the KB list
// while the ingestion job runs. Replaced by the real row once the job completes
// and the documents query refetches.
interface OptimisticDoc {
  // Stable client key — never collides with a real backend UUID
  clientKey: string
  source_type: string
  title: string
}

const ACCEPTED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.md']

const EVENT_LABELS: Record<string, string> = {
  'ingestion.started': 'Starting ingestion...',
  'parsing.started': 'Parsing document...',
  'parsing.complete': 'Document parsed',
  'chunking.started': 'Splitting into chunks...',
  'chunking.complete': 'Chunks ready',
  'metadata.started': 'Extracting metadata & entities...',
  'metadata.progress': 'Extracting metadata & entities...',
  'metadata.complete': 'Metadata extracted',
  'embedding.started': 'Generating embeddings...',
  'embedding.complete': 'Embeddings done',
  'strategy.synthesized': 'Optimising retrieval strategy...',
  'ingestion.complete': 'Processing complete',
  'job.complete': 'Done!',
  'job.failed': 'Failed',
}

// Colour is a verdict (UI-SPEC S8): map the raw backend parse_status onto
// the closed Chip verdict union instead of a raw hex/bg pair. There is no
// amber/warning tier in Gotham — "pending"/"processing" map to "live"
// (brightness, not a hue), not the dusk build's gold.
function parseStatusVerdict(status: string): ChipVerdict {
  if (status === 'complete' || status === 'parsed') return 'pass'
  if (status === 'pending' || status === 'processing') return 'live'
  if (status === 'failed') return 'seal'
  return 'mute'
}

const STATUS_LABEL: Partial<Record<ChipVerdict, string>> = {
  pass: 'Parsed',
  live: 'Processing',
  seal: 'Failed',
}

// SSE stream is aborted after this long. URL ingestion with Haiku extraction can
// run for several minutes, so the cap is generous; on abort the form is reset to
// usable and a neutral "check back" message is shown (the job may still finish
// server-side). See readSseProgress.
const SSE_TIMEOUT_MS = 600_000 // 10 min

// Render an elapsed-seconds counter as "Xs" or "Xm Xs" so the user can see the
// job is still alive during long ingests.
function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m ${s}s`
}

// ---------------------------------------------------------------------------
// prefers-reduced-motion — drives the swarm's settled-vs-animated branch
// (UI-SPEC S6.6, S8.1).
// ---------------------------------------------------------------------------

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    setReduced(mq.matches)
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])
  return reduced
}

// ---------------------------------------------------------------------------
// THE CHUNK SWARM (HIVE) — ported from prototypes/gotham/ingest.html
// lines 406-511. Forty dots stream from off-canvas to a golden-angle-spiral
// cluster at a CAPPED speed (SWARM_MAX_SPEED) — the cap is explicitly the
// point (HIVE's "dispatch births a centroid" language) and must not be
// raised for perceived performance.
// ---------------------------------------------------------------------------

const SWARM_N = 40
const SWARM_W = 170
const SWARM_H = 56
const SWARM_CX = 118
const SWARM_CY = 28
const SWARM_MAX_SPEED = 2.6 // px per 60Hz frame. The cap is the point.
const SWARM_MAX_FORCE = 0.24
const SWARM_SLOW_R = 15
const SWARM_RUN_MS = 1800

// A golden-angle spiral packs the cluster tight and evenly.
function swarmSlot(i: number): { x: number; y: number } {
  const a = i * 2.399963
  const r = 2.4 + 1.5 * Math.sqrt(i)
  return { x: SWARM_CX + r * Math.cos(a), y: SWARM_CY + r * Math.sin(a) }
}

interface SwarmDot {
  el: SVGCircleElement
  x: number
  y: number
  vx: number
  vy: number
  tx: number
  ty: number
}

function ChunkSwarm({ reducedMotion }: { reducedMotion: boolean }) {
  const svgRef = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return

    // Resolve the CURRENT --live bone value at draw time — CSS custom
    // properties do not resolve inside raw SVG presentation attributes, so a
    // JS read is required either way; the fix (must-fix 2) is which value
    // gets read (the live token), not the technique (a literal attribute).
    const liveColor =
      getComputedStyle(document.documentElement).getPropertyValue('--live').trim() || '#E7E5E1'

    while (svg.firstChild) svg.removeChild(svg.firstChild)

    const NS = 'http://www.w3.org/2000/svg'
    const dots: SwarmDot[] = []

    for (let i = 0; i < SWARM_N; i++) {
      const s = swarmSlot(i)
      const c = document.createElementNS(NS, 'circle')
      c.setAttribute('r', '1.7')
      c.setAttribute('fill', liveColor)
      c.setAttribute('fill-opacity', '0.9')
      svg.appendChild(c)

      let x = -8 - Math.random() * 142 // staggered off the left edge
      let y = 8 + Math.random() * 40
      if (reducedMotion) {
        x = s.x
        y = s.y
      }
      c.setAttribute('cx', x.toFixed(2))
      c.setAttribute('cy', y.toFixed(2))

      dots.push({
        el: c,
        x,
        y,
        vx: 1.4 + Math.random() * 0.8,
        vy: (Math.random() - 0.5) * 0.9,
        tx: s.x,
        ty: s.y,
      })
    }

    // prefers-reduced-motion: dots render already-settled, no animation loop
    // (UI-SPEC S6.6, S8.1) — matches ingest.html's stream() calling done()
    // immediately instead of starting the requestAnimationFrame loop.
    if (reducedMotion) return

    let raf = 0
    const t0 = performance.now()
    let last = t0

    function frame(now: number) {
      const dt = Math.min((now - last) / 16.667, 2.2) // frame-rate independent
      last = now

      for (const d of dots) {
        const dx = d.tx - d.x
        const dy = d.ty - d.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.0001

        // arrival: full speed until the slowing radius, then ease into the slot
        const speed = dist < SWARM_SLOW_R ? SWARM_MAX_SPEED * (dist / SWARM_SLOW_R) : SWARM_MAX_SPEED
        const desx = (dx / dist) * speed
        const desy = (dy / dist) * speed

        let sx = desx - d.vx
        let sy = desy - d.vy
        const smag = Math.sqrt(sx * sx + sy * sy)
        if (smag > SWARM_MAX_FORCE) {
          sx = (sx / smag) * SWARM_MAX_FORCE
          sy = (sy / smag) * SWARM_MAX_FORCE
        }

        d.vx += sx * dt
        d.vy += sy * dt

        // the cap: nothing in this swarm moves faster than this
        const vmag = Math.sqrt(d.vx * d.vx + d.vy * d.vy)
        if (vmag > SWARM_MAX_SPEED) {
          d.vx = (d.vx / vmag) * SWARM_MAX_SPEED
          d.vy = (d.vy / vmag) * SWARM_MAX_SPEED
        }

        d.x += d.vx * dt
        d.y += d.vy * dt

        d.el.setAttribute('cx', d.x.toFixed(2))
        d.el.setAttribute('cy', d.y.toFixed(2))
      }

      if (now - t0 < SWARM_RUN_MS) {
        raf = requestAnimationFrame(frame)
      } else {
        for (const d of dots) {
          // settle exactly on the slots
          d.el.setAttribute('cx', d.tx.toFixed(2))
          d.el.setAttribute('cy', d.ty.toFixed(2))
        }
      }
    }

    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [reducedMotion])

  return (
    <svg
      ref={svgRef}
      className="swarm"
      viewBox={`0 0 ${SWARM_W} ${SWARM_H}`}
      width={SWARM_W}
      height={SWARM_H}
      aria-hidden="true"
    />
  )
}

// ---------------------------------------------------------------------------
// IngestPage
// ---------------------------------------------------------------------------

export default function IngestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const queryClient = useQueryClient()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const reducedMotion = useReducedMotion()

  const [activeTab, setActiveTab] = useState<IngestTab>('file')
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [isDragging, setIsDragging] = useState<boolean>(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [urlInput, setUrlInput] = useState('')
  const [urlError, setUrlError] = useState<string | null>(null)

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [progressLabel, setProgressLabel] = useState<string | null>(null)
  // Epoch ms when the current job started; null when no job is running. Drives
  // the elapsed-time counter (see elapsedSeconds effect below).
  const [jobStartedAt, setJobStartedAt] = useState<number | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set())
  // Optimistic in-flight rows surfaced in the KB list while a job is running.
  const [optimisticDocs, setOptimisticDocs] = useState<OptimisticDoc[]>([])

  // Document detail modal: the id of the row whose detail is open (null = closed).
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null)
  // Track each row's DOM node so focus can be restored to the triggering row
  // when the modal closes (WCAG 2.4.3 Focus Order).
  const rowRefs = useRef<Map<string, HTMLButtonElement | null>>(new Map())

  // Roving-tab focus targets (UI-SPEC S13: arrow-key roving tabs).
  const tabButtonRefs = useRef<Record<IngestTab, HTMLButtonElement | null>>({
    file: null,
    url: null,
  })

  // ---------------------------------------------------------------------------
  // Elapsed-time counter — ticks once per second while a job is running so the
  // user gets a sign of life during long ingests. Resets to 0 whenever a job
  // starts (jobStartedAt set) and stops when it clears (job terminal/aborted).
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (jobStartedAt === null) {
      setElapsedSeconds(0)
      return
    }
    setElapsedSeconds(Math.floor((Date.now() - jobStartedAt) / 1000))
    const tick = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - jobStartedAt) / 1000))
    }, 1000)
    return () => clearInterval(tick)
  }, [jobStartedAt])

  // ---------------------------------------------------------------------------
  // Load agent status + documents via TanStack Query
  // ---------------------------------------------------------------------------

  const agentQuery = useQuery({
    queryKey: ['agent', id], // shares cache with layout — instant hit
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json() as Promise<{ id: string; status: string; name: string }>
    },
    enabled: isLoaded && !!isSignedIn,
    staleTime: 30_000,
  })

  const docsQuery = useQuery({
    queryKey: ['agent-documents', id],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/documents`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      return (data.documents ?? []) as Document[]
    },
    enabled: isLoaded && !!isSignedIn && agentQuery.data?.status === 'ready',
    staleTime: 10_000,
    refetchInterval: (query) => {
      const docs = (query.state.data as Document[] | undefined) ?? []
      return docs.some((d) => d.parse_status === 'pending' || d.parse_status === 'processing')
        ? 3_000
        : false
    },
  })

  const agentStatus = agentQuery.data?.status ?? null
  const documents = docsQuery.data ?? []
  const loadError = agentQuery.isError ? 'Failed to load agent. Please refresh.' : null

  const handleDeleteDoc = useCallback(
    async (docId: string) => {
      setDeletingIds((prev) => new Set(prev).add(docId))
      try {
        const token = await getToken()
        const r = await fetch(`${apiBase}/api/v1/agents/${id}/documents/${docId}`, {
          method: 'DELETE',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!r.ok && r.status !== 404) throw new Error(`HTTP ${r.status}`)
        await queryClient.invalidateQueries({ queryKey: ['agent-documents', id] })
      } catch (err) {
        console.error('Delete failed', err)
        setSubmitError('Failed to delete document — please try again.')
      } finally {
        setDeletingIds((prev) => {
          const next = new Set(prev)
          next.delete(docId)
          return next
        })
      }
    },
    [getToken, apiBase, id, queryClient],
  )

  // ---------------------------------------------------------------------------
  // SSE progress reader — fetch + ReadableStream (EventSource can't send the
  // Authorization header). The backend uses standard SSE framing produced by
  // sse-starlette:
  //
  //     event: parsing.started
  //     data: {"document_id": "...", "at": "..."}
  //     id: <uuid>
  //     <blank line terminates the event>
  //
  // The event NAME lives on the `event:` line — the `data:` JSON is only the
  // payload metadata and contains NO `event` field. The previous reader looked
  // for `data:` lines and read `payload.event`, which was always undefined, so
  // progress never advanced past "Starting..." and the terminal job.complete /
  // job.failed transitions never fired. This parser reads the `event:` line for
  // the type and buffers across chunk boundaries so events split across two TCP
  // reads are not dropped.
  // ---------------------------------------------------------------------------

  const readSseProgress = async (eventsUrl: string, token: string) => {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), SSE_TIMEOUT_MS)

    // Was a terminal event (job.complete / job.failed) observed on the stream?
    // Drives whether the cleanup path treats the run as resolved or as a silent
    // drop that still needs a neutral "refresh to see status" hint.
    let sawTerminal = false

    // Single source of truth for resetting the form back to a usable state.
    // Runs on EVERY exit path — terminal event, natural stream end, timeout,
    // abort, or error — so the UI can never be left stuck on "processing".
    // Always: refetch real docs, drop optimistic rows, clear the in-progress
    // label/timer, and re-enable the form.
    const teardown = async () => {
      // Await the refetch so optimistic rows stay visible until real rows land.
      await queryClient.refetchQueries({ queryKey: ['agent-documents', id] })
      setOptimisticDocs([])
      setProgressLabel(null)
      setSubmitting(false)
      setJobStartedAt(null)
    }

    // Handle one fully-parsed SSE event block.
    // Returns true if the stream should stop (terminal event reached).
    const handleEvent = (eventType: string, dataLines: string[]): boolean => {
      // The event NAME is authoritative (from the `event:` line). The data JSON
      // is parsed only for completeness; we never read an `event` field from it.
      if (dataLines.length > 0) {
        try {
          JSON.parse(dataLines.join('\n'))
        } catch {
          // payload may be malformed/empty (e.g. keep-alive) — type still drives UI
        }
      }
      if (!eventType) return false

      if (eventType === 'job.complete') {
        sawTerminal = true
        return true
      }
      if (eventType === 'job.failed') {
        sawTerminal = true
        setSubmitError('Ingestion failed. Check the Celery worker logs.')
        return true
      }

      // Non-terminal progress event — surface its label.
      setProgressLabel(EVENT_LABELS[eventType] ?? eventType)
      return false
    }

    try {
      const resp = await fetch(`${apiBase}${eventsUrl}`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      })
      if (!resp.body) {
        setSubmitError('No response stream. Check if the Celery worker is running.')
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE events are separated by a blank line. Normalise CRLF then split
        // into complete blocks, keeping any trailing partial block in `buffer`.
        buffer = buffer.replace(/\r\n/g, '\n')
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() ?? '' // last element is incomplete (or '')

        for (const block of blocks) {
          let eventType = ''
          const dataLines: string[] = []
          for (const rawLine of block.split('\n')) {
            if (rawLine.startsWith('event:')) {
              eventType = rawLine.slice('event:'.length).trim()
            } else if (rawLine.startsWith('data:')) {
              dataLines.push(rawLine.slice('data:'.length).replace(/^ /, ''))
            }
            // `id:` and comment (`:`) lines are ignored for progress purposes
          }
          const stop = handleEvent(eventType, dataLines)
          if (stop) return
        }
      }

      // Reader returned done:true without a terminal event — connection dropped.
      // refetchInterval polls until pending docs reach a terminal parse_status.
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setSubmitError('Lost connection to job stream. The job may still be running.')
      }
    } finally {
      clearTimeout(timeoutId)
      // refetchInterval drives polling automatically — no signal needed here.
      await teardown()
    }
  }

  // ---------------------------------------------------------------------------
  // Drag-and-drop handlers
  // ---------------------------------------------------------------------------

  const acceptFile = useCallback((files: FileList | File[]) => {
    const list = Array.from(files)
    const valid: File[] = []
    let hadRejected = false
    for (const file of list) {
      const name = file.name.toLowerCase()
      const isAccepted = ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))
      if (isAccepted) {
        valid.push(file)
      } else {
        hadRejected = true
      }
    }
    setSelectedFiles(valid)
    if (hadRejected) {
      setSubmitError('Unsupported file type removed. Accepted: PDF, PNG, JPG, MD')
    } else {
      setSubmitError(null)
    }
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(true)
  }, [])

  const handleDragEnter = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(false)
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      setIsDragging(false)
      acceptFile(e.dataTransfer.files)
    },
    [acceptFile],
  )

  // ---------------------------------------------------------------------------
  // Submit handler
  // ---------------------------------------------------------------------------

  const handleSubmit = async () => {
    setSubmitError(null)
    setProgressLabel(null)

    // Snapshot the sources being submitted so we can render optimistic
    // "processing" rows in the KB list while the job runs. Captured before the
    // inputs are reset below.
    const pendingFiles = [...selectedFiles]
    const pendingUrl = urlInput.trim()

    // Validate
    if (activeTab === 'url') {
      if (!urlInput.trim()) {
        setUrlError('URL is required.')
        return
      }
      if (!urlInput.trim().startsWith('http://') && !urlInput.trim().startsWith('https://')) {
        setUrlError('URL must start with http:// or https://')
        return
      }
      setUrlError(null)
    } else {
      if (selectedFiles.length === 0) {
        setSubmitError('Please select at least one file.')
        return
      }
    }

    setSubmitting(true)
    setProgressLabel('Starting...')
    setJobStartedAt(Date.now())

    try {
      const token = await getToken()
      if (!token) {
        setSubmitError('Not authenticated. Please sign in.')
        setSubmitting(false)
        setJobStartedAt(null)
        return
      }

      const formData = new FormData()
      if (activeTab === 'file' && selectedFiles.length > 0) {
        for (const f of selectedFiles) {
          formData.append('files', f)
        }
      } else if (activeTab === 'url') {
        formData.append('urls', urlInput.trim())
      }

      // POST multipart/form-data — do NOT set Content-Type (browser sets multipart boundary)
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/documents`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      })

      if (res.status === 409) {
        setSubmitError('Agent is still provisioning. Please wait for it to finish.')
        setSubmitting(false)
        setJobStartedAt(null)
        return
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setSubmitError(`Upload failed: ${body?.detail ?? `HTTP ${res.status}`}`)
        setSubmitting(false)
        setJobStartedAt(null)
        return
      }

      const data: { job_id: string; events_url: string } = await res.json()

      // Optimistic feedback: show a "processing" row per submitted source while
      // the ingestion job runs. Cleared once a terminal SSE event triggers a
      // refetch of the real documents list (see readSseProgress -> teardown()).
      const placeholders: OptimisticDoc[] =
        activeTab === 'url'
          ? [{ clientKey: `${data.job_id}:url`, source_type: 'url', title: pendingUrl }]
          : pendingFiles.map((f, i) => ({
              clientKey: `${data.job_id}:${i}`,
              source_type: (f.name.split('.').pop() || 'file').toLowerCase(),
              title: f.name,
            }))
      setOptimisticDocs(placeholders)

      // Reset inputs
      setSelectedFiles([])
      setUrlInput('')

      // Stream SSE progress
      await readSseProgress(data.events_url, token)
    } catch (err) {
      console.error(err)
      setSubmitError('Upload failed. Please try again.')
      setOptimisticDocs([])
      setSubmitting(false)
      setProgressLabel(null)
      setJobStartedAt(null)
    }
  }

  // ---------------------------------------------------------------------------
  // Tab selection — roving tabindex, arrow-key navigation (UI-SPEC S13, ported
  // from ingest.html's select()/keydown handlers).
  // ---------------------------------------------------------------------------

  const TAB_ORDER: IngestTab[] = ['file', 'url']

  const selectTab = (tab: IngestTab, focus: boolean) => {
    setActiveTab(tab)
    setSubmitError(null)
    setUrlError(null)
    setProgressLabel(null)
    setSubmitting(false)
    setJobStartedAt(null)
    if (focus) tabButtonRefs.current[tab]?.focus()
  }

  const handleTabKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (e.key === 'ArrowRight') {
      e.preventDefault()
      selectTab(TAB_ORDER[(index + 1) % TAB_ORDER.length], true)
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      selectTab(TAB_ORDER[(index + TAB_ORDER.length - 1) % TAB_ORDER.length], true)
    } else if (e.key === 'Home') {
      e.preventDefault()
      selectTab(TAB_ORDER[0], true)
    } else if (e.key === 'End') {
      e.preventDefault()
      selectTab(TAB_ORDER[TAB_ORDER.length - 1], true)
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const alertStyle: React.CSSProperties = {
    padding: '12px 16px',
    marginBottom: '20px',
    background: 'var(--fail-dim)',
    border: '1px solid color-mix(in oklch, var(--fail) 32%, transparent)',
    borderRadius: 'var(--r-panel)',
    fontSize: '14px',
    color: 'var(--fail)',
  }

  return (
    <div className="page">
      <style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />

      <header className="page-head">
        <div className="row">
          <div>
            <h1>Ingest documents</h1>
            <p className="sub">
              Everything the agent is allowed to say comes from here. A document is parsed, cut
              into chunks and embedded, and only then can it be cited.
            </p>
          </div>
        </div>
      </header>

      {/* Load error */}
      {loadError && (
        <div role="alert" style={alertStyle}>
          {loadError}
        </div>
      )}

      {/* Agent not ready guard — informational, never a fail/pass verdict colour */}
      {agentStatus !== null && agentStatus !== 'ready' ? (
        <div className="zone" style={{ marginBottom: '24px' }}>
          <p style={{ margin: 0, fontSize: '14px', color: 'var(--ink-2)' }}>
            Agent is still provisioning — ingest is available once the agent is ready.
          </p>
        </div>
      ) : (
        <>
          {/* Tab nav */}
          <h2 className="vh">Add a document</h2>
          <div className="tabs" role="tablist" aria-label="How to add a document">
            {TAB_ORDER.map((tab, i) => (
              <button
                key={tab}
                ref={(el) => {
                  tabButtonRefs.current[tab] = el
                }}
                id={`tab-${tab}`}
                role="tab"
                type="button"
                className="tab"
                aria-selected={activeTab === tab}
                aria-controls={`panel-${tab}`}
                tabIndex={activeTab === tab ? 0 : -1}
                onClick={() => selectTab(tab, false)}
                onKeyDown={(e) => handleTabKeyDown(e, i)}
              >
                {tab === 'file' ? 'Upload file' : 'Add URL'}
              </button>
            ))}
          </div>

          {/* Submit error */}
          {submitError && (
            <div role="alert" style={alertStyle}>
              {submitError}
            </div>
          )}

          {/* Tab: Upload File */}
          <div
            id="panel-file"
            role="tabpanel"
            aria-labelledby="tab-file"
            tabIndex={0}
            hidden={activeTab !== 'file'}
          >
            <div
              className="drop"
              data-over={isDragging ? 'true' : 'false'}
              onDragOver={handleDragOver}
              onDragEnter={handleDragEnter}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onClick={(e) => {
                const target = e.target as HTMLElement
                // the label owns the real dialog; elsewhere on the zone, open it too
                if (target.closest('label') || target.closest('input')) return
                if (!submitting) fileInputRef.current?.click()
              }}
            >
              <h3>
                {selectedFiles.length === 1
                  ? selectedFiles[0].name
                  : selectedFiles.length > 1
                    ? `${selectedFiles.length} files selected`
                    : 'Drop PDF, PNG, JPG or MD'}
              </h3>
              <p className="help">
                {selectedFiles.length > 0
                  ? (() => {
                      const names = selectedFiles.map((f) => f.name).join(', ')
                      return names.length > 60 ? `${names.slice(0, 60)}…` : names
                    })()
                  : 'Up to 20 MB a file. Anything you drop here is retrievable by the agent within seconds.'}
              </p>
              <input
                ref={fileInputRef}
                type="file"
                id="file"
                multiple
                accept=".pdf,.png,.jpg,.jpeg,.md"
                onChange={(e) => {
                  if (e.target.files) acceptFile(e.target.files)
                }}
                disabled={submitting}
              />
              <label className="file-btn" htmlFor="file">
                Choose file
              </label>
            </div>
            <button
              type="button"
              className="btn btn-primary"
              style={{ marginTop: '16px' }}
              onClick={handleSubmit}
              disabled={submitting || selectedFiles.length === 0}
            >
              {submitting && activeTab === 'file' ? 'Uploading…' : 'Upload file'}
            </button>
          </div>

          {/* Tab: Add URL */}
          <div
            id="panel-url"
            role="tabpanel"
            aria-labelledby="tab-url"
            tabIndex={0}
            hidden={activeTab !== 'url'}
          >
            <form
              className="url-form"
              onSubmit={(e) => {
                e.preventDefault()
                handleSubmit()
              }}
            >
              <div className="field">
                <label htmlFor="url">Page address</label>
                <input
                  id="url"
                  type="url"
                  placeholder="https://example.com/document"
                  value={urlInput}
                  onChange={(e) => {
                    setUrlInput(e.target.value)
                    setUrlError(null)
                  }}
                  disabled={submitting}
                  autoComplete="off"
                  spellCheck={false}
                  aria-describedby="url-help"
                />
                <p className="help" id="url-help">
                  The page is fetched once, stripped of navigation and chrome, then chunked like
                  any other document.
                </p>
                {urlError && (
                  <p role="alert" className="help" style={{ color: 'var(--fail)' }}>
                    {urlError}
                  </p>
                )}
              </div>
              <button type="submit" className="btn btn-primary" disabled={submitting}>
                {submitting && activeTab === 'url' ? 'Fetching…' : 'Fetch page'}
              </button>
            </form>
          </div>

          <p className="vh" role="status" aria-live="polite">
            {progressLabel ?? ''}
          </p>
        </>
      )}

      {/* Knowledge base */}
      <section className="section">
        <div className="section-head">
          <h2 className="label" id="kb-label">
            Knowledge base
            <span className="chip chip-mute count num">
              {documents.length + optimisticDocs.length}
            </span>
          </h2>
        </div>

        {documents.length === 0 && optimisticDocs.length === 0 ? (
          <EmptyState
            heading="No documents yet"
            body="Upload a file or add a URL above to start building this agent's knowledge base."
          />
        ) : (
          <Ledger caption="Knowledge base documents: name, type, chunk count, ingestion status, and date added">
            <thead>
              <tr>
                <LedgerColHead>Document</LedgerColHead>
                <LedgerColHead className="col-type">Type</LedgerColHead>
                <LedgerColHead numeric>Chunks</LedgerColHead>
                <LedgerColHead className="st">Status</LedgerColHead>
                <LedgerColHead className="col-added">Added</LedgerColHead>
              </tr>
            </thead>
            <tbody>
              {/* Optimistic in-flight rows — shown while the ingestion job runs
                  (real SSE-driven state), replaced by real rows once the
                  documents query refetches. This is the one place the HIVE
                  swarm renders: a document actually transitioning to parsing. */}
              {optimisticDocs.map((doc) => (
                <tr key={doc.clientKey} aria-busy="true">
                  <LedgerRowHead>
                    <span className="doc">
                      <DocIcon />
                      {doc.title}
                    </span>
                  </LedgerRowHead>
                  <td className="col-type">
                    <Chip verdict="mute">{doc.source_type.toUpperCase()}</Chip>
                  </td>
                  <td className="num pending">pending</td>
                  <td className="st">
                    <div className="parsing">
                      <span className="label">{progressLabel ?? 'Starting…'}</span>
                      <ChunkSwarm reducedMotion={reducedMotion} />
                    </div>
                    {jobStartedAt !== null && (
                      <span className="mono elapsed">{formatElapsed(elapsedSeconds)}</span>
                    )}
                  </td>
                  <td className="col-added mono">—</td>
                </tr>
              ))}

              {documents.map((doc) => {
                const verdict = parseStatusVerdict(doc.parse_status)
                const isProcessing = verdict === 'live'
                return (
                  <tr key={doc.id}>
                    <LedgerRowHead>
                      <button
                        type="button"
                        className="doc doc-open"
                        ref={(el) => {
                          rowRefs.current.set(doc.id, el)
                        }}
                        aria-haspopup="dialog"
                        aria-label={`View details for ${doc.title || doc.source_uri}`}
                        onClick={() => setSelectedDocId(doc.id)}
                      >
                        <DocIcon />
                        <span className="doc-title">{doc.title || doc.source_uri}</span>
                      </button>
                    </LedgerRowHead>
                    <td className="col-type">
                      <Chip verdict="mute">{doc.source_type.toUpperCase()}</Chip>
                    </td>
                    <td className={doc.chunk_count > 0 ? 'num' : 'num pending'}>
                      {doc.chunk_count > 0 ? doc.chunk_count : isProcessing ? 'pending' : doc.chunk_count}
                    </td>
                    <td className="st">
                      <span className="st-line">
                        <Chip verdict={verdict} dot={verdict === 'live'}>
                          {verdict === 'mute' ? doc.parse_status : STATUS_LABEL[verdict]}
                        </Chip>
                      </span>
                    </td>
                    <td className="col-added mono">
                      <span className="added-cell">
                        {new Date(doc.created_at).toLocaleDateString()}
                        <button
                          type="button"
                          className="row-del"
                          onClick={() => handleDeleteDoc(doc.id)}
                          disabled={deletingIds.has(doc.id)}
                          aria-label={`Delete ${doc.title || doc.source_uri}`}
                        >
                          {deletingIds.has(doc.id) ? '…' : '×'}
                        </button>
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </Ledger>
        )}
      </section>

      {/* Next step CTA — visible once at least one document is successfully ingested */}
      {documents.some((d) => d.parse_status !== 'failed') && (
        <section
          className="section"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '24px',
            flexWrap: 'wrap',
          }}
        >
          <p className="voice" style={{ margin: 0 }}>
            Knowledge base ready — you can now run evaluations.
          </p>
          <Link href={`/agents/${id}/eval`} className="btn btn-primary">
            Next: Run evals →
          </Link>
        </section>
      )}

      {/* Document detail modal — opened by clicking a KB row. */}
      {selectedDocId && (
        <DocumentDetailModal
          agentId={id}
          documentId={selectedDocId}
          onClose={() => setSelectedDocId(null)}
          returnFocusRef={{ current: rowRefs.current.get(selectedDocId) ?? null }}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page-scoped CSS — ported from prototypes/gotham/ingest.html's own <style>
// block (tabs / drop / ledger status cell / swarm), following the same
// static dangerouslySetInnerHTML pattern used by soul/page.tsx and
// agents/[id]/page.tsx. `.cross-*` / `.vh` are handled globally
// (PageChrome / globals.css) and are not repeated here.
// ---------------------------------------------------------------------------
const PAGE_CSS = `
  .tabs {
    display: flex; gap: 26px;
    border-bottom: 1px solid var(--hairline);
    margin-bottom: 22px;
  }
  .tab {
    appearance: none; background: none; border: 0;
    padding: 0 0 11px; margin-bottom: -1px; cursor: pointer;
    font-family: var(--mono); font-size: 11px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--ink-3);
    border-bottom: 2px solid transparent;
    transition: color 140ms ease, border-color 140ms ease;
  }
  .tab:hover { color: var(--ink-2); }
  .tab[aria-selected="true"] { color: var(--live); border-bottom-color: var(--live); }

  .drop {
    position: relative;
    display: block; width: 100%; padding: 40px 24px;
    background: var(--well);
    border: 1px dashed var(--hairline-strong);
    border-radius: var(--r-panel);
    text-align: center; cursor: pointer;
    transition: border-color 140ms ease, background 140ms ease;
  }
  .drop:hover, .drop[data-over="true"] { border-color: var(--live); background: var(--surface); }
  .drop h3 { font-size: 15px; }
  .drop .help { margin: 6px 0 16px; }
  .drop label { margin: 0; }
  .drop input[type="file"] {
    position: absolute; width: 1px; height: 1px; opacity: 0;
    overflow: hidden; clip-path: inset(50%);
  }
  .file-btn {
    display: inline-flex; align-items: center; gap: 8px;
    font-family: var(--sans); font-size: 13px; font-weight: 600;
    letter-spacing: 0; text-transform: none;
    padding: 9px 16px; border-radius: var(--r-control);
    color: var(--live-ink); background: color-mix(in srgb, var(--live) 70%, transparent);
    cursor: pointer;
  }
  .drop input[type="file"]:focus-visible + .file-btn { outline: 2px solid var(--live); outline-offset: 2px; }

  .url-form { max-width: 520px; }
  .url-form .btn { margin-top: 2px; }

  .count { margin-left: 10px; }
  .doc { display: flex; align-items: center; gap: 10px; }
  .doc svg { flex: none; color: var(--ink-3); }
  .doc-open {
    appearance: none; background: none; border: none; padding: 0; margin: 0;
    width: 100%; text-align: left; cursor: pointer; color: var(--ink); font: inherit;
  }
  .doc-title {
    font-size: 13px; font-weight: 600; color: var(--ink);
    transition: color 140ms ease;
  }
  .doc-open:hover .doc-title, .doc-open:focus-visible .doc-title { color: var(--live); }

  .ledger th.st, .ledger td.st { min-width: 196px; }
  .ledger td.st { padding-top: 9px; padding-bottom: 9px; }
  .st-line { display: flex; align-items: center; gap: 10px; }
  .elapsed { font-size: 12px; color: var(--ink-3); }
  .pending { color: var(--ink-3); }

  .added-cell { display: flex; align-items: center; gap: 8px; }
  .row-del {
    flex: none; width: 22px; height: 22px;
    display: grid; place-items: center;
    background: transparent; border: 1px solid transparent; border-radius: var(--r-control);
    color: var(--ink-3); cursor: pointer; font-size: 14px; line-height: 1;
    transition: color 140ms ease, background 140ms ease;
  }
  .row-del:hover { color: var(--seal-hot); background: var(--seal-dim); }
  .row-del[disabled] { opacity: 0.4; cursor: not-allowed; }

  /* the chunk swarm (HIVE): dispatch births a centroid, the chunks stream to
     it at a capped speed. You watch the document become citable evidence. */
  .parsing { display: flex; flex-direction: column; gap: 4px; }
  .parsing .label { color: var(--live); }
  .swarm { display: block; border-radius: 3px; background: var(--well); }

  @media (max-width: 760px) {
    .ledger .col-added, .ledger .col-type { display: none; }
  }
`
