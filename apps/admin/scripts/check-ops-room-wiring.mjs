#!/usr/bin/env node
// check-ops-room-wiring.mjs
//
// WIRE-01 / WIRE-02 / WIRE-03 / WIRE-04 standing gate (23-03, Phase 23
// Wave 0/1). Modelled line-for-line on the shipped check-no-dusk-tokens.mjs
// (same walk, same comment-stripping pass, same finding shape, same
// report-then-exit structure) — this is that gate's own template, not
// merely its inspiration.
//
// What this gate is for: .planning/v1.2-MILESTONE-AUDIT.md found five
// integration defects that shipped undetected through two phases that had
// both already passed their own verification. Every one of those findings
// was a grep the audit ran by hand. This script is those greps, made
// permanent and runnable on demand — the standing version of exactly the
// check that caught WIRE-01 through WIRE-04.
//
// Two check families:
//   - Honesty checks: is a false "ships in a future release" claim gone, is
//     the false "evaluated automatically each night" schedule claim gone
//     (BACKLOG 7.6 — no beat worker is deployed anywhere), and are the two
//     Judgement ledger tiles reading real data instead of hardcoding "not
//     tracked yet" over it (WIRE-02, WIRE-03)?
//   - Reachability checks: does each operations-room region actually call
//     the Phase 21 endpoint it needs (WIRE-01, WIRE-04), and does the deploy
//     page READ the embed tag from the API rather than composing a second,
//     disagreeing one of its own (BACKLOG 7.1)?
//
// Scan scope grew with those two: the honesty family was already app-wide,
// and the embed check reads the deploy page specifically.
//
// Self-exclusion: this file lives in apps/admin/scripts/. Every literal it
// searches for is written into its own source below, so it must never scan
// its own directory — the same self-exclusion the token gate already
// relies on for its retired-token literals. Scan scope is the admin app
// directory (and the operations room's own subtree within it) only; this
// file's own directory is never a scan root.
//
// Expected to exit non-zero until the wiring plans in this phase land:
// 23-05 (Live, Retrieval health, Judgement), 23-06 (Adversary), 23-07 (The
// prompt), 23-08 (The bench, plus the final all-six-regions check). Each of
// those plans asserts, in its own <verify> block, that its own checks below
// have flipped to PASS — a red result here is expected, not broken, until
// the plan named next to each check has landed. Run with --report to see
// every check's current status without failing the build.

import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs'
import { extname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
const ADMIN_ROOT = join(__dirname, '..')
const APP_ROOT = join(ADMIN_ROOT, 'app')
const OPS_ROOM_PAGE = join(ADMIN_ROOT, 'app', 'agents', '[id]', 'page.tsx')
const OPS_ROOM_COMPONENTS_DIR = join(ADMIN_ROOT, 'app', 'agents', '[id]', 'components')
const DEPLOY_PAGE = join(ADMIN_ROOT, 'app', 'agents', '[id]', 'deploy', 'page.tsx')

const EXCLUDE_DIRS = new Set(['node_modules', '.next', '.git'])

// Same text-extension allowlist as check-no-dusk-tokens.mjs — binary/asset
// files are never scanned as text.
const TEXT_EXTENSIONS = new Set([
  '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs',
  '.css', '.scss',
  '.html', '.mdx', '.md',
  '.json', '.svg', '.txt',
])

/** Strip `/* ... *\/` block comments (preserving newlines for line numbers)
 * and, for JS/TS-family files, `//` line comments — copied from
 * check-no-dusk-tokens.mjs's own stripComments, so a false claim or a
 * route fragment reintroduced only inside an explanatory comment does not
 * trip either family of check below (T-23-VAL-01). */
function stripComments(content, ext) {
  let out = content.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  const isJsLike = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs'].includes(ext)
  if (isJsLike) {
    out = out
      .split('\n')
      .map((line) => {
        const idx = line.indexOf('//')
        return idx === -1 ? line : line.slice(0, idx)
      })
      .join('\n')
  }
  return out
}

function walk(dir, files = []) {
  let entries
  try {
    entries = readdirSync(dir)
  } catch {
    return files // dir may not exist in every checkout
  }
  for (const entry of entries) {
    if (EXCLUDE_DIRS.has(entry)) continue
    const full = join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) {
      walk(full, files)
    } else {
      files.push(full)
    }
  }
  return files
}

