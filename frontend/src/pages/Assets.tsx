import { useState } from 'react'
import Attachments from '../components/Attachments'
import { AccountSelect, CostCenterSelect, SimpleSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, Tabs, useAction } from '../components/ui'
import { patch, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, label, money, num, today } from '../lib/format'
import { useFetch } from '../lib/hooks'

const STATE_TONE: Record<string, string> = { draft: '', running: 'ok', fully_depreciated: 'info', disposed: 'bad' }

export default function Assets() {
  const { can } = useAuth()
  const [tab, setTab] = useState('register')
  const [asOf, setAsOf] = useState(today())
  const reg = useFetch<any>('/assets/register/', { as_of: asOf })
  const regError = reg.error
  const all = useFetch<any>('/assets/', { page_size: 500 })
  const unreg = useFetch<any[]>('/assets/unregistered/')
  const [year, setYear] = useState(new Date().getFullYear())
  const tax = useFetch<any>(tab === 'tax' ? '/assets/tax-depreciation/' : null, { year })
  const [edit, setEdit] = useState<any>(null)
  const { run } = useAction()
  const reload = () => { reg.reload(); all.reload(); unreg.reload() }
  const runDep = async () => {
    const d = window.prompt('Post depreciation up to (YYYY-MM-DD):', today())
    if (d) { const r = await run(() => post('/assets/run-depreciation/', { up_to: d })); if (r) { alert(r.entries.length ? `Posted: ${r.entries.join(', ')}` : 'Nothing to post.'); reload() } }
  }
  return (
    <>
      <PageHead title="Fixed Assets — الأصول الثابتة" sub="Register, monthly depreciation (EAS 10), disposals, and tax depreciation under Income Tax Law 91/2005 art. 25."
        actions={<>{can('assets.depreciation.run') && <button onClick={runDep}>Run depreciation</button>}{can('assets.asset.manage') && <button className="primary" onClick={() => setEdit({})}>+ New asset</button>}</>} />
      {regError && <div className="alert bad">{regError}</div>}
      {unreg.data && unreg.data.length > 0 && <div className="alert warn" style={{ marginBottom: 12 }}>{unreg.data.length} fixed-asset purchase(s) on vendor bills are not in the register yet — see the “Unregistered purchases” tab.</div>}
      <Tabs tabs={[['register', 'Register'], ['all', 'All assets'], ['unreg', 'Unregistered purchases'], ['tax', 'Tax depreciation']]} value={tab} onChange={setTab} />
      {tab === 'register' && <div className="card">
        <div className="toolbar"><label className="row">As of <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label><span style={{ flex: 1 }} />
          {reg.data && <span className={`badge ${reg.data.reconciled ? 'ok' : 'bad'}`}>{reg.data.reconciled ? '✓ Register agrees with GL' : `✗ GL cost ${money(reg.data.gl_cost)} / accumulated ${money(reg.data.gl_accumulated)}`}</span>}</div>
        <DataTable rows={reg.data?.rows} onRow={(r) => setEdit(all.data?.results.find((a: any) => a.id === r.id))} cols={[
          { key: 'code', title: 'Code', render: (r) => <span className="mono">{r.code}</span> }, { key: 'name', title: 'Asset', render: (r) => <>{r.name} {r.name_ar && <span className="muted">· {r.name_ar}</span>}</> },
          { key: 'category', title: 'Category' }, { key: 'in_service_date', title: 'In service', render: (r) => fmtDate(r.in_service_date) },
          { key: 'custodian', title: 'Custodian / location', render: (r) => [r.custodian, r.location].filter(Boolean).join(' · ') },
          { key: 'cost', title: 'Cost', num: true, render: (r) => money(r.cost) }, { key: 'accumulated', title: 'Accumulated', num: true, render: (r) => money(r.accumulated) },
          { key: 'net_book_value', title: 'Net book value', num: true, render: (r) => <b>{money(r.net_book_value)}</b> }]}
          foot={reg.data && <tr><td colSpan={5}>Total</td><td className="num">{money(reg.data.total_cost)}</td><td className="num">{money(reg.data.total_accumulated)}</td><td className="num">{money(reg.data.total_nbv)}</td></tr>} />
      </div>}
      {tab === 'all' && <div className="card"><DataTable rows={all.data?.results} onRow={setEdit} cols={[
        { key: 'code', title: 'Code' }, { key: 'name', title: 'Asset' }, { key: 'category_name', title: 'Category' },
        { key: 'cost', title: 'Cost', num: true, render: (r) => money(r.cost) }, { key: 'net_book_value', title: 'NBV', num: true, render: (r) => money(r.net_book_value) },
        { key: 'state', title: 'State', render: (r) => <span className={`badge ${STATE_TONE[r.state]}`}>{label(r.state)}</span> }]} /></div>}
      {tab === 'unreg' && <div className="card"><DataTable rows={unreg.data} empty="All capital purchases are registered." cols={[
        { key: 'bill', title: 'Bill' }, { key: 'date', title: 'Date', render: (r) => fmtDate(r.date) }, { key: 'description', title: 'Description' }, { key: 'account', title: 'Account' },
        { key: 'amount', title: 'Amount', num: true, render: (r) => money(r.amount, r.currency) },
        { key: 'x', title: '', render: (r) => can('assets.asset.manage') && <button className="sm primary" onClick={async () => { const a = await run(() => post('/assets/from-bill-line/', { line: r.line }), 'Asset created (draft)'); if (a) { reload(); setEdit(a) } }}>Register asset</button> }]} /></div>}
      {tab === 'tax' && <div className="card">
        <div className="toolbar"><label className="row">Tax year <input type="number" style={{ width: 100 }} value={year} onChange={(e) => setYear(Number(e.target.value))} /></label></div>
        <DataTable rows={tax.data?.rows} cols={[{ key: 'category', title: 'Category' }, { key: 'tax_method', title: 'Tax method', render: (r) => label(r.tax_method) }, { key: 'tax_rate_pct', title: 'Rate', num: true, render: (r) => `${num(r.tax_rate_pct)}%` },
          { key: 'accounting_depreciation', title: 'Accounting', num: true, render: (r) => money(r.accounting_depreciation) }, { key: 'tax_depreciation', title: 'Tax', num: true, render: (r) => money(r.tax_depreciation) },
          { key: 'difference', title: 'Tax − accounting', num: true, render: (r) => money(r.difference) }, { key: 'closing_tax_basis', title: 'Closing tax basis', num: true, render: (r) => r.closing_tax_basis ? money(r.closing_tax_basis) : '—' }]} />
        {tax.data && <div className="card-body muted">{tax.data.note}</div>}
      </div>}
      {edit && <AssetForm initial={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload() }} />}
    </>
  )
}

