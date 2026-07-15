'use client'
import Link from 'next/link'
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
  'metadata.progress': 'Extracting metadata & entities...',
  'metadata.complete': 'Metadata extracted',
  'embedding.started': 'Generating embeddings...',
  'embedding.complete': 'Embeddings done',
  'strategy.synthesized': 'Optimising retrieval strategy...',
  'ingestion.complete': 'Processing complete',
  'job.complete': 'Done!',
  'job.failed': 'Failed',
}

const PARSE_STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  complete: { bg: 'var(--green-bg)', fg: 'var(--green)' },
  parsed: { bg: 'var(--green-bg)', fg: 'var(--green)' },
  pending: { bg: 'var(--gold-bg)', fg: 'var(--gold)' },
  processing: { bg: 'var(--gold-bg)', fg: 'var(--gold)' },
  failed: { bg: 'var(--red-bg)', fg: 'var(--red)' },
}

function getParseStatusColor(status: string) {
  return PARSE_STATUS_COLORS[status] ?? { bg: 'var(--chip)', fg: 'var(--text-3)' }
}

// Shared uppercase micro-label spec — sentence case is the norm everywhere else;
// these tracked labels are the one exception. 11px / 600 / 0.12em tracking.
const microLabel: React.CSSProperties = {
  fontSize: '11px',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.12em',
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
        margin: '0 auto',
        fontFamily: 'var(--font-sans)',
      }}
    >
      <h1
        className="on-photo"
        style={{
          fontSize: '22px',
          fontWeight: 400,
          fontFamily: 'var(--font-display)',
          fontVariationSettings: '"opsz" 144, "SOFT" 30',
          color: 'var(--text-1)',
          marginBottom: '8px',
        }}
      >
        Ingest documents
      </h1>

      <p
        className="on-photo"
        style={{
          color: 'var(--text-2)',
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
            border: '1px solid rgba(248,113,113,0.3)',
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
            background: 'var(--gold-bg)',
            border: '1px solid rgba(251,191,36,0.3)',
            borderRadius: 'var(--radius-xs)',
            fontSize: '14px',
            color: 'var(--gold)',
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
                {tab === 'file' ? 'Upload file' : 'Add URL'}
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
                border: '1px solid rgba(248,113,113,0.3)',
                borderRadius: 'var(--radius-xs)',
                fontSize: '14px',
                color: 'var(--red)',
              }}
            >
              {submitError}
            </div>
          )}

          {/* Tab: Upload File */}
          {activeTab === 'file' && (
            <div style={{ marginBottom: '24px' }}>
              <label
                style={{
                  ...microLabel,
                  display: 'block',
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
                  border: `2px dashed ${isDragging ? 'var(--border-hard)' : 'var(--border)'}`,
                  borderRadius: 'var(--radius-sm)',
                  background: isDragging ? 'var(--accent-dim)' : 'var(--well)',
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
                    submitting || selectedFiles.length === 0 ? 'var(--chip)' : 'var(--accent)',
                  color:
                    submitting || selectedFiles.length === 0
                      ? 'var(--text-3)'
                      : 'var(--text-on-accent)',
                  border: 'none',
                  borderRadius: 'var(--radius-xs)',
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
                  ...microLabel,
                  display: 'block',
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
                  background: 'var(--well)',
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
                  background: submitting ? 'var(--chip)' : 'var(--accent)',
                  color: submitting ? 'var(--text-3)' : 'var(--text-on-accent)',
                  border: 'none',
                  borderRadius: 'var(--radius-xs)',
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
              ...microLabel,
              color: 'var(--text-3)',
              marginBottom: '12px',
            }}
          >
            Knowledge base ({documents.length + optimisticDocs.length})
          </h2>
          <div
            className="glass-strong"
            style={{
              borderRadius: 'var(--radius-md)',
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
                  borderBottom: '1px solid var(--border-soft)',
                  background: 'var(--chip)',
                  opacity: 0.75,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                }}
              >
                {/* Source type badge */}
                <span
                  style={{
                    padding: '2px 8px',
                    borderRadius: 'var(--radius-pill)',
                    fontSize: '10px',
                    fontWeight: 700,
                    background: 'var(--lilac-dim)',
                    color: 'var(--lilac)',
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
                    borderRadius: 'var(--radius-pill)',
                    fontSize: '11px',
                    fontWeight: 600,
                    background: 'var(--gold-bg)',
                    color: 'var(--gold)',
                    whiteSpace: 'nowrap',
                    flexShrink: 0,
                  }}
                >
                  {progressLabel ?? 'processing…'}
                  {jobStartedAt !== null && (
                    <span style={{ fontFamily: 'var(--font-mono)' }}>
                      {' '}· {formatElapsed(elapsedSeconds)}
                    </span>
                  )}
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
                    borderBottom:
                      i < documents.length - 1 ? '1px solid var(--border-soft)' : undefined,
                    background: 'transparent',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    cursor: 'pointer',
                    transition: 'background 0.15s ease',
                  }}
                  onMouseEnter={(e) => {
                    ;(e.currentTarget as HTMLDivElement).style.background = 'var(--chip)'
                  }}
                  onMouseLeave={(e) => {
                    ;(e.currentTarget as HTMLDivElement).style.background = 'transparent'
                  }}
                >
                  {/* Source type badge */}
                  <span
                    style={{
                      padding: '2px 8px',
                      borderRadius: 'var(--radius-pill)',
                      fontSize: '10px',
                      fontWeight: 700,
                      background: 'var(--lilac-dim)',
                      color: 'var(--lilac)',
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
                        fontFamily: doc.title ? 'var(--font-sans)' : 'var(--font-mono)',
                        color: 'var(--text-2)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {doc.title || doc.source_uri}
                    </div>
                    {doc.chunk_count > 0 && (
                      <div style={{ fontSize: '11px', color: 'var(--text-3)', marginTop: '2px' }}>
                        {doc.chunk_count} chunk{doc.chunk_count !== 1 ? 's' : ''}
                      </div>
                    )}
                  </div>

                  {/* Parse status badge */}
                  <span
                    style={{
                      padding: '3px 10px',
                      borderRadius: 'var(--radius-pill)',
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
                      color: 'var(--text-3)',
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

      {/* Next step CTA — visible once at least one document is successfully ingested */}
      {documents.some((d) => d.parse_status !== 'failed') && (
        <div
          style={{
            marginTop: '32px',
            paddingTop: '24px',
            borderTop: '1px solid var(--border-soft)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <p style={{ fontSize: '14px', color: 'var(--text-3)', margin: 0 }}>
            Knowledge base ready — you can now run evaluations.
          </p>
          <Link
            href={`/agents/${id}/eval`}
            style={{
              display: 'inline-block',
              padding: '12px 28px',
              minHeight: '44px',
              background: 'var(--accent)',
              color: 'var(--text-on-accent)',
              borderRadius: 'var(--radius-xs)',
              fontSize: '15px',
              fontWeight: 600,
              textDecoration: 'none',
              whiteSpace: 'nowrap',
            }}
          >
            Next: Run evals →
          </Link>
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