/** Reads and comment-strips every text-extension file at the given paths. */
function readTextFiles(paths) {
  const files = []
  for (const full of paths) {
    const ext = extname(full)
    if (!TEXT_EXTENSIONS.has(ext)) continue
    let raw
    try {
      raw = readFileSync(full, 'utf8')
    } catch {
      continue
    }
    files.push({ full, rel: relative(ADMIN_ROOT, full), ext, stripped: stripComments(raw, ext) })
  }
  return files
}

/** Every hit of `literal` (a plain substring, not a regex) outside a comment, across `files`. */
function findLiteral(files, literal) {
  const hits = []
  for (const f of files) {
    const lines = f.stripped.split('\n')
    lines.forEach((line, i) => {
      if (line.includes(literal)) {
        hits.push({ file: f.rel, line: i + 1, snippet: line.trim().slice(0, 140) })
      }
    })
  }
  return hits
}

// ---------------------------------------------------------------------------
// The two file sets this gate reads once and re-uses across every check.
// APP_FILES: the whole admin app — the honesty checks' scope, because a
// false capability claim or the evasion phrase behind it would be exactly
// as dishonest anywhere else in the console, not only inside the
// operations room.
// OPS_ROOM_FILES: page.tsx plus everything under its own components/
// directory ONLY — never deploy/, soul/, ingest/, eval/, or settings/,
// which are separate pages under the same [id] route param and are not
// "the operations room" this gate is about. A broad scan across every
// sibling page would risk a false PASS if an unrelated page happened to
// mention the same fragment for an unrelated reason.
// ---------------------------------------------------------------------------

const APP_FILES = readTextFiles(walk(APP_ROOT))

const OPS_ROOM_PATHS = [
  ...(existsSync(OPS_ROOM_PAGE) ? [OPS_ROOM_PAGE] : []),
  ...walk(OPS_ROOM_COMPONENTS_DIR),
]
const OPS_ROOM_FILES = readTextFiles(OPS_ROOM_PATHS)

// The deploy page on its own — the embed check below is about that one page
// building an artifact it must instead read, so a hit anywhere else in the
// console would be a different fact and must not answer for it.
const DEPLOY_PAGE_FILES = readTextFiles(existsSync(DEPLOY_PAGE) ? [DEPLOY_PAGE] : [])

// ---------------------------------------------------------------------------
// Honesty checks (WIRE-02, WIRE-03)
// ---------------------------------------------------------------------------

// The three literals, read out of page.tsx this session rather than
// retyped from memory — a gate that greps for a near-miss of the real
// string is worse than no gate. Quoted at 23-UI-SPEC.md §5 and
// v1.2-MILESTONE-AUDIT.md:162-164.
const FUTURE_RELEASE_CLAIMS = [
  {
    id: 'no-retrieval-health-future-claim',
    region: 'Retrieval health',
    flippedBy: '23-05',
    literal: 'Retrieval health instrumentation ships in a future release.',
  },
  {
    id: 'no-coverage-future-claim',
    region: 'Adversary',
    flippedBy: '23-06',
    literal:
      'Per-strategy coverage detail ships in a future release; showing the latest run summary above.',
  },
  {
    id: 'no-prompt-versions-future-claim',
    region: 'The prompt',
    flippedBy: '23-07',
    literal: 'Version history, canary releases and rollback ship in a future release.',
  },
]

// The phrase all three share verbatim — not "ships in a future release"
// (claim 3 reads "...rollback ship in a future release.", singular "ship",
// no trailing "s"), but "in a future release", which is present character
// for character in all three regardless of which verb precedes it. Catches
// the CLASS of evasion, not just these three instances, so the next author
// reaching for the same phrase trips this before review.
const FUTURE_RELEASE_PHRASE = 'in a future release'

function runHonestyChecks() {
  const results = FUTURE_RELEASE_CLAIMS.map((claim) => {
    const hits = findLiteral(APP_FILES, claim.literal)
    return {
      id: claim.id,
      region: claim.region,
      flippedBy: claim.flippedBy,
      pass: hits.length === 0,
      evidence: hits,
    }
  })

  const phraseHits = findLiteral(APP_FILES, FUTURE_RELEASE_PHRASE)
  results.push({
    id: 'no-future-release-evasion-phrase',
    region: 'all',
    flippedBy: '23-07 (last of the three claims to land)',
    pass: phraseHits.length === 0,
    evidence: phraseHits,
  })

  return results
}

