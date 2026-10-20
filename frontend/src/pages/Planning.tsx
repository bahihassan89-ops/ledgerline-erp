import { useEffect, useMemo, useState } from 'react'
import { AccountSelect, JournalSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, Tabs, useAction } from '../components/ui'
import { get, patch, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, money, num, today } from '../lib/format'
import { useFetch, useOptions } from '../lib/hooks'

export function Budgets() {
  const { can } = useAuth()
  const { data: budgets, reload } = useFetch<any[]>('/budgets/')
  const { data: years } = useFetch<any[]>('/fiscal-years/')
  const [sel, setSel] = useState<number | null>(null)
  const [tab, setTab] = useState('report')
  const b = budgets?.find((x) => x.id === (sel ?? budgets?.[0]?.id))
  const { run } = useAction()
  const create = async () => {
    const name = window.prompt('Budget name:'); if (!name || !years?.length) return
    const r = await run(() => post('/budgets/', { name, fiscal_year: years[0].id }), 'Budget created'); if (r) { reload(); setSel(r.id); setTab('edit') }
  }
  return (
    <>
      <PageHead title="Budgets — الموازنة التقديرية" sub="Monthly budget per account; compare with actual results at any time." actions={can('planning.budget.manage') && <button className="primary" onClick={create}>+ New budget</button>} />
      {!b ? <div className="empty">No budget yet.</div> : <>
        <div className="row" style={{ marginBottom: 12 }}><select style={{ width: 'auto' }} value={b.id} onChange={(e) => setSel(Number(e.target.value))}>{budgets!.map((x) => <option key={x.id} value={x.id}>{x.name} · {x.fiscal_year_name}</option>)}</select>
          <span className={`badge ${b.state === 'approved' ? 'ok' : ''}`}>{b.state}</span>
          {b.state === 'draft' && can('planning.budget.manage') && <button className="sm" onClick={async () => { if (window.confirm('Approve and freeze this budget?') && await run(() => post(`/budgets/${b.id}/approve/`), 'Approved')) reload() }}>Approve</button>}</div>
        <Tabs tabs={[['report', 'Budget vs actual'], ['edit', 'Budget lines']]} value={tab} onChange={setTab} />
        {tab === 'report' ? <BudgetReport budget={b} /> : <BudgetGrid budget={b} years={years || []} readOnly={b.state !== 'draft' || !can('planning.budget.manage')} />}
      </>}
    </>
  )
}

function BudgetReport({ budget }: any) {
  const fy = useFetch<any[]>('/fiscal-years/').data?.find((y) => y.id === budget.fiscal_year)
  const [from, setFrom] = useState<string>('')
  const [to, setTo] = useState<string>('')
  const { data } = useFetch<any>(`/budgets/${budget.id}/report/`, { date_from: from || undefined, date_to: to || undefined })
  return (
    <div className="card">
      <div className="toolbar">
        <select value={from} onChange={(e) => setFrom(e.target.value)}><option value="">From start of year</option>{fy?.periods.map((p: any) => <option key={p.id} value={p.start_date}>From {p.name}</option>)}</select>
        <select value={to} onChange={(e) => setTo(e.target.value)}><option value="">To end of year</option>{fy?.periods.map((p: any) => <option key={p.id} value={p.end_date}>To {p.name}</option>)}</select>
        {data && <><span style={{ flex: 1 }} /><span>Income: budget {money(data.income.budget)} / actual {money(data.income.actual)}</span><span>Expense: budget {money(data.expense.budget)} / actual {money(data.expense.actual)}</span></>}
      </div>
      <DataTable rows={data?.rows} cols={[{ key: 'code', title: 'Account', render: (r) => <><span className="mono">{r.code}</span> {r.name}</> },
        { key: 'budget', title: 'Budget', num: true, render: (r) => money(r.budget) }, { key: 'actual', title: 'Actual', num: true, render: (r) => money(r.actual) },
        { key: 'variance', title: 'Variance', num: true, render: (r) => <span style={{ color: r.favourable ? 'var(--ok)' : 'var(--bad)' }}>{money(r.variance)}</span> },
        { key: 'variance_pct', title: '%', num: true, render: (r) => r.variance_pct ? `${r.variance_pct}%` : '—' }]} />
    </div>
  )
}