function AssetForm({ initial, onClose, onSaved }: any) {
  const { can } = useAuth()
  const [f, setF] = useState<any>({ code: '', name: '', name_ar: '', category: null, serial_number: '', location: '', custodian: '', cost_center: null, acquisition_date: today(), in_service_date: today(), depreciation_start: null, cost: '', salvage_value: '0', opening_accumulated: '0', useful_life_months: '', method: 'straight_line', declining_rate: '0', notes: '', state: 'draft', ...initial })
  const sched = useFetch<any[]>(f.id && f.state !== 'draft' ? `/assets/${f.id}/schedule/` : null)
  const [disp, setDisp] = useState(false)
  const { run, busy } = useAction()
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const draft = f.state === 'draft'
  const save = async () => {
    const { id, accumulated, net_book_value, category_name, bill_number, state, ...b } = f; void accumulated; void net_book_value; void category_name; void bill_number; void state
    if (!b.useful_life_months) delete b.useful_life_months
    const r = await run(() => (id ? patch(`/assets/${id}/`, b) : post('/assets/', b)), 'Saved'); if (r) setF(r); return r
  }
  const confirm = async () => { const s = draft ? await save() : f; if (!s) return; const r = await run(() => post(`/assets/${s.id}/confirm/`), 'Asset in service; schedule created'); if (r) onSaved() }
  return (
    <Modal wide title={f.id ? `${f.code} ${f.name}` : 'New fixed asset'} onClose={onClose}
      footer={<>{f.id && ['running', 'fully_depreciated'].includes(f.state) && can('assets.asset.dispose') && <button className="danger" onClick={() => setDisp(true)}>Dispose / sell</button>}<span style={{ flex: 1 }} />
        {can('assets.asset.manage') && <button disabled={busy} onClick={async () => { if (await save()) onSaved() }}>Save</button>}
        {draft && can('assets.asset.manage') && <button className="primary" disabled={busy} onClick={confirm}>Confirm (start depreciation)</button>}</>}>
      {f.bill_number && <div className="alert info">Purchased on vendor bill {f.bill_number}.</div>}
      <div className="grid form">
        <Field label="Code"><input className="mono" value={f.code} onChange={(e) => set('code', e.target.value)} /></Field>
        <Field label="Name"><input value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="الاسم بالعربية"><input dir="rtl" value={f.name_ar} onChange={(e) => set('name_ar', e.target.value)} /></Field>
        <Field label="Category"><SimpleSelect allowEmpty={false} path="/asset-categories/" labelOf={(c) => `${c.name} — ${c.useful_life_months} months, tax ${num(c.tax_rate_pct)}%`} value={f.category} onChange={(v) => set('category', v)} disabled={!draft} /></Field>
        <Field label="Serial number"><input value={f.serial_number} onChange={(e) => set('serial_number', e.target.value)} /></Field>
        <Field label="Location"><input value={f.location} onChange={(e) => set('location', e.target.value)} /></Field>
        <Field label="Custodian (العهدة)"><input value={f.custodian} onChange={(e) => set('custodian', e.target.value)} /></Field>
        <Field label="Cost center"><CostCenterSelect value={f.cost_center} onChange={(v) => set('cost_center', v)} /></Field>
        <Field label="Acquisition date"><input type="date" value={f.acquisition_date} onChange={(e) => set('acquisition_date', e.target.value)} disabled={!draft} /></Field>
        <Field label="In-service date"><input type="date" value={f.in_service_date} onChange={(e) => set('in_service_date', e.target.value)} disabled={!draft} /></Field>
        <Field label="Cost (base currency)"><input className="num" value={f.cost} onChange={(e) => set('cost', e.target.value)} disabled={!draft} /></Field>
        <Field label="Salvage value"><input className="num" value={f.salvage_value} onChange={(e) => set('salvage_value', e.target.value)} disabled={!draft} /></Field>
        <Field label="Useful life (months)" hint="Empty = category default"><input className="num" value={f.useful_life_months} onChange={(e) => set('useful_life_months', e.target.value)} disabled={!draft} /></Field>
        <Field label="Method"><select value={f.method} onChange={(e) => set('method', e.target.value)} disabled={!draft}><option value="straight_line">Straight line</option><option value="declining_balance">Declining balance</option></select></Field>
        {f.method === 'declining_balance' && <Field label="Annual rate %"><input className="num" value={f.declining_rate} onChange={(e) => set('declining_rate', e.target.value)} disabled={!draft} /></Field>}
      </div>
      <details><summary className="muted">Go-live: asset already partly depreciated in the previous system</summary>
        <div className="grid form" style={{ marginTop: 10 }}>
          <Field label="Accumulated depreciation before go-live"><input className="num" value={f.opening_accumulated} onChange={(e) => set('opening_accumulated', e.target.value)} disabled={!draft} /></Field>
          <Field label="First month to depreciate here"><input type="date" value={f.depreciation_start || ''} onChange={(e) => set('depreciation_start', e.target.value || null)} disabled={!draft} /></Field>
        </div></details>
      {sched.data && <div className="card"><div className="card-head"><h3>Depreciation schedule</h3><span className="muted">NBV {money(f.net_book_value)}</span></div>
        <div style={{ maxHeight: 260, overflow: 'auto' }}><DataTable rows={sched.data} cols={[{ key: 'date', title: 'Month', render: (l) => fmtDate(l.date) }, { key: 'amount', title: 'Depreciation', num: true, render: (l) => money(l.amount) }, { key: 'posted', title: 'Status', render: (l) => l.posted ? <span className="badge ok">{l.entry_number}</span> : <span className="badge">planned</span> }]} /></div></div>}
      {f.id && <Attachments type="assets.asset" id={f.id} canEdit={can('assets.asset.manage')} />}
      {disp && <DisposeDialog asset={f} onClose={() => setDisp(false)} onDone={() => { setDisp(false); onSaved() }} />}
    </Modal>
  )
}

function DisposeDialog({ asset, onClose, onDone }: any) {
  const [f, setF] = useState<any>({ date: today(), proceeds: '0', proceeds_account: null, reason: '' })
  const { run, busy } = useAction()
  return (
    <Modal title={`Dispose ${asset.code}`} onClose={onClose} footer={<button className="primary danger" disabled={busy} onClick={async () => { if (await run(() => post(`/assets/${asset.id}/dispose/`, f), 'Disposal posted')) onDone() }}>Post disposal</button>}>
      <div className="alert info">Depreciation is posted up to the disposal date first. Gain or loss = proceeds − net book value.</div>
      <div className="grid form">
        <Field label="Disposal date"><input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
        <Field label="Sale proceeds (0 = scrapped)"><input className="num" value={f.proceeds} onChange={(e) => setF({ ...f, proceeds: e.target.value })} /></Field>
      </div>
      {num(f.proceeds) > 0 && <Field label="Proceeds received into"><AccountSelect value={f.proceeds_account} onChange={(v) => setF({ ...f, proceeds_account: v })} filter={(a) => ['bank', 'cash', 'current_asset'].includes(a.account_type)} /></Field>}
      <Field label="Reason / buyer"><input value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
    </Modal>
  )
}
