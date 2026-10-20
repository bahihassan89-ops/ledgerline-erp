export function money(v: string | number | null | undefined, currency?: string, decimals = 2): string {
  const n = typeof v === 'number' ? v : parseFloat(v ?? '0')
  if (Number.isNaN(n)) return '—'
  const s = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
  const out = n < 0 ? `(${s})` : s
  return currency ? `${out} ${currency}` : out
}
export const num = (v: any) => parseFloat(v ?? '0') || 0
export const qty = (v: any) => num(v).toLocaleString('en-US', { maximumFractionDigits: 4 })
export const today = () => new Date().toISOString().slice(0, 10)
export const yearStart = () => `${new Date().getFullYear()}-01-01`
export const fmtDate = (d?: string | null) => (d ? new Date(d + (d.length === 10 ? 'T00:00:00' : '')).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—')
export const label = (s?: string) => (s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export function toCSV(rows: Record<string, any>[], cols: { key: string; title: string }[]): string {
  const esc = (v: any) => {
    let s = v == null ? '' : String(v)
    if (/^[=+\-@\t\r]/.test(s) && Number.isNaN(Number(s))) s = "'" + s // CSV formula-injection guard
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [cols.map((c) => esc(c.title)).join(','), ...rows.map((r) => cols.map((c) => esc(r[c.key])).join(','))].join('\n')
}
export function download(name: string, content: string) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([content], { type: 'text/csv' }))
  a.download = name
  a.click()
  URL.revokeObjectURL(a.href)
}