function BudgetGrid({ budget, years, readOnly }: any) {
  const fy = years.find((y: any) => y.id === budget.fiscal_year)
  const periods = fy?.periods || []
  const accounts = useOptions<any>('/accounts/', { active: true })
  const [grid, setGrid] = useState<Record<number, Record<number, string>>>({})
  const [add, setAdd] = useState<number | null>(null)
  const { run, busy } = useAction()
  useEffect(() => {
    get<any[]>(`/budgets/${budget.id}/lines/`).then((lines) => {
      const g: Record<number, Record<number, string>> = {}
      lines.forEach((l) => { (g[l.account] ||= {})[l.period] = String(num(l.amount)) })
      setGrid(g)
    })
  }, [budget.id])
  const accById = useMemo(() => Object.fromEntries(accounts.map((a) => [a.id, a])), [accounts])
  const save = () => run(() => put(`/budgets/${budget.id}/lines/`, { lines: Object.entries(grid).flatMap(([a, ps]) => Object.entries(ps).map(([p, v]) => ({ account: Number(a), period: Number(p), amount: v || '0' }))) }), 'Budget saved')
  const fill = (a: number) => { const v = window.prompt('Amount per month:'); if (v) setGrid({ ...grid, [a]: Object.fromEntries(periods.map((p: any) => [p.id, v])) }) }
  return (
    <div className="card">
      {!readOnly && <div className="toolbar"><div style={{ width: 340 }}><AccountSelect value={add} onChange={setAdd} filter={(a) => ['income', 'expense'].includes(a.account_class) && !grid[a.id]} placeholder="Add account…" /></div>
        <button className="sm" disabled={!add} onClick={() => { setGrid({ ...grid, [add!]: {} }); setAdd(null) }}>Add row</button><span style={{ flex: 1 }} /><button className="primary" disabled={busy} onClick={save}>Save budget</button></div>}
      <div className="table-wrap"><table className="data lines">
        <thead><tr><th>Account</th>{periods.map((p: any) => <th key={p.id} className="num">{p.name.split(' ')[0]}</th>)}<th className="num">Year</th><th /></tr></thead>
        <tbody>{Object.keys(grid).map(Number).map((a) => (
          <tr key={a}><td style={{ whiteSpace: 'nowrap' }}>{accById[a] ? `${accById[a].code} ${accById[a].name}` : a}</td>
            {periods.map((p: any) => <td key={p.id}><input className="num" style={{ width: 90 }} disabled={readOnly} value={grid[a]?.[p.id] ?? ''} onChange={(e) => setGrid({ ...grid, [a]: { ...grid[a], [p.id]: e.target.value } })} /></td>)}
            <td className="num"><b>{money(Object.values(grid[a] || {}).reduce((s, v) => s + num(v), 0))}</b></td>
            <td>{!readOnly && <button className="sm ghost" onClick={() => fill(a)}>Fill</button>}</td></tr>))}</tbody>
      </table></div>
    </div>
  )
}

export function Recurring() {
  const { can } = useAuth()
  const { data, reload } = useFetch<any[]>('/recurring-entries/')
  const [edit, setEdit] = useState<any>(null)
  const { run } = useAction()
  return (
    <>
      <PageHead title="Recurring Entries — القيود المتكررة" sub="Rent, amortisation of prepayments, standing accruals. Generated automatically every day by the scheduler, or on demand."
        actions={can('planning.recurring.manage') && <><button onClick={async () => { const r = await run(() => post('/recurring-entries/run-due/', {})); if (r) { alert(r.created.length ? `Created: ${r.created.join(', ')}` : 'Nothing due.'); reload() } }}>Generate due now</button>
          <button className="primary" onClick={() => setEdit({ name: '', journal: null, narration: '', frequency: 'monthly', next_date: today(), end_date: null, auto_post: false, auto_reverse_days: null, active: true, lines: [{ account: null, debit: '', credit: '' }, { account: null, debit: '', credit: '' }] })}>+ New template</button></>} />
      <div className="card"><DataTable rows={data} onRow={setEdit} cols={[{ key: 'name', title: 'Name' }, { key: 'journal_code', title: 'Journal' }, { key: 'frequency', title: 'Frequency' },
        { key: 'next_date', title: 'Next', render: (r) => fmtDate(r.next_date) }, { key: 'end_date', title: 'Until', render: (r) => fmtDate(r.end_date) },
        { key: 'amount', title: 'Amount', num: true, render: (r) => money(r.lines.reduce((s: number, l: any) => s + num(l.debit), 0)) },
        { key: 'auto_post', title: 'Mode', render: (r) => <>{r.auto_post ? 'Auto-post' : 'Draft'}{r.auto_reverse_days ? ` · reverses after ${r.auto_reverse_days}d` : ''}</> },
        { key: 'active', title: '', render: (r) => r.active ? <span className="badge ok">active</span> : <span className="badge">ended</span> }]} /></div>
      {edit && <RecurringForm initial={edit} onClose={() => { setEdit(null); reload() }} />}
    </>
  )
}

