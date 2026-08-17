#!/usr/bin/env node
// check-theming-contract.mjs
//
// The widget themes itself by writing CSS custom properties onto the iframe's
// root element from the `theming` dict GET /widget/{agent_id}/config returns.
// The two halves of that contract had never been compared. Widget.jsx wrote
// --primary-color, --accent-gold, --font-family, --border-radius and
// --background; widget.css referenced none of the five, so every per-tenant
// colour, font and radius was inert (BACKLOG 7.2b).
//
// This gate compares them for real, on every build:
//
//   1. every CSS variable in src/theming.js THEMING_MAP is referenced by at
//      least one rule in src/widget.css other than :root. A variable that only
//      appears as a :root declaration is a default, not a consumer -- that is
//      exactly the shape the old code passed by accident.
//   2. src/theming.js is the only module that writes a custom property, so a
//      new ad-hoc setProperty call cannot route around rule 1.
//
// Exit 0 on pass, 1 on any finding.

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
const WIDGET_ROOT = join(__dirname, '..')
const SRC = join(WIDGET_ROOT, 'src')
const THEMING_MODULE = join(SRC, 'theming.js')
const STYLESHEET = join(SRC, 'widget.css')

/** Strip block comments, keeping newlines so reported line numbers stay true. */
function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
}

/**
 * Flatten a stylesheet into { selector, body } pairs. At-rules (@media,
 * @keyframes) are recursed into so a variable used only inside one still
 * counts as consumed.
 */
function rules(css, out = []) {
  let i = 0
  while (i < css.length) {
    const open = css.indexOf('{', i)
    if (open === -1) break
    const selector = css.slice(i, open).trim()
    let depth = 1
    let j = open + 1
    while (j < css.length && depth > 0) {
      if (css[j] === '{') depth += 1
      else if (css[j] === '}') depth -= 1
      j += 1
    }
    const body = css.slice(open + 1, j - 1)
    if (selector.startsWith('@')) rules(body, out)
    else out.push({ selector, body })
    i = j
  }
  return out
}

/** varName -> [selectors that read it], excluding :root's own declarations. */
function consumers(css) {
  const map = new Map()
  for (const { selector, body } of rules(stripComments(css))) {
    if (selector === ':root') continue
    for (const match of body.matchAll(/var\(\s*(--[\w-]+)/g)) {
      const list = map.get(match[1]) || []
      list.push(selector)
      map.set(match[1], list)
    }
  }
  return map
}

function walk(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walk(full, files)
    else files.push(full)
  }
  return files
}

/** Modules writing custom properties outside theming.js. Tests fake the root
 *  element, so they define setProperty rather than calling it and are skipped. */
function strayInjectors() {
  return walk(SRC)
    .filter((f) => /\.(js|jsx)$/.test(f) && !/\.test\.jsx?$/.test(f) && f !== THEMING_MODULE)
    .filter((f) => /\.setProperty\s*\(/.test(readFileSync(f, 'utf8')))
    .map((f) => relative(WIDGET_ROOT, f))
}

const { THEMING_MAP, NON_CSS_KEYS } = await import(pathToFileURL(THEMING_MODULE).href)
const read = consumers(readFileSync(STYLESHEET, 'utf8'))
const findings = []

for (const [key, name] of Object.entries(THEMING_MAP)) {
  const selectors = read.get(name)
  if (selectors) console.log(`  ok    ${key.padEnd(21)} -> ${name.padEnd(20)} ${selectors[0]}`)
  else findings.push(`theming key '${key}' injects ${name}, which no rule in widget.css reads`)
}

for (const key of NON_CSS_KEYS) {
  if (THEMING_MAP[key]) findings.push(`'${key}' is not a CSS value, so it cannot map to ${THEMING_MAP[key]}`)
}

for (const file of strayInjectors()) {
  findings.push(`${file} writes a custom property directly; route it through src/theming.js`)
}

if (findings.length > 0) {
  console.error(`\ncheck:theming-contract: FAIL -- ${findings.length} finding(s):`)
  for (const f of findings) console.error(`  ${f}`)
  process.exit(1)
}

console.log(
  `check:theming-contract: PASS -- ${Object.keys(THEMING_MAP).length} theming keys, every variable read by a rule.`
)
