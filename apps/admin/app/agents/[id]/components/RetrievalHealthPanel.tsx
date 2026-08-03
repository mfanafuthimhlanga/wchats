'use client'
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import EmptyState from '../../../components/gotham/EmptyState'
import Chip from '../../../components/gotham/Chip'
import Ledger, { LedgerCell, LedgerColHead, LedgerRowHead } from '../../../components/gotham/Ledger'
import {
  METRICS_SENTINEL,
  RETRIEVAL_SENTINEL,
  isMetricsSentinel,
  isRetrievalSentinel,
  formatPercent,
  formatInteger,
  renderRetrievalAverageCell,
  renderStalenessField,
} from './opsFormat'

/**
 * The Retrieval health region (WIRE-01, WIRE-03, 23-05) —
 * GET /agents/{id}/retrieval-health (apps/api/app/api/v1/metrics.py:111-165).
 * House query shape, same as LivePanel. Three sub-blocks, each independently
 * honest about whether it has data: the context-window bar, the twelve-row
 * readings ledger, and the index-staleness tile row.
 *
 * Two different sentinel spellings govern this one payload — the spaced
 * form on every avg_* average (retrieval_metrics_service.py:145) means "no
 * queries in this window yet", the underscore form on the staleness fields
 * (staleness.py:65, same constant metrics.py uses) means "the scan itself
 * failed". They are checked with two independently-named predicates from
 * the shared pure layer; neither group is ever checked with the other's
 * predicate.
 */

interface IndexStaleness {
  stale_count: number | typeof METRICS_SENTINEL
  stale_document_ids: string[]
  drift_detected: boolean | typeof METRICS_SENTINEL
  drift_model_counts: Record<string, number> | typeof METRICS_SENTINEL
  current_embedding_model: string
}

interface RetrievalHealthResponse {
  sample_count: number
  avg_bm25_top_score: number | typeof RETRIEVAL_SENTINEL
  avg_vector_top_score: number | typeof RETRIEVAL_SENTINEL
  avg_rrf_top_score: number | typeof RETRIEVAL_SENTINEL
  avg_rerank_top_score: number | typeof RETRIEVAL_SENTINEL
  avg_reranker_lift: number | typeof RETRIEVAL_SENTINEL
  avg_recall_at_k: number | typeof RETRIEVAL_SENTINEL
  avg_ndcg_at_10: number | typeof RETRIEVAL_SENTINEL
  avg_mrr: number | typeof RETRIEVAL_SENTINEL
  avg_cited_chunk_rank: number | typeof RETRIEVAL_SENTINEL
  avg_retrieved_tokens: number | typeof RETRIEVAL_SENTINEL
  avg_ctx_window_utilization: number | typeof RETRIEVAL_SENTINEL
  avg_carried_never_cited_tokens: number | typeof RETRIEVAL_SENTINEL
  avg_compaction_ratio: number | typeof RETRIEVAL_SENTINEL
  avg_citation_coverage: number | typeof RETRIEVAL_SENTINEL
  avg_faithfulness: number | typeof RETRIEVAL_SENTINEL
  index_staleness: IndexStaleness
}

