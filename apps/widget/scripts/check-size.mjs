import { readFileSync } from 'fs'
import { gzipSync } from 'zlib'
const raw = readFileSync('dist/widget.iife.js')
const size = gzipSync(raw).length
if (size > 20480) { console.error(`BUNDLE SIZE EXCEEDED: ${size} bytes (limit 20480)`); process.exit(1) }
console.log(`Bundle size OK: ${size} bytes`)
