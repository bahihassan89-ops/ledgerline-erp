import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { money } from '../lib/format'

// ---------------- toasts ----------------
type Toast = { id: number; text: string; kind?: 'ok' | 'bad' | '' }
const ToastCtx = createContext<(text: string, kind?: Toast['kind']) => void>(() => {})
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const push = useCallback((text: string, kind: Toast['kind'] = '') => {
    const id = Date.now() + Math.random()
    setItems((x) => [...x, { id, text, kind }])
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === 'bad' ? 9000 : 4000)
  }, [])
  return <ToastCtx.Provider value={push}>{children}<div className="toasts">{items.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}</div></ToastCtx.Provider>
}
export const useToast = () => useContext(ToastCtx)

/** Run an async action with a toast on success/failure. */
export function useAction() {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const run = useCallback(async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    setBusy(true)
    try { const r = await fn(); if (ok) toast(ok, 'ok'); return r } catch (e: any) { toast(e.message || 'Failed', 'bad'); return undefined } finally { setBusy(false) }
  }, [toast])
  return { run, busy }
}

// ---------------- modal ----------------
export function Modal({ title, onClose, children, footer, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true">
        <div className="modal-head"><h2>{title}</h2><span style={{ flex: 1 }} /><button className="ghost" onClick={onClose} aria-label="Close">✕</button></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="field">{label}{children}{hint && <span className="hint">{hint}</span>}</label>
}

export function PageHead({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return <div className="page-head"><div><h1>{title}</h1>{sub && <div className="sub">{sub}</div>}</div><div className="actions">{actions}</div></div>
}

// ---------------- state badges ----------------
const TONE: Record<string, string> = {
  posted: 'ok', paid: 'ok', confirmed: 'info', approved: 'info', done: 'ok', open: 'ok', sent: 'ok',
  draft: '', to_approve: 'warn', pending: 'warn', partial: 'warn', not_paid: 'bad', closed: 'warn',
  locked: 'bad', cancelled: 'bad', rejected: 'bad', failed: 'bad',
}
const NAMES: Record<string, string> = { to_approve: 'Waiting approval', not_paid: 'Unpaid', draft: 'Draft' }
export function StateBadge({ state }: { state: string }) {
  return <span className={`badge ${TONE[state] ?? ''}`}>{NAMES[state] ?? state.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())}</span>
}

export function Money({ v, cur, d = 2 }: { v: any; cur?: string; d?: number }) {
  return <span className="num">{money(v, cur, d)}</span>
}

// ---------------- table ----------------
export type Col<T> = { key: string; title: string; num?: boolean; render?: (r: T) => ReactNode; width?: number | string }
export function DataTable<T extends Record<string, any>>({ rows, cols, onRow, empty = 'Nothing here yet.', foot }: { rows: T[] | null | undefined; cols: Col<T>[]; onRow?: (r: T) => void; empty?: string; foot?: ReactNode }) {
  if (!rows) return <div className="empty">Loading…</div>
  if (!rows.length) return <div className="empty">{empty}</div>
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr>{cols.map((c) => <th key={c.key} className={c.num ? 'num' : ''} style={{ width: c.width }}>{c.title}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={r.id ?? i} className={onRow ? 'clickable' : ''} onClick={() => onRow?.(r)}>
            {cols.map((c) => <td key={c.key} className={c.num ? 'num' : ''}>{c.render ? c.render(r) : r[c.key]}</td>)}
          </tr>))}
        </tbody>
        {foot && <tfoot>{foot}</tfoot>}
      </table>
    </div>
  )
}

export function Tabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (v: string) => void }) {
  return <div className="tabs">{tabs.map(([k, l]) => <button key={k} className={value === k ? 'on' : ''} onClick={() => onChange(k)}>{l}</button>)}</div>
}

export function Confirm({ text, onYes, children, className }: { text: string; onYes: () => void; children: ReactNode; className?: string }) {
  return <button className={className} onClick={() => { if (window.confirm(text)) onYes() }}>{children}</button>
}