export default function RetrievalHealthPanel({
  agentId,
  documentCount,
  enabled,
  onError,
}: {
  agentId: string
  documentCount: number
  enabled: boolean
  onError: (region: string, message: string | null) => void
}) {
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

  const healthQuery = useQuery({
    queryKey: ['retrieval-health', agentId],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const r = await fetch(`${apiBase}/api/v1/agents/${agentId}/retrieval-health`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return (await r.json()) as RetrievalHealthResponse
    },
    enabled: enabled && documentCount > 0,
    staleTime: 15_000,
  })

  useEffect(() => {
    if (healthQuery.isError) {
      onError('retrieval-health', (healthQuery.error as Error).message || 'Failed to load retrieval health.')
    } else {
      onError('retrieval-health', null)
    }
  }, [healthQuery.isError, healthQuery.error, onError])

  // The zero-document case comes first, before the query result is
  // considered at all — there is nothing to retrieve from, which is a
  // materially different absence from an empty measurement window.
  if (documentCount === 0) {
    return (
      <EmptyState
        heading="No documents to retrieve from yet"
        body="Retrieval health has nothing to measure until this agent has a knowledge base."
        linkHref={`/agents/${agentId}/ingest`}
        linkLabel="Go to Ingest"
      />
    )
  }

  const data = healthQuery.data

  if (!data) {
    // On a genuine failure the region reports through onError above and
    // renders nothing else — the page owns the one error banner. While
    // still pending (not yet errored) a small note holds the section's
    // place, matching the "Fetching…" convention already on this page.
    return healthQuery.isError ? null : <p className="foot-note">Fetching retrieval health…</p>
  }

  const staleCount = data.index_staleness.stale_count
  const driftDetected = data.index_staleness.drift_detected
  // The two independent scan queries (staleness.py) can fail separately —
  // if EITHER degraded to the underscore sentinel, the block renders the
  // one scan-unavailable sentence rather than mixing a real number with a
  // truthy-but-sentinel string coerced into a false "drift" chip.
  const stalenessScanFailed = isMetricsSentinel(staleCount) || isMetricsSentinel(driftDetected)

  return (
    <>
      {isRetrievalSentinel(data.avg_ctx_window_utilization) ? (
        <p className="foot-note">{renderRetrievalAverageCell(data.avg_ctx_window_utilization)}</p>
      ) : (
        <>
          {/* 23-09 adversarial review (UI-3): this fill used var(--live), which
              is not one of GOTHAM's reserved --live usages (20-UI-SPEC.md §4) —
              it is a passive measurement, not a verdict. --live also inherits
              --seal (red) site-wide whenever the deploy gate is blocked
              (globals.css :root[data-gate="blocked"]), which painted a healthy
              7.1% utilisation reading in alarm-red during the rendered review.
              --ink-2 is bone-neutral and untouched by the gate cascade, so the
              bar now stays a plain instrument reading regardless of gate
              state, matching the fix hint: "bone/neutral unless it crosses a
              defined threshold" (no threshold is defined for this reading). */}
          <p className="mono help" style={{ marginBottom: 6 }}>
            {formatPercent(Math.min(1, Math.max(0, data.avg_ctx_window_utilization)))} of the context window
          </p>
          <div
            role="progressbar"
            aria-label="Context window utilisation"
            aria-valuenow={Math.round(Math.min(1, Math.max(0, data.avg_ctx_window_utilization)) * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            style={{
              height: 8,
              border: '1px solid var(--hairline)',
              borderRadius: 'var(--r-control)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                height: '100%',
                width: formatPercent(Math.min(1, Math.max(0, data.avg_ctx_window_utilization))),
                background: 'var(--ink-2)',
              }}
            />
          </div>
          {/* 23-09 adversarial review: avg_retrieved_tokens and
              avg_carried_never_cited_tokens are independently-typed sentinel
              unions. renderRetrievalAverageCell returns a full sentence
              ("No queries in this window yet.") for the sentinel case, built
              to REPLACE a value, not fill a numeral slot mid-sentence —
              interpolating that sentence into "{sentence} tokens retrieved ·
              {n} carried but never cited" would read as a broken composite.
              Both fields share the same underlying zero-rows cause as
              avg_ctx_window_utilization (retrieval_metrics_service.py's one
              query populates the whole avg_* group together), so this guards
              the whole line rather than trusting that lockstep at the type
              level. */}
          {isRetrievalSentinel(data.avg_retrieved_tokens) || isRetrievalSentinel(data.avg_carried_never_cited_tokens) ? (
            <p className="mono help">No queries in this window yet.</p>
          ) : (
            <p className="mono help">
              {renderRetrievalAverageCell(data.avg_retrieved_tokens, formatInteger)} tokens retrieved ·{' '}
              {renderRetrievalAverageCell(data.avg_carried_never_cited_tokens, formatInteger)} carried but never cited
            </p>
          )}
        </>
      )}

      <div className="scroll-x">
        <Ledger caption="Retrieval readings for this window. Twelve independent measurements, each honest about whether it has data yet.">
          <thead>
            <tr>
              <LedgerColHead>Reading</LedgerColHead>
              <LedgerColHead numeric>Value</LedgerColHead>
            </tr>
          </thead>
          <tbody>
            <tr>
              <LedgerRowHead>BM25 top score</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_bm25_top_score)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>Vector top score</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_vector_top_score)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>RRF top score</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_rrf_top_score)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>Rerank top score</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_rerank_top_score)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>Reranker lift</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_reranker_lift)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>Recall@k</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_recall_at_k)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>nDCG@10</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_ndcg_at_10)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>MRR</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_mrr)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>Cited-chunk rank</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_cited_chunk_rank)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>Compaction ratio</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_compaction_ratio)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>Citation coverage</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_citation_coverage)}</LedgerCell>
            </tr>
            <tr>
              <LedgerRowHead>Faithfulness</LedgerRowHead>
              <LedgerCell numeric className="mono">{renderRetrievalAverageCell(data.avg_faithfulness)}</LedgerCell>
            </tr>
          </tbody>
        </Ledger>
      </div>

      {stalenessScanFailed ? (
        <p className="foot-note">{renderStalenessField(METRICS_SENTINEL)}</p>
      ) : (
        <div className="sev">
          <div className="sev-cell">
            <span className="num sev-n">{renderStalenessField(staleCount)}</span>
            <span className="label">Stale documents</span>
          </div>
          <div className="sev-cell">
            <Chip verdict={driftDetected ? 'fail' : 'pass'}>{driftDetected ? 'Drift detected' : 'No drift'}</Chip>
            <span className="label">Embedding drift</span>
          </div>
          <div className="sev-cell" style={{ minWidth: 0 }}>
            {/* 23-09 adversarial review: no wrap guard existed on this
                identifier string; defensively guarded against overflow at
                narrow widths rather than trusting every embedding model
                name to contain a soft-wrap-friendly hyphen. */}
            <span className="mono sev-n" style={{ overflowWrap: 'break-word' }}>
              {data.index_staleness.current_embedding_model}
            </span>
            <span className="label">Embedding model</span>
          </div>
        </div>
      )}
    </>
  )
}
