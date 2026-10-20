import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CurrencySelect } from '../components/pickers'
import { Field, Modal, PageHead, useAction } from '../components/ui'
import { patch, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { label, money } from '../lib/format'
import { invalidateOptions, useFetch } from '../lib/hooks'

const TYPES: Record<string, string[]> = {
  asset: ['cash', 'bank', 'receivable', 'notes_receivable', 'current_asset', 'inventory', 'wip', 'prepayment', 'tax_input', 'wht_receivable', 'fixed_asset', 'accumulated_depreciation', 'non_current_asset'],
  liability: ['payable', 'notes_payable', 'current_liability', 'tax_output', 'wht_payable', 'grni', 'non_current_liability'],
  equity: ['equity', 'retained_earnings'],
  income: ['revenue', 'other_income'],
  expense: ['cost_of_sales', 'expense', 'depreciation', 'other_expense'],
}
const CLASS_LABEL: Record<string, string> = { asset: 'Asset', liability: 'Liability', equity: 'Equity', income: 'Revenue', expense: 'Expense' }
const debitNormal = (c: string) => c === 'asset' || c === 'expense'

export default function Accounts() {
  const { can } = useAuth()
  const nav = useNavigate()
  const { data, reload } = useFetch<any[]>('/accounts/tree/')
  const [open, setOpen] = useState<Record<number, boolean>>({})
  const [q, setQ] = useState('')
  const [maxLevel, setMaxLevel] = useState(4)
  const [edit, setEdit] = useState<any>(null)

  const rows = useMemo(() => {
    const out: any[] = []
    const term = q.trim().toLowerCase()
    const match = (n: any): boolean => !term || n.code.startsWith(term) || n.name.toLowerCase().includes(term) || n.children.some(match)
    const walk = (nodes: any[]) => nodes.forEach((n) => {
      if (!match(n) || n.level > maxLevel) return
      out.push(n)
      if ((open[n.id] ?? n.level < 2) || term) walk(n.children)
    })
    walk(data || [])
    return out
  }, [data, open, q, maxLevel])

  const toggle = (n: any) => setOpen((o) => ({ ...o, [n.id]: !(o[n.id] ?? n.level < 2) }))
  const expandAll = (v: boolean) => {
    const all: Record<number, boolean> = {}
    const walk = (ns: any[]) => ns.forEach((n) => { all[n.id] = v; walk(n.children) }); walk(data || []); setOpen(all)
  }
  return (
    <>
      <PageHead title="Chart of Accounts" sub="Four-level tree: class → group → sub-group → postable ledger account. Balances are cumulative, in base currency."
        actions={can('ledger.account.manage') && <button className="primary" onClick={() => setEdit({})}>+ New account</button>} />
      <div className="card">
        <div className="toolbar">
          <input placeholder="Search code or name…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={maxLevel} onChange={(e) => setMaxLevel(Number(e.target.value))}>
            {[1, 2, 3, 4, 5, 6].map((l) => <option key={l} value={l}>Show up to level {l}</option>)}
          </select>
          <button onClick={() => expandAll(true)}>Expand all</button>
          <button onClick={() => expandAll(false)}>Collapse</button>
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Code & name</th><th>Type</th><th>Flags</th><th className="num">Balance</th><th /></tr></thead>
            <tbody>{rows.map((n) => {
              const bal = parseFloat(n.balance)
              const shown = debitNormal(n.account_class) ? bal : -bal
              return (
                <tr key={n.id} className={`tree-row ${n.level === 1 ? 'lvl1' : n.is_group ? 'group' : ''}`} style={{ opacity: n.active ? 1 : .5 }}>
                  <td style={{ paddingLeft: 12 + (n.level - 1) * 22 }}>
                    <span className="tree-toggle" onClick={() => toggle(n)}>{n.children.length ? ((open[n.id] ?? n.level < 2) || q ? '▾' : '▸') : ''}</span>
                    <span className="mono">{n.code}</span> &nbsp;{n.name}<span className="lvl-tag">L{n.level}</span>{n.name_ar && <span className="muted" dir="rtl" style={{ marginInlineStart: 10 }}>{n.name_ar}</span>}
                  </td>
                  <td>{n.is_group ? <span className="muted">Group · {CLASS_LABEL[n.account_class]}</span> : label(n.account_type)}</td>
                  <td>{n.is_control && <span className="badge info">Control</span>} {n.reconcilable && <span className="badge">Reconcilable</span>} {!n.active && <span className="badge bad">Inactive</span>}</td>
                  <td className="num">{money(shown)}{shown !== 0 && <span className="muted" style={{ fontSize: 11 }}> {(bal > 0) ? 'Dr' : 'Cr'}</span>}</td>
                  <td className="right" style={{ whiteSpace: 'nowrap' }}>
                    {!n.is_group && <button className="sm ghost" onClick={() => nav(`/reports?r=general-ledger&account=${n.id}`)}>Ledger</button>}
                    {can('ledger.account.manage') && <>
                      {n.is_group && <button className="sm ghost" onClick={() => setEdit({ parent: n.id, account_class: n.account_class, code: n.code, _parent: n })}>+ Sub</button>}
                      <button className="sm ghost" onClick={() => setEdit(n)}>Edit</button></>}
                  </td>
                </tr>)
            })}</tbody>
          </table>
        </div>
      </div>
      {edit && <AccountForm initial={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); invalidateOptions(); reload() }} />}
    </>
  )
}

