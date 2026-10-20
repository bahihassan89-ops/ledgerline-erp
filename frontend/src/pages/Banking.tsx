import { useState } from 'react'
import { AccountSelect, JournalSelect, PartnerSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, Tabs, useAction } from '../components/ui'
import { get, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, money, num, today } from '../lib/format'
import { useFetch, useOptions } from '../lib/hooks'

export default function Banking() {
  const { can } = useAuth()
  const [tab, setTab] = useState('statements')
  const { data, reload } = useFetch<any>('/bank-statements/')
  const [open, setOpen] = useState<any>(null)
  const [imp, setImp] = useState(false)
  return (
    <>
      <PageHead title="Bank Reconciliation — التسوية البنكية" sub="Import the bank statement, match each line to the books, post bank charges, and prove the balances agree."
        actions={can('banking.statement.manage') && <button className="primary" onClick={() => setImp(true)}>Import statement</button>} />
      <Tabs tabs={[['statements', 'Statements'], ['report', 'Reconciliation statement']]} value={tab} onChange={setTab} />
      {tab === 'statements' ? <div className="card"><DataTable rows={data?.results} onRow={setOpen} empty="No statements imported yet." cols={[
        { key: 'journal_code', title: 'Bank' }, { key: 'reference', title: 'Reference' },
        { key: 'period', title: 'Period', render: (r) => `${fmtDate(r.date_from)} – ${fmtDate(r.date_to)}` },
        { key: 'opening_balance', title: 'Opening', num: true, render: (r) => money(r.opening_balance) },
        { key: 'closing_balance', title: 'Closing', num: true, render: (r) => money(r.closing_balance) },
        { key: 'unmatched', title: 'Unmatched', num: true, render: (r) => r.unmatched ? <span className="badge warn">{r.unmatched}</span> : '✓' },
        { key: 'state', title: 'State', render: (r) => <span className={`badge ${r.state === 'reconciled' ? 'ok' : 'warn'}`}>{r.state}</span> }]} /></div>
        : <ReconReport />}
      {imp && <ImportDialog onClose={() => setImp(false)} onDone={(st: any) => { setImp(false); reload(); setOpen(st) }} />}
      {open && <StatementView id={open.id} onClose={() => { setOpen(null); reload() }} />}
    </>
  )
}

function ImportDialog({ onClose, onDone }: any) {
  const [f, setF] = useState<any>({ journal: null, opening_balance: '', closing_balance: '', reference: '', csv: '' })
  const { run, busy } = useAction()
  const go = async () => { const r = await run(() => post('/bank-statements/import/', f), 'Statement imported'); if (r) { await run(() => post(`/bank-statements/${r.id}/auto-match/`)); onDone(r) } }
  return (
    <Modal wide title="Import bank statement" onClose={onClose} footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={busy || !f.journal || !f.csv} onClick={go}>Import & auto-match</button></>}>
      <div className="grid form">
        <Field label="Bank journal"><JournalSelect types={['bank']} value={f.journal} onChange={(v) => setF({ ...f, journal: v })} /></Field>
        <Field label="Statement reference"><input value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
        <Field label="Opening balance (per bank)"><input className="num" value={f.opening_balance} onChange={(e) => setF({ ...f, opening_balance: e.target.value })} /></Field>
        <Field label="Closing balance (per bank)"><input className="num" value={f.closing_balance} onChange={(e) => setF({ ...f, closing_balance: e.target.value })} /></Field>
      </div>
      <Field label="CSV file" hint="Columns: date, description, reference and either amount (signed) or debit/credit (bank's view). Dates YYYY-MM-DD or DD/MM/YYYY.">
        <input type="file" accept=".csv,.txt" onChange={async (e) => { const file = e.target.files?.[0]; if (file) setF({ ...f, csv: await file.text() }) }} />
      </Field>
      {f.csv && <pre className="mono" style={{ maxHeight: 160, overflow: 'auto', background: 'var(--surface-2)', padding: 8, borderRadius: 6 }}>{f.csv.split('\n').slice(0, 8).join('\n')}</pre>}
    </Modal>
  )
}

