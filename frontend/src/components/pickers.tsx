import { useOptions } from '../lib/hooks'

type P = { value: any; onChange: (v: any) => void; disabled?: boolean; allowEmpty?: boolean; placeholder?: string }

export function AccountSelect({ value, onChange, disabled, filter, allowEmpty, placeholder = 'Select account…' }: P & { filter?: (a: any) => boolean }) {
  const accounts = useOptions<any>('/accounts/', { active: true })
  const shown = accounts.filter((a) => !a.is_group && (!filter || filter(a)))
  return (
    <select value={value ?? ''} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} disabled={disabled}>
      <option value="">{allowEmpty ? '— none —' : placeholder}</option>
      {shown.map((a) => <option key={a.id} value={a.id}>{a.code} · {a.name}{a.is_control ? ' (control)' : ''}</option>)}
    </select>
  )
}

export function PartnerSelect({ value, onChange, disabled, kind, allowEmpty }: P & { kind?: 'customer' | 'vendor' }) {
  const q = kind === 'customer' ? { is_customer: true } : kind === 'vendor' ? { is_vendor: true } : {}
  const items = useOptions<any>('/partners/', { active: true, ...q })
  return (
    <select value={value ?? ''} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} disabled={disabled}>
      <option value="">{allowEmpty ? '— none —' : `Select ${kind ?? 'partner'}…`}</option>
      {items.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}{p.on_hold ? ' (ON HOLD)' : ''}</option>)}
    </select>
  )
}

export function SimpleSelect({ path, value, onChange, disabled, allowEmpty, labelOf, query }: P & { path: string; labelOf: (x: any) => string; query?: any }) {
  const items = useOptions<any>(path, query)
  return (
    <select value={value ?? ''} onChange={(e) => onChange(e.target.value === '' ? null : (isNaN(Number(e.target.value)) ? e.target.value : Number(e.target.value)))} disabled={disabled}>
      {allowEmpty !== false && <option value="">— none —</option>}
      {items.map((x) => <option key={x.id ?? x.code} value={x.id ?? x.code}>{labelOf(x)}</option>)}
    </select>
  )
}

export const TaxSelect = (p: P & { scope: 'sale' | 'purchase' }) =>
  <SimpleSelect {...p} path="/taxes/" query={{ scope: p.scope, active: true }} labelOf={(t) => `${t.name}`} />
export const CurrencySelect = (p: P) =>
  <SimpleSelect {...p} allowEmpty={p.allowEmpty ?? false} path="/currencies/" labelOf={(c) => `${c.code} — ${c.name}`} />
export const ProductSelect = (p: P) =>
  <SimpleSelect {...p} path="/products/" query={{ active: true }} labelOf={(x) => `${x.sku} · ${x.name}`} />
export const WarehouseSelect = (p: P) =>
  <SimpleSelect {...p} allowEmpty={false} path="/warehouses/" labelOf={(x) => `${x.code} · ${x.name}`} />
export const JournalSelect = (p: P & { types?: string[] }) => {
  const items = useOptions<any>('/journals/', { active: true })
  return (
    <select value={p.value ?? ''} onChange={(e) => p.onChange(Number(e.target.value) || null)} disabled={p.disabled}>
      <option value="">Select journal…</option>
      {items.filter((j) => !p.types || p.types.includes(j.type)).map((j) => <option key={j.id} value={j.id}>{j.code} — {j.name}</option>)}
    </select>
  )
}
export const CostCenterSelect = (p: P) =>
  <SimpleSelect {...p} path="/cost-centers/" labelOf={(x) => `${x.code} ${x.name}`} />
