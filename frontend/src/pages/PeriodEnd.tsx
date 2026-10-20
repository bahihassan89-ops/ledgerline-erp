import { useState } from 'react'
import { Link } from 'react-router-dom'
import { JournalSelect } from '../components/pickers'
import { DataTable, Field, PageHead, Tabs, useAction } from '../components/ui'
import { post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { parseCSV } from '../lib/csv'
import { download, money, today } from '../lib/format'
import { useFetch } from '../lib/hooks'

const ICON: Record<string, string> = { ok: '✓', warn: '!', fail: '✗' }

export function CloseChecklist() {
  const { data: years } = useFetch<any[]>('/fiscal-years/')
  const periods = (years || []).flatMap((y) => y.periods)
  const [pid, setPid] = useState<number | null>(null)
  const { data, loading } = useFetch<any>('/close-checklist/', pid ? { period: pid } : {})
  return (
    <>
      <PageHead title="Month-End Close — إقفال الفترة" sub="Every control the team should see green before closing the period." />
      <div className="card">
        <div className="toolbar"><select value={pid ?? data?.period.id ?? ''} onChange={(e) => setPid(Number(e.target.value))}>{periods.map((p: any) => <option key={p.id} value={p.id}>{p.name} ({p.state})</option>)}</select>
          {data && <><span className="badge ok">{data.summary.ok} ok</span><span className="badge warn">{data.summary.warn} to review</span><span className="badge bad">{data.summary.fail} blocking</span>
            <span style={{ flex: 1 }} /><span className={`badge ${data.ready_to_close ? 'ok' : 'bad'}`}>{data.ready_to_close ? 'Ready to close' : 'Not ready'}</span><Link to="/periods"><button className="sm">Go to periods →</button></Link></>}</div>
        {loading && !data ? <div className="empty">Running checks…</div> : <table className="data"><tbody>{data?.items.map((i: any) => (
          <tr key={i.key}><td style={{ width: 36 }}><span className={`badge ${i.status === 'ok' ? 'ok' : i.status === 'warn' ? 'warn' : 'bad'}`}>{ICON[i.status]}</span></td>
            <td><b>{i.title}</b><div className="muted">{i.detail}</div></td><td className="right">{i.link && i.status !== 'ok' && <Link to={i.link}>Open →</Link>}</td></tr>))}</tbody></table>}
      </div>
    </>
  )
}

export function Revaluation() {
  const { can, company } = useAuth()
  const [date, setDate] = useState(today())
  const { data, reload, error } = useFetch<any>('/fx-revaluation/', { date })
  const { run, busy } = useAction()
  const go = async () => { if (window.confirm(`Post revaluation at ${date}? It is reversed automatically the next day.`)) { const r = await run(() => post('/fx-revaluation/', { date })); if (r) { alert(r.entries.length ? `Posted ${r.entries.join(', ')}` : 'Nothing to revalue.'); reload() } } }
  const groups = Object.entries(data || {}) as [string, any[]][]
  return (
    <>
      <PageHead title="FX Revaluation — إعادة تقييم العملات" sub={`Open foreign-currency invoices, bills and bank accounts restated at the closing rate (EAS 13). Differences go to unrealised FX and reverse on the 1st. Base: ${company?.base_currency}.`}
        actions={can('ledger.revaluation.run') && <button className="primary" disabled={busy || !groups.length} onClick={go}>Post revaluation</button>} />
      <div className="card">
        <div className="toolbar"><label className="row">Closing date <input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label><span className="muted">Enter the closing rates first (Accounting Setup → Exchange rates).</span></div>
        {error && <div className="alert bad" style={{ margin: 12 }}>{error}</div>}
        {!groups.length ? <div className="empty">Nothing to revalue at this date.</div> : groups.map(([cur, rows]) => (
          <div key={cur}><div className="card-head"><h3>{cur}</h3><span className="muted">closing rate {rows[0].rate}</span><span style={{ flex: 1 }} />Net adjustment <b>{money(rows.reduce((s, r) => s + Number(r.adjustment), 0))}</b></div>
            <DataTable rows={rows} cols={[{ key: 'kind', title: 'Item' }, { key: 'number', title: 'Document / account' }, { key: 'partner', title: 'Partner' }, { key: 'open', title: `Open (${cur})`, num: true, render: (r) => money(r.open) },
              { key: 'booked_rate', title: 'Booked rate', num: true, render: (r) => r.booked_rate ?? '—' }, { key: 'adjustment', title: 'Adjustment', num: true, render: (r) => money(r.adjustment) }]} /></div>))}
      </div>
    </>
  )
}

const TEMPLATES: Record<string, [string, string]> = {
  'opening-balances': ['account_code,debit,credit', '110110,100000,\n310101,,100000'],
  'open-items-receivable': ['partner_code,number,date,due_date,currency,amount,rate', 'C001,INV-OLD-001,2025-12-15,2026-01-14,EGP,25000,1'],
  'open-items-payable': ['partner_code,number,date,due_date,currency,amount,rate', 'V001,BILL-778,2025-12-20,2026-01-19,EGP,12000,1'],
  journal: ['account_code,debit,credit,description,partner_code,cost_center_code', '520101,150000,,Salaries,,ADM\n210201,,150000,Net salaries payable,,'],
}

export function Imports() {
  const [tab, setTab] = useState('opening-balances')
  const [rows, setRows] = useState<Record<string, string>[]>([])
  const [date, setDate] = useState(today())
  const [journal, setJournal] = useState<number | null>(null)
  const [narration, setNarration] = useState('Imported journal')
  const [result, setResult] = useState<any>(null)
  const { run, busy } = useAction()
  const kind = tab.startsWith('open-items') ? 'open-items' : tab
  const go = async () => {
    const body: any = { rows, date }
    if (tab.startsWith('open-items')) body.kind = tab.endsWith('receivable') ? 'receivable' : 'payable'
    if (tab === 'journal') { body.journal = journal; body.narration = narration }
    const r = await run(() => post(`/imports/${kind}/`, body), 'Imported')
    if (r) { setResult(r); setRows([]) }
  }
  const tpl = TEMPLATES[tab]
  return (
    <>
      <PageHead title="Imports & Go-Live — ترحيل الأرصدة" sub="Bring balances from the previous system: 1) opening trial balance, 2) open customer and vendor items, 3) opening stock (Stock → Opening stock). Opening Balance Equity must end at zero." />
      <Tabs tabs={[['opening-balances', '1. Opening trial balance'], ['open-items-receivable', '2a. Open customer items'], ['open-items-payable', '2b. Open vendor items'], ['journal', 'Journal from spreadsheet']]} value={tab} onChange={(v) => { setTab(v); setRows([]); setResult(null) }} />
      <div className="card card-body stack">
        {tab === 'opening-balances' && <div className="alert info">Every account except receivables, payables, inventory and WIP (those come from steps 2 and 3). The difference is parked in Opening Balance Equity.</div>}
        {tab.startsWith('open-items') && <div className="alert info">One row per unpaid invoice/bill with its original number and due date (negative amount = credit note). Imported items are not e-invoiced.</div>}
        {tab === 'journal' && <div className="alert info">Creates a <b>draft</b> journal entry (payroll summaries, allocations…) for review and posting.</div>}
        <div className="grid form">
          {tab !== 'open-items-receivable' && tab !== 'open-items-payable' && <Field label="Entry date"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>}
          {tab === 'journal' && <><Field label="Journal"><JournalSelect types={['general', 'bank', 'cash']} value={journal} onChange={setJournal} /></Field><Field label="Narration"><input value={narration} onChange={(e) => setNarration(e.target.value)} /></Field></>}
          <Field label="CSV file" hint={`Columns: ${tpl[0]}`}><input type="file" accept=".csv" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setRows(parseCSV(await f.text())) }} /></Field>
        </div>
        <div className="row"><button className="sm" onClick={() => download(`${tab}-template.csv`, `${tpl[0]}\n${tpl[1]}\n`)}>Download template</button><span style={{ flex: 1 }} />
          <button className="primary" disabled={busy || !rows.length || (tab === 'journal' && !journal)} onClick={go}>Import {rows.length || ''} rows</button></div>
        {rows.length > 0 && <div style={{ maxHeight: 300, overflow: 'auto' }}><DataTable rows={rows.map((r, i) => ({ ...r, id: i }))} cols={Object.keys(rows[0]).map((k) => ({ key: k, title: k }))} /></div>}
        {result && <div className="alert ok"><pre className="mono" style={{ margin: 0 }}>{JSON.stringify(result, null, 2)}</pre></div>}
      </div>
    </>
  )
}