function StatementView({ id, onClose }: { id: number; onClose: () => void }) {
  const { can } = useAuth()
  const { data: st, setData, reload } = useFetch<any>(`/bank-statements/${id}/`)
  const [cands, setCands] = useState<any[] | null>(null)
  const [line, setLine] = useState<any>(null)
  const [mode, setMode] = useState<'match' | 'entry' | null>(null)
  const [entry, setEntry] = useState<any>({ account: null, partner: null, description: '' })
  const { run } = useAction()
  const edit = can('banking.statement.manage') && st?.state === 'open'
  const openMatch = async (l: any) => { setLine(l); setMode('match'); setCands(await get(`/bank-statements/${id}/candidates/`)) }
  const doMatch = async (jl: number) => { const r = await run(() => post(`/bank-statements/${id}/match/`, { line: line.id, journal_line: jl }), 'Matched'); if (r) { setData(r); setMode(null) } }
  const doEntry = async () => { const r = await run(() => post(`/bank-statements/${id}/create-entry/`, { line: line.id, ...entry }), 'Entry posted and matched'); if (r) { setData(r); setMode(null) } }
  if (!st) return null
  return (
    <Modal wide title={`${st.journal_code} statement ${fmtDate(st.date_from)} – ${fmtDate(st.date_to)}`} onClose={onClose}
      footer={edit && <><button onClick={async () => { const r = await run(() => post(`/bank-statements/${id}/auto-match/`)); if (r) setData(r.statement) }}>Auto-match</button>
        <button className="primary" disabled={st.unmatched > 0} onClick={async () => { if (await run(() => post(`/bank-statements/${id}/close/`), 'Statement reconciled')) reload() }}>Mark reconciled</button></>}>
      <div className="row"><span>Opening <b>{money(st.opening_balance)}</b></span><span>Closing <b>{money(st.closing_balance)}</b></span><span style={{ flex: 1 }} /><span className={`badge ${st.unmatched ? 'warn' : 'ok'}`}>{st.unmatched ? `${st.unmatched} to match` : 'All matched'}</span></div>
      <DataTable rows={st.lines} cols={[
        { key: 'date', title: 'Date', render: (l) => fmtDate(l.date) }, { key: 'description', title: 'Description' }, { key: 'reference', title: 'Ref' },
        { key: 'amount', title: 'Amount', num: true, render: (l) => <span style={{ color: num(l.amount) < 0 ? 'var(--bad)' : undefined }}>{money(l.amount)}</span> },
        { key: 'm', title: 'Matched to', render: (l) => l.matched_entry ? <span className="badge ok">{l.matched_entry} · {l.matched_by}</span> : <span className="badge warn">unmatched</span> },
        { key: 'a', title: '', render: (l) => edit && (l.matched_line ? <button className="sm ghost" onClick={async () => { const r = await run(() => post(`/bank-statements/${id}/unmatch/`, { line: l.id })); if (r) setData(r) }}>Unmatch</button>
          : <div className="row"><button className="sm" onClick={() => openMatch(l)}>Match…</button><button className="sm" onClick={() => { setLine(l); setMode('entry'); setEntry({ account: null, partner: null, description: l.description }) }}>Create entry</button></div>) }]} />
      {mode === 'match' && <Modal wide title={`Match ${money(line.amount)} — ${line.description}`} onClose={() => setMode(null)}>
        <DataTable rows={cands?.filter((c) => Math.abs(num(c.amount) - num(line.amount)) < 0.005)} empty="No unmatched book entry with this exact amount. Post the missing receipt/payment, or create an entry for bank charges."
          cols={[{ key: 'date', title: 'Date', render: (c) => fmtDate(c.date) }, { key: 'number', title: 'Entry' }, { key: 'reference', title: 'Reference' }, { key: 'partner', title: 'Partner' }, { key: 'amount', title: 'Amount', num: true, render: (c) => money(c.amount) }, { key: 'x', title: '', render: (c) => <button className="sm primary" onClick={() => doMatch(c.id)}>Match</button> }]} />
      </Modal>}
      {mode === 'entry' && <Modal title="Post entry for statement line" onClose={() => setMode(null)} footer={<button className="primary" disabled={!entry.account} onClick={doEntry}>Post & match</button>}>
        <div className="muted">{fmtDate(line.date)} · {line.description} · <b>{money(line.amount)}</b></div>
        <Field label="Counter account" hint="e.g. Bank charges, Interest income, or a receivable/payable for direct debits"><AccountSelect value={entry.account} onChange={(v) => setEntry({ ...entry, account: v })} /></Field>
        <Field label="Partner (only for receivable/payable)"><PartnerSelect allowEmpty value={entry.partner} onChange={(v) => setEntry({ ...entry, partner: v })} /></Field>
        <Field label="Description"><input value={entry.description} onChange={(e) => setEntry({ ...entry, description: e.target.value })} /></Field>
      </Modal>}
    </Modal>
  )
}