function RecurringForm({ initial, onClose }: any) {
  const [f, setF] = useState<any>(initial)
  const { run, busy } = useAction()
  const setL = (i: number, p: any) => setF({ ...f, lines: f.lines.map((l: any, j: number) => (j === i ? { ...l, ...p } : l)) })
  const d = f.lines.reduce((s: number, l: any) => s + num(l.debit), 0), c = f.lines.reduce((s: number, l: any) => s + num(l.credit), 0)
  const save = async () => { const { id, journal_code, last_entry_number, last_entry, ...b } = f; void journal_code; void last_entry_number; void last_entry; if (await run(() => (id ? patch(`/recurring-entries/${id}/`, b) : post('/recurring-entries/', b)), 'Saved')) onClose() }
  return (
    <Modal wide title={f.id ? f.name : 'New recurring entry'} onClose={onClose} footer={<button className="primary" disabled={busy || d !== c || !d} onClick={save}>Save</button>}>
      <div className="grid form"><Field label="Name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Journal"><JournalSelect types={['general', 'bank', 'cash']} value={f.journal} onChange={(v) => setF({ ...f, journal: v })} /></Field>
        <Field label="Frequency"><select value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value })}><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option><option value="yearly">Yearly</option></select></Field>
        <Field label="Next date"><input type="date" value={f.next_date} onChange={(e) => setF({ ...f, next_date: e.target.value })} /></Field>
        <Field label="End date (optional)"><input type="date" value={f.end_date || ''} onChange={(e) => setF({ ...f, end_date: e.target.value || null })} /></Field>
        <Field label="Auto-reverse after (days)" hint="For accruals; empty = no reversal"><input className="num" value={f.auto_reverse_days ?? ''} onChange={(e) => setF({ ...f, auto_reverse_days: e.target.value ? Number(e.target.value) : null })} /></Field></div>
      <Field label="Narration"><input value={f.narration} onChange={(e) => setF({ ...f, narration: e.target.value })} /></Field>
      <label className="check"><input type="checkbox" checked={f.auto_post} onChange={(e) => setF({ ...f, auto_post: e.target.checked })} />Post automatically (otherwise drafts are created for review)</label>
      <label className="check"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} />Active</label>
      <table className="data lines"><thead><tr><th>Account</th><th>Description</th><th className="num">Debit</th><th className="num">Credit</th><th /></tr></thead>
        <tbody>{f.lines.map((l: any, i: number) => <tr key={i}><td><AccountSelect value={l.account} onChange={(v) => setL(i, { account: v })} /></td><td><input value={l.description || ''} onChange={(e) => setL(i, { description: e.target.value })} /></td>
          <td><input className="num" value={l.debit || ''} onChange={(e) => setL(i, { debit: e.target.value, credit: '' })} /></td><td><input className="num" value={l.credit || ''} onChange={(e) => setL(i, { credit: e.target.value, debit: '' })} /></td>
          <td>{f.lines.length > 2 && <button className="sm ghost" onClick={() => setF({ ...f, lines: f.lines.filter((_: any, j: number) => j !== i) })}>✕</button>}</td></tr>)}</tbody></table>
      <div className="balance-bar"><button className="sm" style={{ marginRight: 'auto' }} onClick={() => setF({ ...f, lines: [...f.lines, { account: null, debit: '', credit: '' }] })}>+ Line</button>
        <span>Debit {money(d)}</span><span>Credit {money(c)}</span><span className={d === c && d ? 'ok' : 'bad'}>{d === c && d ? '✓ Balanced' : 'Not balanced'}</span></div>
    </Modal>
  )
}
