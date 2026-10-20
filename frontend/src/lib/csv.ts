/** Minimal RFC-4180 CSV parser → array of objects keyed by lower-cased header. */
export function parseCSV(text: string): Record<string, string>[] {
  const rows: string[][] = []
  let row: string[] = [], cell = '', q = false
  const t = text.replace(/^﻿/, '')
  for (let i = 0; i < t.length; i++) {
    const ch = t[i]
    if (q) {
      if (ch === '"' && t[i + 1] === '"') { cell += '"'; i++ } else if (ch === '"') q = false; else cell += ch
    } else if (ch === '"') q = true
    else if (ch === ',') { row.push(cell); cell = '' }
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && t[i + 1] === '\n') i++; row.push(cell); rows.push(row); row = []; cell = '' }
    else cell += ch
  }
  if (cell || row.length) { row.push(cell); rows.push(row) }
  const [head, ...body] = rows.filter((r) => r.some((c) => c.trim()))
  if (!head) return []
  const keys = head.map((h) => h.trim().toLowerCase().replace(/\s+/g, '_'))
  return body.map((r) => Object.fromEntries(keys.map((k, i) => [k, (r[i] ?? '').trim()])))
}