// The Judgement check is a different shape from the three above: it is not
// a bare count-of-zero, because the operations room legitimately retains
// one occurrence of "not tracked yet" verbatim, at the per-scenario Added
// column (EvalScenarioResult carries no timestamp field — 23-UI-SPEC.md
// §4.4 point 4). So this check does not search for that phrase at all.
// Instead it asserts two things: the two WIRE-02 tiles reference the real
// ledger counts, and the CSS class that styles the two hardcoded "not
// tracked yet" tiles (`chan-untracked`, PAGE_CSS in page.tsx) has zero
// remaining usages anywhere in the app. That class has exactly one
// legitimate consumer today — the two WIRE-02 tiles — and no other region
// uses it (each region's own honest-empty copy uses its own class or plain
// text), so a flat usage count is a precise, non-brittle signal without
// needing to locate the Judgement <section>'s own start/end markers in the
// markup. Recorded here per 23-03-PLAN.md's own escape hatch: "prefer
// asserting the exact expected occurrence count of the untracked class in
// the file over a bare zero... and record which form was used and why."
function runJudgementLedgerCheck() {
  const bornHits = findLiteral(APP_FILES, 'born_in_production_count')
  const authoredHits = findLiteral(APP_FILES, 'authored_count')
  const untrackedTileUsages = findLiteral(APP_FILES, 'className="chan-untracked"')

  const pass = bornHits.length > 0 && authoredHits.length > 0 && untrackedTileUsages.length === 0

  const evidence = []
  if (bornHits.length === 0) evidence.push('born_in_production_count is not referenced anywhere in apps/admin/app')
  if (authoredHits.length === 0) evidence.push('authored_count is not referenced anywhere in apps/admin/app')
  evidence.push(...untrackedTileUsages)

  return {
    id: 'judgement-tiles-honest',
    region: 'Judgement',
    flippedBy: '23-05',
    pass,
    evidence: pass ? [] : evidence,
  }
}

// A false claim about a SCHEDULE, which is the same defect class as the three
// above wearing different clothes (BACKLOG 7.6). The eval page's empty state
// read "Your agent is evaluated automatically each night." No beat worker is
// deployed anywhere — scheduled evals ship in M4 of the masterplan — so the
// console was telling an owner a run had happened when none ever had, which is
// worse than the "ships in a future release" evasions because it is not even
// deferring: it is asserting. Both a literal check and the phrase behind it,
// same shape as FUTURE_RELEASE_PHRASE, so the class is caught and not just the
// one sentence.
const SCHEDULED_EVAL_CLAIM = 'Your agent is evaluated automatically each night.'
const SCHEDULED_EVAL_PHRASE = 'automatically each night'

function runScheduledEvalClaimCheck() {
  const hits = [
    ...findLiteral(APP_FILES, SCHEDULED_EVAL_CLAIM),
    ...findLiteral(APP_FILES, SCHEDULED_EVAL_PHRASE),
  ]
  return {
    id: 'no-scheduled-eval-claim',
    region: 'Eval',
    flippedBy: 'BACKLOG 7.6',
    pass: hits.length === 0,
    evidence: hits,
  }
}

// ---------------------------------------------------------------------------
// Single-generator check (BACKLOG 7.1)
//
// The embed <script> tag had two generators that disagreed: the API's
// (hardcoded CDN host, no data-api at all) and the console's own, computed in
// the browser from NEXT_PUBLIC_WCHATS_* with an empty-string API-base default.
// The loader warns and continues on a missing API base, so the console could
// hand an owner a tag that renders a widget which can never reach the API.
//
// The API is the single generator now. This check is the standing form of that
// decision: the deploy page must CALL the endpoint, and must contain none of
// the fragments a hand-built tag needs. Both halves matter — the absence check
// alone would pass on a page that had simply deleted the Embed section.
// ---------------------------------------------------------------------------

const EMBED_BUILD_FRAGMENTS = [
  '<script src=',          // the tag being assembled as a string
  'data-agent=',           // its required attributes, written by hand
  'data-api=',
  'NEXT_PUBLIC_WCHATS_',   // the build-time env vars that fed the old generator
]

function runEmbedSnippetSourceCheck() {
  if (DEPLOY_PAGE_FILES.length === 0) {
    return {
      id: 'embed-snippet-single-generator',
      region: 'Deploy',
      flippedBy: 'BACKLOG 7.1',
      pass: false,
      evidence: [`deploy page not found at ${relative(ADMIN_ROOT, DEPLOY_PAGE)} — this check cannot be satisfied by the file's absence`],
    }
  }

  const readsEndpoint = findLiteral(DEPLOY_PAGE_FILES, 'embed-snippet')
  const builtLocally = EMBED_BUILD_FRAGMENTS.flatMap((fragment) =>
    findLiteral(DEPLOY_PAGE_FILES, fragment).map((hit) => ({
      ...hit,
      snippet: `builds the tag itself ("${fragment}"): ${hit.snippet}`,
    })),
  )

  const evidence = []
  if (readsEndpoint.length === 0) {
    evidence.push('the deploy page never calls GET /agents/{id}/embed-snippet — it must render what the API generated')
  }
  evidence.push(...builtLocally)

  return {
    id: 'embed-snippet-single-generator',
    region: 'Deploy',
    flippedBy: 'BACKLOG 7.1',
    pass: readsEndpoint.length > 0 && builtLocally.length === 0,
    evidence,
  }
}

