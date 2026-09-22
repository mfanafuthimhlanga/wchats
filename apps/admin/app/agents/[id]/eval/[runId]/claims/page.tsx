'use client'
import { use, useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import ClaimsReview, { SaveError, type SaveSitting } from './ClaimsReview'
import { DEMO_FLAGGED } from './demoFixture'
import type { FlaggedClaimsResponse } from './sitting'

/**
 * `/agents/[id]/eval/[runId]/claims`: the review of the claims the judge
 * flagged on one run (#290 step 3). Reads `GET .../eval-runs/{run}/claims`,
 * writes each sitting through `POST .../claims/review`. The fetch pattern is
 * the one eval/page.tsx keeps: a Clerk token on every call, react-query for
 * the read.
 */
export default function ClaimsPage({ params }: { params: Promise<{ id: string; runId: string }> }) {
  const { id, runId } = use(params)
  const { getToken, isLoaded, isSignedIn } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const isDemo = process.env.NEXT_PUBLIC_DEMO === 'true'
  const url = `${apiBase}/api/v1/agents/${id}/eval-runs/${runId}/claims`
  const backHref = `/agents/${id}/eval`
  const queryClient = useQueryClient()

  const query = useQuery<FlaggedClaimsResponse>({
    queryKey: ['eval-claims', id, runId],
    queryFn: async () => {
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json()
    },
    enabled: !isDemo && isLoaded && !!isSignedIn,
    staleTime: 30_000,
  })

  const save = useCallback<SaveSitting>(
    async (body) => {
      if (isDemo) return
      const token = await getToken()
      if (!token) throw new Error('Not authenticated')
      const res = await fetch(`${url}/review`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new SaveError(res.status)
      // the eval page reads the same key for its answered count
      void queryClient.invalidateQueries({ queryKey: ['eval-claims', id, runId] })
    },
    [isDemo, getToken, url, queryClient, id, runId],
  )

  if (isDemo) return <ClaimsReview data={DEMO_FLAGGED} save={save} backHref={backHref} />

  if (query.isError) {
    return (
      <div className="page">
        <header className="page-head">
          <h1>Flagged claims</h1>
        </header>
        <p role="alert" style={{ color: 'var(--fail)' }}>
          Could not load the flagged claims. {query.error.message}
        </p>
      </div>
    )
  }

  if (!query.data) {
    return (
      <div className="page" aria-busy="true">
        <header className="page-head">
          <h1>Flagged claims</h1>
        </header>
        <div aria-hidden="true">
          {[220, 44, 44].map((h, i) => (
            <div key={i} style={{ height: h, borderRadius: 'var(--r-panel)', background: 'var(--surface)', marginBottom: 8 }} />
          ))}
        </div>
      </div>
    )
  }

  return <ClaimsReview data={query.data} save={save} backHref={backHref} />
}
