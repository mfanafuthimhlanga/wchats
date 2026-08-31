#!/usr/bin/env node
// sync-embed.mjs [--check]
//
// embed/ is the folder embed/README.md tells a customer to publish, but nothing
// ever copied a build into it. embed/widget.iife.js was 20,834 bytes dated
// 1 June while dist/widget.iife.js was 23,552 bytes dated 4 August, so what
// would have shipped was months behind source (BACKLOG 7.5).
//
// There are TWO shipping locations, not one. apps/admin/public/wchats/ held the
// same 1-June bundle, and it is not a leftover: the AWS-era deploy ran
// `aws s3 sync apps/admin/public/wchats/` into the CloudFront origin bucket
// and named it the upload source (that tree is deleted with ADR 0005; #135
// carries the Railway-era serving story), and Next.js serves public/ at
// the admin origin so /wchats/widget.js is live there too. Syncing one and not
// the other is how the stale bundle survived a gate written about it (D1).
//
//   default    run from postbuild: copy the built artefacts into both targets,
//              then re-read every side and prove they match by SHA-256.
//   --check    verify only, write nothing. The drift gate: it fails if either
//              target was hand-edited, or if dist/ was rebuilt without syncing.
//
// widget.js and index.html are hand-maintained loader files, not build output.
// embed/ is their source of truth and they are copied from there into
// apps/admin/public/wchats/, so a loader fix cannot land in one location only.

import { createHash } from 'node:crypto'
import { copyFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
const WIDGET_ROOT = join(__dirname, '..')
const REPO_ROOT = join(WIDGET_ROOT, '..', '..')
const DIST = join(WIDGET_ROOT, 'dist')
const EMBED = join(WIDGET_ROOT, 'embed')
const ADMIN_PUBLIC = join(REPO_ROOT, 'apps', 'admin', 'public', 'wchats')
const API_STATIC = join(REPO_ROOT, 'apps', 'api', 'static', 'wchats')

const BUILT = ['widget.iife.js', 'widget.css']
const LOADERS = ['widget.js', 'index.html']

// [source dir, destination dir, file names]. dist/ is the source of the built
// artefacts for both shipping locations; embed/ is the source of the loaders
// for the admin one.
const SYNCS = [
  [DIST, EMBED, BUILT],
  [DIST, ADMIN_PUBLIC, BUILT],
  [EMBED, ADMIN_PUBLIC, LOADERS],
  // #135: the api service serves the bundle at /wchats on Railway (the
  // CloudFront origin went with ADR 0005), and its Docker build context is
  // apps/api, so the files must live inside it. Beside app/, not in it: the
  // complexity gate runs lizard over app/ and a minified bundle is not code
  // it should measure. Same drift gate as the rest.
  [DIST, API_STATIC, BUILT],
  [EMBED, API_STATIC, LOADERS],
]

const checkOnly = process.argv.includes('--check')
const sha256 = (path) => createHash('sha256').update(readFileSync(path)).digest('hex')
const label = (path) => relative(REPO_ROOT, path).replace(/\\/g, '/')

const findings = []

for (const [sourceDir, destDir, names] of SYNCS) {
  for (const name of names) {
    const source = join(sourceDir, name)
    const dest = join(destDir, name)

    if (!existsSync(source)) {
      const hint = sourceDir === DIST ? ' -- run `npm run build` before this check' : ''
      findings.push(`${label(source)} is missing${hint}`)
      continue
    }
    if (!checkOnly) {
      mkdirSync(destDir, { recursive: true })
      copyFileSync(source, dest)
    }
    if (!existsSync(dest)) {
      findings.push(`${label(dest)} is missing`)
      continue
    }

    const sourceHash = sha256(source)
    const destHash = sha256(dest)
    if (sourceHash === destHash) {
      console.log(
        `  ${checkOnly ? 'in sync' : 'synced '} ${label(dest).padEnd(40)} ` +
        `${readFileSync(dest).length} bytes  sha256:${destHash.slice(0, 12)}`
      )
    } else {
      const fix = sourceDir === DIST ? 'npm run build' : 'node scripts/sync-embed.mjs'
      findings.push(
        `${label(dest)} does not match ${label(source)} ` +
        `(${destHash.slice(0, 12)} vs ${sourceHash.slice(0, 12)}) -- run \`${fix}\``
      )
    }
  }
}

if (findings.length > 0) {
  console.error(`\ncheck:embed-sync: FAIL -- ${findings.length} finding(s):`)
  for (const f of findings) console.error(`  ${f}`)
  process.exit(1)
}

const fileCount = SYNCS.reduce((n, [, , names]) => n + names.length, 0)
console.log(
  `check:embed-sync: PASS -- embed/, apps/admin/public/wchats/ and ` +
  `apps/api/static/wchats/ all match their sources (${fileCount} files).`
)