// ---------------------------------------------------------------------------
// Reachability checks (WIRE-01, WIRE-04) — one per operations-room region,
// scoped to OPS_ROOM_FILES only. A check passes when every one of its
// fragments appears, outside a comment, somewhere under the operations
// room's own files. This is v1.2-MILESTONE-AUDIT.md's own zero-caller grep
// ("/metrics", "retrieval-health", "/traces", "prompt-versions",
// "red-team/programme", "/contain" all returned zero files) turned into a
// standing assertion.
// ---------------------------------------------------------------------------

const REACHABILITY_CHECKS = [
  { id: 'live-metrics-wired', region: 'Live', flippedBy: '23-05', fragments: ['/metrics'] },
  {
    id: 'retrieval-health-wired',
    region: 'Retrieval health',
    flippedBy: '23-05',
    fragments: ['retrieval-health'],
  },
  { id: 'bench-traces-wired', region: 'The bench', flippedBy: '23-08', fragments: ['/traces'] },
  {
    id: 'judgement-ledger-referenced',
    region: 'Judgement',
    flippedBy: '23-05',
    fragments: ['born_in_production_count', 'authored_count'],
  },
  {
    id: 'adversary-programme-and-contain-wired',
    region: 'Adversary',
    flippedBy: '23-06',
    fragments: ['red-team/programme', '/contain'],
  },
  {
    id: 'prompt-versions-wired',
    region: 'The prompt',
    flippedBy: '23-07',
    fragments: ['prompt-versions'],
  },
]

function runReachabilityChecks() {
  return REACHABILITY_CHECKS.map((check) => {
    const perFragment = check.fragments.map((fragment) => ({
      fragment,
      hits: findLiteral(OPS_ROOM_FILES, fragment),
    }))
    const pass = perFragment.every((f) => f.hits.length > 0)
    return {
      id: check.id,
      region: check.region,
      flippedBy: check.flippedBy,
      pass,
      evidence: pass
        ? []
        : perFragment
            .filter((f) => f.hits.length === 0)
            .map((f) => `no reference to "${f.fragment}" found under the operations room (page.tsx + its components/)`),
    }
  })
}

// ---------------------------------------------------------------------------
// Report + exit
// ---------------------------------------------------------------------------

function main() {
  const reportMode = process.argv.includes('--report')

  const allChecks = [
    ...runHonestyChecks(),
    runScheduledEvalClaimCheck(),
    runJudgementLedgerCheck(),
    runEmbedSnippetSourceCheck(),
    ...runReachabilityChecks(),
  ]

  if (reportMode) {
    for (const c of allChecks) {
      console.log(`${c.pass ? 'PASS' : 'OPEN'} ${c.id}  [${c.region}, flips in ${c.flippedBy}]`)
    }
    process.exit(0)
  }

  const failing = allChecks.filter((c) => !c.pass)

  if (failing.length === 0) {
    console.log(
      'check:ops-room-wiring: PASS -- every region calls its own Phase 21 endpoint and the console asserts nothing false about its own capabilities.'
    )
    process.exit(0)
  }

  console.log(`check:ops-room-wiring: FAIL -- ${failing.length} outstanding item(s):\n`)
  for (const c of failing) {
    console.log(`  ${c.id}  [${c.region}, flips in ${c.flippedBy}]`)
    for (const e of c.evidence) {
      if (typeof e === 'string') {
        console.log(`    ${e}`)
      } else {
        console.log(`    ${e.file}:${e.line}  ${e.snippet}`)
      }
    }
  }
  console.log(
    '\nExpected to be non-zero until the wiring plans in Phase 23 land: 23-05 (Live, Retrieval\n' +
      'health, Judgement), 23-06 (Adversary), 23-07 (The prompt), 23-08 (The bench, and the\n' +
      'point at which every check above should read PASS). A red result here is expected until\n' +
      'then, not a broken gate. Run with --report to see every check\'s status without failing.'
  )
  process.exit(1)
}

main()