function AccountForm({ initial, onClose, onSaved }: any) {
  const isNew = !initial.id
  const [f, setF] = useState<any>({ code: '', name: '', name_ar: '', account_type: 'expense', account_class: 'expense', active: true, is_control: false, requires_cost_center: false, currency: null, description: '', cash_flow_category: '', ...initial })
  const { data: groups } = useFetch<any[]>('/accounts/', { account_type: 'group' })
  const { run, busy } = useAction()
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }))
  const parent = groups?.find((g) => g.id === f.parent)
  const cls = parent?.account_class || f.account_class
  const save = async () => {
    const body = { code: f.code, name: f.name, name_ar: f.name_ar, parent: f.parent || null, account_class: cls, account_type: f.account_type, is_control: f.is_control, requires_cost_center: f.requires_cost_center, currency: f.currency || null, description: f.description, active: f.active, cash_flow_category: f.cash_flow_category }
    const r = await run(() => (isNew ? post('/accounts/', body) : patch(`/accounts/${f.id}/`, body)), 'Account saved')
    if (r) onSaved()
  }
  return (
    <Modal title={isNew ? 'New account' : `Edit ${initial.code}`} onClose={onClose} footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={busy} onClick={save}>Save</button></>}>
      <div className="grid form">
        <Field label="Parent (group)">
          <select value={f.parent ?? ''} onChange={(e) => set('parent', Number(e.target.value) || null)}>
            <option value="">— top level (class) —</option>
            {groups?.map((g) => <option key={g.id} value={g.id}>{' '.repeat((g.level - 1) * 3)}{g.code} {g.name}</option>)}
          </select>
        </Field>
        <Field label="Code" hint={parent ? `Must start with ${parent.code}` : 'Top-level classes use 1 digit'}><input className="mono" value={f.code} onChange={(e) => set('code', e.target.value.trim())} /></Field>
      </div>
      <Field label="Name"><input value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
      <Field label="الاسم بالعربية"><input dir="rtl" value={f.name_ar || ''} onChange={(e) => set('name_ar', e.target.value)} /></Field>
      <div className="grid form">
        <Field label="Cash-flow line" hint="Empty = from account type"><select value={f.cash_flow_category || ''} onChange={(e) => set('cash_flow_category', e.target.value)}><option value="">Automatic</option><option value="operating">Operating</option><option value="investing">Investing</option><option value="financing">Financing</option><option value="cash">Cash & equivalents</option><option value="none">Exclude</option></select></Field>
        {!parent && <Field label="Class"><select value={f.account_class} onChange={(e) => set('account_class', e.target.value)}>{Object.entries(CLASS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>}
        <Field label="Type" hint="Group = non-postable parent">
          <select value={f.account_type} onChange={(e) => set('account_type', e.target.value)}>
            <option value="group">Group (non-postable)</option>
            {TYPES[cls]?.map((t) => <option key={t} value={t}>{label(t)}</option>)}
          </select>
        </Field>
        <Field label="Restrict to currency"><CurrencySelect allowEmpty value={f.currency} onChange={(v) => set("currency", v)} /></Field>
      </div>
      <label className="check"><input type="checkbox" checked={f.is_control} onChange={(e) => set('is_control', e.target.checked)} />Control account — only sub-ledgers (invoices, payments, stock) may post</label>
      <label className="check"><input type="checkbox" checked={f.requires_cost_center} onChange={(e) => set('requires_cost_center', e.target.checked)} />Require a cost center on every line</label>
      <label className="check"><input type="checkbox" checked={f.active} onChange={(e) => set('active', e.target.checked)} />Active</label>
      <Field label="Description / usage notes"><textarea rows={2} value={f.description} onChange={(e) => set('description', e.target.value)} /></Field>
    </Modal>
  )
}
