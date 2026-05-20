'use client'
import { useState, useEffect, use } from 'react'
import { useAuth } from '@clerk/nextjs'
import Link from 'next/link'

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

const EVENT_LABELS: Record<string, string> = {
  'job.started': 'Starting...',
  parsing: 'Parsing documents...',
  chunking: 'Chunking text...',
  embedding: 'Generating embeddings...',
  'job.complete': 'Done!',
  'job.failed': 'Failed',
}

const PARSE_STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  complete: { bg: 'var(--green-bg)', fg: 'var(--green)' },
  pending: { bg: 'var(--amber-bg)', fg: 'var(--amber)' },
  processing: { bg: 'var(--amber-bg)', fg: 'var(--amber)' },
  failed: { bg: 'var(--red-bg)', fg: 'var(--red)' },
}

function getParseStatusColor(status: string) {
  return PARSE_STATUS_COLORS[status] ?? { bg: 'var(--surface-3)', fg: 'var(--text-3)' }
}

// ---------------------------------------------------------------------------
// IngestPage
// ---------------------------------------------------------------------------

export default function IngestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  const [agentStatus, setAgentStatus] = useState<string | null>(null)
  const [documents, setDocuments] = useState<Document[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  const [activeTab, setActiveTab] = useState<IngestTab>('file')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [urlInput, setUrlInput] = useState('')
  const [urlError, setUrlError] = useState<string | null>(null)

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [progressLabel, setProgressLabel] = useState<string | null>(null)

  // ---------------------------------------------------------------------------
  // Load agent status + documents on mount
  // ---------------------------------------------------------------------------

  const loadDocuments = async () => {
    try {
      const token = await getToken()
      if (!token) {
        setLoadError('Not authenticated. Please sign in.')
        return
      }
      const r = await fetch(`${apiBase}/api/v1/agents/${id}/documents`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data: { documents: Document[] } = await r.json()
      setDocuments(data.documents ?? [])
    } catch (err) {
      console.error(err)
      setLoadError('Failed to load documents. Please refresh.')
    }
  }

  useEffect(() => {
    const init = async () => {
      try {
        const token = await getToken()
        if (!token) {
          setLoadError('Not authenticated. Please sign in.')
          return
        }
        // Load agent status
        const ar = await fetch(`${apiBase}/api/v1/agents/${id}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!ar.ok) throw new Error(`HTTP ${ar.status}`)
        const agent = await ar.json()
        setAgentStatus(agent.status)

        // Load documents
        const dr = await fetch(`${apiBase}/api/v1/agents/${id}/documents`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!dr.ok) throw new Error(`HTTP ${dr.status}`)
        const data: { documents: Document[] } = await dr.json()
        setDocuments(data.documents ?? [])
      } catch (err) {
        console.error(err)
        setLoadError('Failed to load agent data. Please refresh.')
      }
    }
    init()
  }, [id, apiBase, getToken])

  // ---------------------------------------------------------------------------
  // SSE progress reader — fetch + ReadableStream (EventSource doesn't support headers)
  // ---------------------------------------------------------------------------

  const readSseProgress = async (eventsUrl: string, token: string) => {
    try {
      const resp = await fetch(`${apiBase}${eventsUrl}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!resp.body) return

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const chunk = decoder.decode(value, { stream: true })
        for (const line of chunk.split('\n')) {
          if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.slice(6))
              const label = EVENT_LABELS[payload.event] ?? payload.event
              setProgressLabel(label)
              if (payload.event === 'job.complete') {
                await loadDocuments()
                setSubmitting(false)
                return
              }
              if (payload.event === 'job.failed') {
                setSubmitError('Ingestion failed. Check the Celery worker logs.')
                setSubmitting(false)
                return
              }
            } catch {
              // ignore malformed lines
            }
          }
        }
      }
    } catch (err) {
      console.error('SSE read error:', err)
      setSubmitError('Lost connection to job stream. Check if the job completed.')
      setSubmitting(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Submit handler
  // ---------------------------------------------------------------------------

  const handleSubmit = async () => {
    setSubmitError(null)
    setProgressLabel(null)

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
      if (!selectedFile) {
        setSubmitError('Please select a file.')
        return
      }
    }

    setSubmitting(true)
    setProgressLabel('Starting...')

    try {
      const token = await getToken()
      if (!token) {
        setSubmitError('Not authenticated. Please sign in.')
        setSubmitting(false)
        return
      }

      const formData = new FormData()
      if (activeTab === 'file' && selectedFile) {
        formData.append('files', selectedFile)
      } else if (activeTab === 'url') {
        formData.append('urls', urlInput.trim())
      }

      // POST multipart/form-data — do NOT set Content-Type (browser sets multipart boundary)
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/documents`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      })

      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        const detail = body?.detail ?? `HTTP ${res.status}`
        setSubmitError(`Upload failed: ${detail}`)
        setSubmitting(false)
        return
      }

      const data: { job_id: string; events_url: string } = await res.json()

      // Reset inputs
      setSelectedFile(null)
      setUrlInput('')

      // Stream SSE progress
      await readSseProgress(data.events_url, token)
    } catch (err) {
      console.error(err)
      setSubmitError('Upload failed. Please try again.')
      setSubmitting(false)
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
      <Link
        href={`/agents/${id}`}
        style={{
          fontSize: '14px',
          color: 'var(--accent)',
          textDecoration: 'none',
          display: 'inline-flex',
          alignItems: 'center',
          gap: '6px',
          marginBottom: '24px',
        }}
      >
        ← Back to Configure
      </Link>

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
        Upload PDFs, images, or URLs for your agent&apos;s knowledge base.
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

          {/* Progress indicator */}
          {submitting && progressLabel && (
            <div
              style={{
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
              {progressLabel}
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
              <input
                type="file"
                accept=".pdf,.png,.jpg,.jpeg"
                onChange={(e) => {
                  setSelectedFile(e.target.files?.[0] ?? null)
                  setSubmitError(null)
                }}
                disabled={submitting}
                style={{
                  display: 'block',
                  marginBottom: '16px',
                  fontSize: '14px',
                  color: 'var(--text-2)',
                  fontFamily: 'var(--font-sans)',
                }}
              />
              <button
                onClick={handleSubmit}
                disabled={submitting || !selectedFile}
                style={{
                  padding: '12px 28px',
                  minHeight: '44px',
                  background: submitting || !selectedFile ? 'var(--surface-3)' : 'var(--accent)',
                  color: submitting || !selectedFile ? 'var(--text-4)' : '#fff',
                  border: 'none',
                  borderRadius: 'var(--radius-sm)',
                  cursor: submitting || !selectedFile ? 'not-allowed' : 'pointer',
                  fontSize: '14px',
                  fontWeight: 600,
                  fontFamily: 'var(--font-sans)',
                }}
              >
                {submitting ? 'Uploading…' : 'Upload file'}
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
                {submitting ? 'Adding…' : 'Add URL'}
              </button>
            </div>
          )}
        </>
      )}

      {/* Document list */}
      {documents.length > 0 && (
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
            Knowledge Base ({documents.length})
          </h2>
          <div
            style={{
              border: '1px solid var(--border-soft)',
              borderRadius: 'var(--radius-xs)',
              overflow: 'hidden',
            }}
          >
            {documents.map((doc, i) => {
              const sc = getParseStatusColor(doc.parse_status)
              return (
                <div
                  key={doc.id}
                  style={{
                    padding: '14px 16px',
                    borderTop: i > 0 ? '1px solid var(--border-soft)' : undefined,
                    background: 'var(--surface-1)',
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
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
