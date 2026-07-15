#!/usr/bin/env node
// check-no-dusk-tokens.mjs
//
// SC1 / UI2-07 gate (see 20-VALIDATION.md Success-Criterion -> Validation Map
// and 20-UI-SPEC.md Section 10, anti-pattern 2): fails the build if any
// retired "Hillbrow at Dusk" design-system token, class, brand string, or
// asset filename remains anywhere under apps/admin/app or apps/admin/public.
//
// Wave 0 (this plan, 20-01) EXPECTS this script to exit non-zero: the dusk
// pages have not been rebuilt yet. It flips green once 20-03 (token cutover)
// and 20-14 (final dusk-page deletion) land.
//
// Scope note: this is a fast, best-effort textual gate, not a CSS/TS parser.
// Line-comment stripping for `//` is best-effort (it does not understand
// string literals, so a `//` inside a URL string will truncate the rest of
// that line before scanning) -- acceptable for an internal grep gate whose
// false-negative risk is caught by the human-verify parity checkpoints in
// later waves, not solely by this script.

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { extname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
const ADMIN_ROOT = join(__dirname, '..')
const SCAN_ROOTS = ['app', 'public'].map((d) => join(ADMIN_ROOT, d))
const EXCLUDE_DIRS = new Set(['node_modules', '.next', '.git'])

// Out-of-scope for the dusk cutover: the published customer-facing chat
// widget bundle (a separate Preact package, built + copied here in 12-02 for
// CDN delivery). It has its own standalone brand palette (coral/burgundy)
// that is NOT the retired "Hillbrow at Dusk" admin theme -- 20-UI-SPEC.md §4
// explicitly calls the real widget "a separate package, out of this phase's
// scope." Its var names (--accent, --gold, --amber, --text-1..4, ...)
// coincidentally collide with the forbidden-marker patterns below, which are
// tuned for the admin console's retired tokens. Excluding this path avoids
// false positives without weakening the gate for actual admin app code.
const EXCLUDE_PATHS = [join(ADMIN_ROOT, 'public', 'wchats')]

// Extensions whose *content* we grep. Everything else (png, ico, woff, ...)
// is only checked by filename below -- reading binary asset bytes as text
// would produce garbage matches.
const TEXT_EXTENSIONS = new Set([
  '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs',
  '.css', '.scss',
  '.html', '.mdx', '.md',
  '.json', '.svg', '.txt',
])

// Filenames that must not exist anywhere under the scan roots, regardless
// of extension (retired dusk-theme binary assets).
const FORBIDDEN_FILENAMES = ['skyline-w-chats.png']

// Forbidden-marker list, built from 20-UI-SPEC.md Section 10 anti-pattern 2
// ("No dusk/skyline/amber-console residue") -- the retired glass/skyline/
// coral/lilac/cyan token families, the retired display serif family
// (Fraunces), the retired console brand-class (amber-console), and the
// retired metallic-accent family (brass/gold/amber).
//
// `pattern` is matched against comment-stripped file content.
const FORBIDDEN_MARKERS = [
  // -- the glass token + class family (dusk's blurred/translucent panels) --
  { label: 'CSS var --glass-bg', pattern: /--glass-bg/g },
  { label: 'CSS var --glass-blur', pattern: /--glass-blur/g },
  { label: 'CSS var --glass-highlight', pattern: /--glass-highlight/g },
  { label: 'class .glass', pattern: /\bglass\b(?!-)/g },
  { label: 'class .glass-strong', pattern: /\bglass-strong\b/g },
  { label: 'class .glass-nav', pattern: /\bglass-nav\b/g },
  { label: 'class .on-photo', pattern: /\bon-photo\b/g },

  // -- retired accent-hue token families (sunset-coral / jacaranda lilac / --
  // -- tower cyan / building amber-gold), see RESEARCH.md Summary --
  { label: 'CSS var --accent', pattern: /--accent\b/g },
  { label: 'CSS var --lilac', pattern: /--lilac/g },
  { label: 'CSS var --cyan', pattern: /--cyan/g },
  { label: 'CSS var --amber', pattern: /--amber/g },
  { label: 'CSS var --gold', pattern: /--gold/g },
  { label: 'retired metallic-accent family (--brass-)', pattern: /--brass[- ]?/gi },

  // -- retired console brand-class --
  { label: 'retired brand string amber-console', pattern: /amber-console/gi },

  // -- other dusk-era structural/typographic tokens --
  { label: 'CSS var --bg-deep', pattern: /--bg-deep/g },
  { label: 'CSS var --chip', pattern: /--chip\b/g },
  { label: 'CSS var --border', pattern: /--border\b/g },
  { label: 'CSS var --text-1..4', pattern: /--text-[1-4]\b/g },
  { label: 'CSS var --radius-*', pattern: /--radius-[a-z]+/g },
  { label: 'CSS var --shadow-*', pattern: /--shadow-[a-z0-9]+/gi },
  { label: 'CSS var --font-display (dusk value, retired)', pattern: /--font-display\b/g },

  // -- retired display serif family --
  { label: 'retired display serif Fraunces', pattern: /Fraunces/g },

  // -- retired skyline photo background asset --
  { label: 'skyline asset reference', pattern: /skyline/gi },
]

/** Strip `/* ... *\/` block comments (preserving newlines for line numbers)
 * and, for JS/TS-family files, `//` line comments, so header/doc prose
 * cannot self-invalidate the scan. */
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
    return files // dir may not exist (e.g. public/ in some checkouts)
  }
  for (const entry of entries) {
    if (EXCLUDE_DIRS.has(entry)) continue
    const full = join(dir, entry)
    if (EXCLUDE_PATHS.includes(full)) continue
    const st = statSync(full)
    if (st.isDirectory()) {
      walk(full, files)
    } else {
      files.push(full)
    }
  }
  return files
}

function main() {
  const findings = [] // { file, line, label, snippet } | { file, label: 'forbidden filename' }

  for (const root of SCAN_ROOTS) {
    for (const file of walk(root)) {
      const base = file.split(/[\\/]/).pop()

      if (FORBIDDEN_FILENAMES.includes(base)) {
        findings.push({ file: relative(ADMIN_ROOT, file), label: `forbidden filename: ${base}` })
      }

      const ext = extname(file)
      if (!TEXT_EXTENSIONS.has(ext)) continue

      let raw
      try {
        raw = readFileSync(file, 'utf8')
      } catch {
        continue
      }
      const stripped = stripComments(raw, ext)
      const lines = stripped.split('\n')

      for (const { label, pattern } of FORBIDDEN_MARKERS) {
        lines.forEach((line, i) => {
          pattern.lastIndex = 0
          if (pattern.test(line)) {
            findings.push({
              file: relative(ADMIN_ROOT, file),
              line: i + 1,
              label,
              snippet: line.trim().slice(0, 120),
            })
          }
        })
      }
    }
  }

  if (findings.length === 0) {
    console.log('check:no-dusk-tokens: PASS -- no retired dusk/skyline/amber-console markers found.')
    process.exit(0)
  }

  console.log(`check:no-dusk-tokens: FAIL -- ${findings.length} retired marker(s) found:\n`)
  for (const f of findings) {
    if (f.line) {
      console.log(`  ${f.file}:${f.line}  [${f.label}]  ${f.snippet}`)
    } else {
      console.log(`  ${f.file}  [${f.label}]`)
    }
  }
  console.log(
    '\nExpected to be non-zero pre-cutover (Wave 0-1). Flips green once 20-03 (token cutover) and 20-14 (dusk-page deletion) land.'
  )
  process.exit(1)
}

main()