function ReconReport() {
  const journals = useOptions<any>('/journals/', { type: 'bank' })
  const [j, setJ] = useState<number | null>(null)
  const [asOf, setAsOf] = useState(today())
  const jid = j ?? journals[0]?.id
  const { data: r } = useFetch<any>(jid ? '/bank-statements/reconciliation/' : null, { journal: jid, as_of: asOf })
  const rows = (items: any[]) => <DataTable rows={items} empty="None." cols={[{ key: 'date', title: 'Date', render: (x) => fmtDate(x.date) }, { key: 'number', title: 'Entry' }, { key: 'partner', title: 'Partner' }, { key: 'description', title: 'Description' }, { key: 'amount', title: 'Amount', num: true, render: (x) => money(x.amount) }]} />
  return (
    <div className="card">
      <div className="toolbar"><JournalSelect types={['bank']} value={jid} onChange={setJ} /><label className="row">As of <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label><span style={{ flex: 1 }} /><button onClick={() => window.print()}>Print</button></div>
      {r && <div className="card-body stack">
        <div className="row"><h2>{r.account}</h2><span className={`badge ${r.reconciled ? 'ok' : 'bad'}`}>{r.reconciled ? '✓ Reconciled' : `✗ Difference ${money(r.difference)}`}</span>
          {r.start_agrees === false && <span className="badge warn">First statement opening ({money(r.first_statement_opening)}) ≠ books at start ({money(r.book_balance_at_start)})</span>}</div>
        <table className="data"><tbody>
          <tr><td>Balance per bank statement {r.statement_date && `(${fmtDate(r.statement_date)})`}</td><td className="num"><b>{money(r.balance_per_bank)}</b></td></tr>
          <tr><td>Add: deposits in transit (in books, not yet on statement)</td><td className="num">{money(r.outstanding_receipts_total)}</td></tr>
          <tr><td>Less: outstanding payments / unpresented cheques</td><td className="num">({money(r.outstanding_payments_total)})</td></tr>
          <tr className="group"><td>Adjusted bank balance</td><td className="num">{money(r.adjusted_bank_balance)}</td></tr>
          <tr><td>Balance per books</td><td className="num"><b>{money(r.balance_per_books)}</b></td></tr>
          <tr><td>Add/(less): statement items not yet booked</td><td className="num">{money(r.statement_items_not_in_books)}</td></tr>
          <tr className="group"><td>Adjusted book balance</td><td className="num">{money(r.adjusted_book_balance)}</td></tr>
        </tbody></table>
        <h3>Deposits in transit</h3>{rows(r.outstanding_receipts)}
        <h3>Outstanding payments</h3>{rows(r.outstanding_payments)}
      </div>}
    </div>
  )
}
