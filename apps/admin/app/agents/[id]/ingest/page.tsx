'use client'
import { useState, useRef, useCallback, useEffect, use } from 'react'
import { useAuth } from '@clerk/nextjs'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import DocumentDetailModal from './DocumentDetailModal'

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
  'metadata.complete': 'Metadata extracted',
  'embedding.started': 'Generating embeddings...',
  'embedding.complete': 'Embeddings done',
  'ingestion.complete': 'Processing complete',
  'job.complete': 'Done!',
  'job.failed': 'Failed',
}

const PARSE_STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  complete: { bg: 'var(--green-bg)', fg: 'var(--green)' },
  parsed: { bg: 'var(--green-bg)', fg: 'var(--green)' },
  pending: { bg: 'var(--amber-bg)', fg: 'var(--amber)' },
  processing: { bg: 'var(--amber-bg)', fg: 'var(--amber)' },
  failed: { bg: 'var(--red-bg)', fg: 'var(--red)' },
}

function getParseStatusColor(status: string) {
  return PARSE_STATUS_COLORS[status] ?? { bg: 'var(--surface-3)', fg: 'var(--text-3)' }
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
// IngestPage
// ---------------------------------------------------------------------------

export default function IngestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const queryClient = useQueryClient()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  const [activeTab, setActiveTab] = useState<IngestTab>('file')
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [isDragging, setIsDragging] = useState<boolean>(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [urlInput, setUrlInput] = useState('')
  const [urlError, setUrlError] = useState<string | null>(null)

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [progressLabel, setProgressLabel] = useState<string | null>(null)
  // Neutral (non-error) status surfaced after a stream ends without a terminal
  // event — e.g. timeout/abort or a dropped connection. Distinct from
  // submitError so it does not render as a red alert.
  const [progressNotice, setProgressNotice] = useState<string | null>(null)
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
  const rowRefs = useRef<Map<string, HTMLDivElement | null>>(new Map())

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
  })

  const agentStatus = agentQuery.data?.status ?? null
  const documents = docsQuery.data ?? []
  const loadError = agentQuery.isError ? 'Failed to load agent. Please refresh.' : null

  // ---------------------------------------------------------------------------
  // TanStack Query: invalidate + refetch the documents list.
  // Used after a job reaches a terminal state so freshly-ingested rows
  // (and their final parse_status / chunk_count) appear without a manual reload.
  // ---------------------------------------------------------------------------

  const refreshDocuments = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ['agent-documents', id] })
  }, [queryClient, id])

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
      // The job may have produced (or be finishing) real rows server-side; pull
      // whatever exists now so the KB list reflects reality.
      await refreshDocuments()
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
        setProgressNotice('Processing complete.')
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

      // Reader returned done:true. If we got here WITHOUT a terminal event the
      // Celery chain may have finished but the connection dropped before
      // job.complete arrived (network blip, server restart). Surface a neutral
      // hint rather than leaving the form stuck.
      if (!sawTerminal) {
        setProgressNotice('Upload submitted — refresh to see status.')
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        // Timeout/abort: the job is very likely still running server-side.
        setProgressNotice('Taking longer than expected — check back in a moment.')
      } else {
        setSubmitError('Lost connection to job stream. The job may still be running.')
      }
    } finally {
      clearTimeout(timeoutId)
      // Cleanup is unconditional: terminal event, silent drop, abort, or error
      // all funnel through here so `submitting` is always cleared.
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
    setProgressNotice(null)
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
      // refetch of the real documents list (see readSseProgress -> finish()).
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
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div
      style={{
        padding: '32px 40px',
        maxWidth: '720px',
        fontFamily: 'var(--font-sans)',
      }}
    >
      <h1
        style={{
          fontSize: '22px',
          fontWeight: 700,
          color: 'var(--text-1)',
          marginBottom: '8px',
        }}
      >
        Ingest documents
      </h1>

      <p
        style={{
          color: 'var(--text-3)',
          fontSize: '14px',
          marginBottom: '24px',
        }}
      >
        Upload PDFs, images, markdown, or URLs for your agent&apos;s knowledge base.
      </p>

      {/* Load error */}
      {loadError && (
        <div
          role="alert"
          style={{
            padding: '12px 16px',
            marginBottom: '20px',
            background: 'var(--red-bg)',
            border: '1px solid rgba(192,57,43,0.3)',
            borderRadius: 'var(--radius-xs)',
            fontSize: '14px',
            color: 'var(--red)',
          }}
        >
          {loadError}
        </div>
      )}

      {/* Agent not ready guard */}
      {agentStatus !== null && agentStatus !== 'ready' ? (
        <div
          style={{
            padding: '24px',
            background: 'var(--amber-bg)',
            border: '1px solid rgba(180,120,0,0.2)',
            borderRadius: 'var(--radius-xs)',
            fontSize: '14px',
            color: 'var(--amber)',
            marginBottom: '24px',
          }}
        >
          Agent is still provisioning — ingest is available once the agent is ready.
        </div>
      ) : (
        <>
          {/* Tab nav */}
          <div
            role="tablist"
            style={{
              display: 'flex',
              borderBottom: '1px solid var(--border-soft)',
              marginBottom: '20px',
            }}
          >
            {(['file', 'url'] as IngestTab[]).map((tab) => (
              <button
                key={tab}
                role="tab"
                aria-selected={activeTab === tab}
                onClick={() => {
                  setActiveTab(tab)
                  setSubmitError(null)
                  setUrlError(null)
                  setProgressLabel(null)
                  setProgressNotice(null)
                  setSubmitting(false)
                  setJobStartedAt(null)
                }}
                style={{
                  padding: '10px 20px',
                  border: 'none',
                  borderBottom: `2px solid ${activeTab === tab ? 'var(--accent)' : 'transparent'}`,
                  background: 'none',
                  color: activeTab === tab ? 'var(--accent)' : 'var(--text-3)',
                  fontWeight: activeTab === tab ? 600 : 400,
                  fontSize: '14px',
                  cursor: 'pointer',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                {tab === 'file' ? 'Upload File' : 'Add URL'}
              </button>
            ))}
          </div>

          {/* Submit error */}
          {submitError && (
            <div
              role="alert"
              style={{
                padding: '12px 16px',
                marginBottom: '16px',
                background: 'var(--red-bg)',
                border: '1px solid rgba(192,57,43,0.3)',
                borderRadius: 'var(--radius-xs)',
                fontSize: '14px',
                color: 'var(--red)',
              }}
            >
              {submitError}
            </div>
          )}

          {/* Progress indicator — live label + elapsed-time counter so a long
              job visibly stays alive. */}
          {submitting && progressLabel && (
            <div
              role="status"
              aria-live="polite"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '12px',
                padding: '12px 16px',
                marginBottom: '16px',
                background: 'var(--accent-dim)',
                border: '1px solid rgba(123,28,58,0.15)',
                borderRadius: 'var(--radius-xs)',
                fontSize: '14px',
                color: 'var(--accent)',
                fontWeight: 500,
              }}
            >
              <span>{progressLabel}</span>
              {jobStartedAt !== null && (
                <span
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: '12px',
                    fontVariantNumeric: 'tabular-nums',
                    opacity: 0.8,
                    flexShrink: 0,
                  }}
                >
                  {formatElapsed(elapsedSeconds)}
                </span>
              )}
            </div>
          )}

          {/* Neutral notice — shown after a stream ends without a terminal event
              (timeout/abort or a dropped connection). Not an error: the job may
              still be running or already finished server-side. */}
          {progressNotice && !submitting && (
            <div
              role="status"
              aria-live="polite"
              style={{
                padding: '12px 16px',
                marginBottom: '16px',
                background: 'var(--surface-2)',
                border: '1px solid var(--border-soft)',
                borderRadius: 'var(--radius-xs)',
                fontSize: '14px',
                color: 'var(--text-2)',
                fontWeight: 500,
              }}
            >
              {progressNotice}
            </div>
          )}

          {/* Tab: Upload File */}
          {activeTab === 'file' && (
            <div style={{ marginBottom: '24px' }}>
              <label
                style={{
                  display: 'block',
                  fontWeight: 600,
                  fontSize: '11px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--text-3)',
                  marginBottom: '8px',
                }}
              >
                File
              </label>
              <div
                role="button"
                tabIndex={0}
                aria-label="Drop a file here or click to browse"
                onClick={() => {
                  if (!submitting) fileInputRef.current?.click()
                }}
                onKeyDown={(e) => {
                  if ((e.key === 'Enter' || e.key === ' ') && !submitting) {
                    e.preventDefault()
                    fileInputRef.current?.click()
                  }
                }}
                onDragOver={handleDragOver}
                onDragEnter={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  textAlign: 'center',
                  height: '160px',
                  marginBottom: '16px',
                  padding: '16px',
                  border: `2px dashed ${isDragging ? 'var(--accent)' : 'var(--border-soft)'}`,
                  borderRadius: 'var(--radius-sm)',
                  background: isDragging ? 'var(--accent-dim)' : 'var(--surface-2)',
                  cursor: submitting ? 'not-allowed' : 'pointer',
                  transition: 'border-color 0.15s ease, background 0.15s ease',
                }}
              >
                {selectedFiles.length === 1 ? (
                  <span
                    style={{
                      fontSize: '14px',
                      fontWeight: 600,
                      color: 'var(--text-2)',
                      fontFamily: 'var(--font-sans)',
                      wordBreak: 'break-all',
                    }}
                  >
                    {selectedFiles[0].name}
                  </span>
                ) : selectedFiles.length > 1 ? (
                  <>
                    <span
                      style={{
                        fontSize: '14px',
                        fontWeight: 600,
                        color: 'var(--text-2)',
                        fontFamily: 'var(--font-sans)',
                      }}
                    >
                      {selectedFiles.length} files selected
                    </span>
                    <span
                      style={{
                        fontSize: '12px',
                        color: 'var(--text-3)',
                        fontFamily: 'var(--font-sans)',
                        marginTop: '4px',
                        wordBreak: 'break-all',
                      }}
                    >
                      {(() => {
                        const names = selectedFiles.map((f) => f.name).join(', ')
                        return names.length > 60 ? `${names.slice(0, 60)}…` : names
                      })()}
                    </span>
                  </>
                ) : (
                  <>
                    <span
                      style={{
                        fontSize: '14px',
                        fontWeight: 600,
                        color: 'var(--text-2)',
                        fontFamily: 'var(--font-sans)',
                      }}
                    >
                      Drop files here
                    </span>
                    <span
                      style={{
                        fontSize: '12px',
                        color: 'var(--text-3)',
                        fontFamily: 'var(--font-sans)',
                        marginTop: '4px',
                      }}
                    >
                      or click to browse
                    </span>
                  </>
                )}
              </div>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.png,.jpg,.jpeg,.md"
                onChange={(e) => {
                  if (e.target.files) acceptFile(e.target.files)
                }}
                disabled={submitting}
                style={{ display: 'none' }}
              />
              <button
                onClick={handleSubmit}
                disabled={submitting || selectedFiles.length === 0}
                style={{
                  padding: '12px 28px',
                  minHeight: '44px',
                  background:
                    submitting || selectedFiles.length === 0 ? 'var(--surface-3)' : 'var(--accent)',
                  color: submitting || selectedFiles.length === 0 ? 'var(--text-4)' : '#fff',
                  border: 'none',
                  borderRadius: 'var(--radius-sm)',
                  cursor: submitting || selectedFiles.length === 0 ? 'not-allowed' : 'pointer',
                  fontSize: '14px',
                  fontWeight: 600,
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Upload file
              </button>
            </div>
          )}

          {/* Tab: Add URL */}
          {activeTab === 'url' && (
            <div style={{ marginBottom: '24px' }}>
              <label
                htmlFor="urlInput"
                style={{
                  display: 'block',
                  fontWeight: 600,
                  fontSize: '11px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--text-3)',
                  marginBottom: '8px',
                }}
              >
                URL
              </label>
              <input
                id="urlInput"
                type="url"
                placeholder="https://example.com/document"
                value={urlInput}
                onChange={(e) => {
                  setUrlInput(e.target.value)
                  setUrlError(null)
                }}
                disabled={submitting}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  border: `1px solid ${urlError ? 'var(--red)' : 'var(--border)'}`,
                  borderRadius: 'var(--radius-xs)',
                  fontSize: '14px',
                  fontFamily: 'var(--font-sans)',
                  background: 'var(--surface-2)',
                  color: 'var(--text-1)',
                  outline: 'none',
                  boxSizing: 'border-box',
                  marginBottom: '8px',
                }}
              />
              {urlError && (
                <p role="alert" style={{ fontSize: '12px', color: 'var(--red)', marginBottom: '12px' }}>
                  {urlError}
                </p>
              )}
              <button
                onClick={handleSubmit}
                disabled={submitting}
                style={{
                  padding: '12px 28px',
                  minHeight: '44px',
                  background: submitting ? 'var(--surface-3)' : 'var(--accent)',
                  color: submitting ? 'var(--text-4)' : '#fff',
                  border: 'none',
                  borderRadius: 'var(--radius-sm)',
                  cursor: submitting ? 'not-allowed' : 'pointer',
                  fontSize: '14px',
                  fontWeight: 600,
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Add URL
              </button>
            </div>
          )}
        </>
      )}

      {/* Document list */}
      {(documents.length > 0 || optimisticDocs.length > 0) && (
        <div style={{ marginTop: '32px' }}>
          <h2
            style={{
              fontSize: '14px',
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              color: 'var(--text-3)',
              marginBottom: '12px',
            }}
          >
            Knowledge Base ({documents.length + optimisticDocs.length})
          </h2>
          <div
            style={{
              border: '1px solid var(--border-soft)',
              borderRadius: 'var(--radius-xs)',
              overflow: 'hidden',
            }}
          >
            {/* Optimistic in-flight rows — shown while the ingestion job runs,
                replaced by real rows once the documents query refetches. */}
            {optimisticDocs.map((doc) => (
              <div
                key={doc.clientKey}
                aria-busy="true"
                style={{
                  padding: '14px 16px',
                  background: 'var(--surface-2)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                }}
              >
                {/* Source type badge */}
                <span
                  style={{
                    padding: '2px 8px',
                    borderRadius: '999px',
                    fontSize: '10px',
                    fontWeight: 700,
                    background: 'var(--surface-3)',
                    color: 'var(--text-3)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                    whiteSpace: 'nowrap',
                    flexShrink: 0,
                  }}
                >
                  {doc.source_type}
                </span>

                {/* Title */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: '13px',
                      fontWeight: 600,
                      color: 'var(--text-2)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {doc.title}
                  </div>
                </div>

                {/* Processing badge (live label tracks the SSE progress; the
                    elapsed counter signals the job is still alive). */}
                <span
                  style={{
                    padding: '3px 10px',
                    borderRadius: '999px',
                    fontSize: '11px',
                    fontWeight: 600,
                    background: 'var(--amber-bg)',
                    color: 'var(--amber)',
                    whiteSpace: 'nowrap',
                    flexShrink: 0,
                  }}
                >
                  {progressLabel ?? 'processing…'}
                  {jobStartedAt !== null && ` · ${formatElapsed(elapsedSeconds)}`}
                </span>
              </div>
            ))}

            {documents.map((doc, i) => {
              const sc = getParseStatusColor(doc.parse_status)
              return (
                <div
                  key={doc.id}
                  ref={(el) => {
                    rowRefs.current.set(doc.id, el)
                  }}
                  role="button"
                  tabIndex={0}
                  aria-haspopup="dialog"
                  aria-label={`View details for ${doc.title || doc.source_uri}`}
                  onClick={() => setSelectedDocId(doc.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setSelectedDocId(doc.id)
                    }
                  }}
                  style={{
                    padding: '14px 16px',
                    borderTop:
                      i > 0 || optimisticDocs.length > 0
                        ? '1px solid var(--border-soft)'
                        : undefined,
                    background: 'var(--surface-1)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    cursor: 'pointer',
                    transition: 'background 0.15s ease',
                  }}
                  onMouseEnter={(e) => {
                    ;(e.currentTarget as HTMLDivElement).style.background = 'var(--surface-2)'
                  }}
                  onMouseLeave={(e) => {
                    ;(e.currentTarget as HTMLDivElement).style.background = 'var(--surface-1)'
                  }}
                >
                  {/* Source type badge */}
                  <span
                    style={{
                      padding: '2px 8px',
                      borderRadius: '999px',
                      fontSize: '10px',
                      fontWeight: 700,
                      background: 'var(--surface-3)',
                      color: 'var(--text-3)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.06em',
                      whiteSpace: 'nowrap',
                      flexShrink: 0,
                    }}
                  >
                    {doc.source_type}
                  </span>

                  {/* Title / URI */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: '13px',
                        fontWeight: 600,
                        color: 'var(--text-1)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {doc.title || doc.source_uri}
                    </div>
                    {doc.chunk_count > 0 && (
                      <div style={{ fontSize: '11px', color: 'var(--text-4)', marginTop: '2px' }}>
                        {doc.chunk_count} chunk{doc.chunk_count !== 1 ? 's' : ''}
                      </div>
                    )}
                  </div>

                  {/* Parse status badge */}
                  <span
                    style={{
                      padding: '3px 10px',
                      borderRadius: '999px',
                      fontSize: '11px',
                      fontWeight: 600,
                      background: sc.bg,
                      color: sc.fg,
                      whiteSpace: 'nowrap',
                      flexShrink: 0,
                    }}
                  >
                    {doc.parse_status}
                  </span>

                  {/* Date */}
                  <span
                    style={{
                      fontSize: '11px',
                      color: 'var(--text-4)',
                      fontFamily: 'var(--font-mono)',
                      whiteSpace: 'nowrap',
                      flexShrink: 0,
                    }}
                  >
                    {new Date(doc.created_at).toLocaleDateString()}
                  </span>

                  {/* Delete button — stop propagation so it never opens the
                      detail modal; the row click handler must not fire. */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDeleteDoc(doc.id)
                    }}
                    onKeyDown={(e) => e.stopPropagation()}
                    disabled={deletingIds.has(doc.id)}
                    aria-label={`Delete ${doc.title || doc.source_uri}`}
                    style={{
                      flexShrink: 0,
                      background: 'none',
                      border: 'none',
                      cursor: deletingIds.has(doc.id) ? 'not-allowed' : 'pointer',
                      padding: '4px 6px',
                      borderRadius: 'var(--radius-xs)',
                      color: deletingIds.has(doc.id) ? 'var(--text-4)' : 'var(--text-3)',
                      fontSize: '14px',
                      lineHeight: 1,
                      opacity: deletingIds.has(doc.id) ? 0.4 : 1,
                      transition: 'color 0.15s ease, opacity 0.15s ease',
                    }}
                    onMouseEnter={(e) => {
                      if (!deletingIds.has(doc.id))
                        (e.currentTarget as HTMLButtonElement).style.color = 'var(--red)'
                    }}
                    onMouseLeave={(e) => {
                      ;(e.currentTarget as HTMLButtonElement).style.color = deletingIds.has(doc.id)
                        ? 'var(--text-4)'
                        : 'var(--text-3)'
                    }}
                  >
                    {deletingIds.has(doc.id) ? '…' : '×'}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
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
