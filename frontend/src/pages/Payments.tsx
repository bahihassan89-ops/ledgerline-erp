import { useEffect, useState } from 'react'
import { CurrencySelect, JournalSelect, PartnerSelect, SimpleSelect } from '../components/pickers'
import { DataTable, Export, Field, Modal, PageHead, StateBadge, useAction } from '../components/ui'
import { list, patch, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, money, num, today } from '../lib/format'
import { useFetch } from '../lib/hooks'

export default function Payments({ direction }: { direction: 'inbound' | 'outbound' }) {
  const inbound = direction === 'inbound'
  const { can } = useAuth()
  const [search, setSearch] = useState('')
  const { data, error, reload } = useFetch<any>('/payments/', { direction, search, page_size: 100 })
  const [open, setOpen] = useState<any>(null)
  return (
    <>
      <PageHead title={inbound ? 'Customer Receipts' : 'Vendor Payments'} sub={inbound ? 'Money received from customers, applied to their invoices.' : 'Money paid to vendors, applied to their bills.'}
        actions={can('billing.payment.create') && <button className="primary" onClick={() => setOpen({})}>+ New {inbound ? 'receipt' : 'payment'}</button>} />
      {error && <div className="alert bad">{error}</div>}
      <div className="card">
        <div className="toolbar"><input placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
        <DataTable rows={data?.results} onRow={setOpen} cols={[
          { key: 'number', title: 'Number', render: (r) => <span className="mono">{r.number || `Draft #${r.id}`}</span> },
          { key: 'date', title: 'Date', render: (r) => fmtDate(r.date) },
          { key: 'partner_name', title: inbound ? 'Customer' : 'Vendor' },
          { key: 'journal_code', title: 'Journal' },
          { key: 'reference', title: 'Reference' },
          { key: 'amount', title: 'Amount', num: true, render: (r) => money(r.amount, r.currency) },
          { key: 'amount_unallocated', title: 'Unapplied', num: true, render: (r) => r.state === 'posted' && num(r.amount_unallocated) ? <b>{money(r.amount_unallocated)}</b> : '—' },
          { key: 'method', title: 'Method', render: (r) => r.method === 'cheque' ? <span className="badge">Cheque {r.cheque_number} · {r.cheque_state?.replace('_', ' ')}</span> : r.method },
          { key: 'state', title: 'State', render: (r) => <StateBadge state={r.state} /> },
        ]} />
      </div>
      {open && <PaymentEditor direction={direction} pay={open.id ? open : null} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  )
}

function PaymentEditor({ direction, pay, onClose, onChanged }: any) {
  const inbound = direction === 'inbound'
  const { company, can } = useAuth()
  const [p, setP] = useState<any>(pay)
  const [f, setF] = useState<any>(() => ({ partner: p?.partner ?? null, journal: p?.journal ?? null, date: p?.date ?? today(), amount: p ? String(num(p.amount)) : '', currency: p?.currency ?? company?.base_currency, reference: p?.reference ?? '', memo: p?.memo ?? '', method: p?.method ?? 'transfer', cheque_number: p?.cheque_number ?? '', cheque_bank: p?.cheque_bank ?? '', cheque_due_date: p?.cheque_due_date ?? null }))
  const [open, setOpen] = useState<any[]>([])
  const [alloc, setAlloc] = useState<Record<number, string>>({})
  const { run, busy } = useAction()
  const editable = !p || p.state === 'draft'
  const canAllocate = editable || (p?.state === 'posted' && num(p.amount_unallocated) > 0)

  useEffect(() => {
    // Resetting the allocation list when the partner is cleared, mirroring the fetch below
    // that repopulates it once a partner is chosen again — not a cascading-render pattern.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!f.partner) { setOpen([]); return }
    const base = inbound ? '/invoices/' : '/bills/'
    list(base, { partner: f.partner, state: 'posted', move_type: inbound ? 'out_invoice' : 'in_invoice' })
      .then((xs) => setOpen(xs.filter((x) => num(x.amount_residual) > 0 && x.currency === f.currency)))
  }, [f.partner, f.currency, inbound, p?.state])

  const allocTotal = Object.values(alloc).reduce((s, v) => s + num(v), 0)
  const available = editable ? num(f.amount) : num(p?.amount_unallocated)
  const autoFill = () => {
    let left = available; const a: Record<number, string> = {}
    for (const inv of [...open].sort((x, y) => (x.due_date || '').localeCompare(y.due_date || ''))) {
      if (left <= 0) break
      const take = Math.min(left, num(inv.amount_residual)); a[inv.id] = take.toFixed(2); left -= take
    }
    setAlloc(a)
  }
  const allocations = () => Object.entries(alloc).filter(([, v]) => num(v) > 0).map(([invoice, amount]) => ({ invoice: Number(invoice), amount }))
  const saveAndPost = async () => {
    const body = { ...f, direction }
    const draft = p || await run(() => post('/payments/', body))
    if (!draft) return
    const r = await run(() => post(`/payments/${draft.id}/post/`, { allocations: allocations() }), 'Payment posted')
    if (r) { setP(r); setAlloc({}); onChanged() }
  }
  const applyMore = async () => {
    const r = await run(() => post(`/payments/${p.id}/allocate/`, { allocations: allocations() }), 'Applied')
    if (r) { setP(r); setAlloc({}); onChanged() }
  }
  return (
    <Modal wide onClose={onClose} title={<>{p?.number || (inbound ? 'New customer receipt' : 'New vendor payment')} {p && <StateBadge state={p.state} />}</>}
      footer={<>
        {p?.state === 'posted' && !p.allocations.length && can('billing.payment.post') && <button className="danger" onClick={async () => { const reason = window.prompt('Reason for cancelling?'); if (reason) { const r = await run(() => post(`/payments/${p.id}/cancel/`, { reason }), 'Cancelled'); if (r) { setP(r); onChanged() } } }}>Cancel payment</button>}
        <span style={{ flex: 1 }} />
        {editable && can('billing.payment.post') && <button className="primary" disabled={busy || !f.partner || !f.journal || !num(f.amount) || allocTotal > available + 1e-9} onClick={saveAndPost}>Post {allocTotal ? '& apply' : ''}</button>}
        {!editable && canAllocate && allocTotal > 0 && <button className="primary" disabled={busy || allocTotal > available + 1e-9} onClick={applyMore}>Apply to invoices</button>}
      </>}>
      <div className="grid form">
        <Field label={inbound ? 'Customer' : 'Vendor'}><PartnerSelect kind={inbound ? 'customer' : 'vendor'} value={f.partner} onChange={(v) => setF({ ...f, partner: v })} disabled={!editable} /></Field>
        <Field label="Bank / cash journal"><JournalSelect types={['bank', 'cash']} value={f.journal} onChange={(v) => setF({ ...f, journal: v })} disabled={!editable} /></Field>
        <Field label="Date"><input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} disabled={!editable} /></Field>
        <Field label="Amount"><input className="num" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} disabled={!editable} /></Field>
        <Field label="Currency"><CurrencySelect value={f.currency} onChange={(v) => setF({ ...f, currency: v })} disabled={!editable} /></Field>
        <Field label="Bank reference"><input value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} disabled={!editable} /></Field>
        <Field label="Method"><select value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })} disabled={!editable}><option value="transfer">Bank transfer</option><option value="cash">Cash</option><option value="cheque">Cheque</option></select></Field>
        {f.method === 'cheque' && <>
          <Field label="Cheque number"><input value={f.cheque_number} onChange={(e) => setF({ ...f, cheque_number: e.target.value })} disabled={!editable} /></Field>
          <Field label="Drawee bank"><input value={f.cheque_bank} onChange={(e) => setF({ ...f, cheque_bank: e.target.value })} disabled={!editable} /></Field>
          <Field label="Cheque due date"><input type="date" value={f.cheque_due_date || ''} onChange={(e) => setF({ ...f, cheque_due_date: e.target.value || null })} disabled={!editable} /></Field>
        </>}
      </div>
      {f.method === 'cheque' && <div className="alert info">{inbound ? 'The cheque is held in Notes Receivable (أوراق القبض) until the bank collects it.' : 'The cheque sits in Notes Payable (أوراق الدفع) until the bank pays it.'} {p?.cheque_state && <>Current status: <b>{p.cheque_state.replace('_', ' ')}</b>.</>}</div>}
      {p?.allocations?.length > 0 && <div className="card"><div className="card-head"><h3>Applied to</h3></div>
        <DataTable rows={p.allocations} cols={[{ key: 'invoice_number', title: 'Document' }, { key: 'date', title: 'Date', render: (a) => fmtDate(a.date) }, { key: 'fx_entry_number', title: 'FX entry', render: (a) => a.fx_entry_number || '—' }, { key: 'amount', title: 'Amount', num: true, render: (a) => money(a.amount) },
          { key: 'x', title: '', render: (a) => can('billing.payment.post') && <button className="sm ghost" onClick={async () => { if (window.confirm('Remove this allocation?')) { const r = await run(() => post(`/payments/${p.id}/unallocate/`, { allocation: a.id }), 'Allocation removed'); if (r) { setP(r); onChanged() } } }}>Remove</button> }]} /></div>}
      {canAllocate && f.partner && <div className="card">
        <div className="card-head"><h3>Open {inbound ? 'invoices' : 'bills'}</h3><span style={{ flex: 1 }} /><span className="muted">Available {money(available)} · Applying {money(allocTotal)}</span><button className="sm" onClick={autoFill}>Auto-apply oldest first</button></div>
        <DataTable rows={open} empty="No open documents in this currency." cols={[
          { key: 'number', title: 'Number' }, { key: 'date', title: 'Date', render: (r) => fmtDate(r.date) }, { key: 'due_date', title: 'Due', render: (r) => fmtDate(r.due_date) },
          { key: 'amount_total', title: 'Total', num: true, render: (r) => money(r.amount_total) }, { key: 'amount_residual', title: 'Open', num: true, render: (r) => money(r.amount_residual) },
          { key: 'apply', title: 'Apply', num: true, render: (r) => <input className="num" style={{ width: 120 }} value={alloc[r.id] ?? ''} onChange={(e) => setAlloc({ ...alloc, [r.id]: e.target.value })} /> }]} />
      </div>}
    </Modal>
  )
}

export function Cheques() {
  const { can } = useAuth()
  const [direction, setDirection] = useState('inbound')
  const [state, setState] = useState('')
  const { data, error, reload } = useFetch<any>('/payments/cheques/', { direction, state })
  const { run } = useAction()
  const [endorsing, setEndorsing] = useState<any>(null)
  const act = async (id: number, path: string, body: any, msg: string) => { if (await run(() => post(`/payments/${id}/${path}/`, body), msg)) reload() }
  const total = (data?.rows || []).filter((r: any) => ['in_hand', 'deposited', 'issued'].includes(r.state)).reduce((s: number, r: any) => s + num(r.amount), 0)
  const exportCols = [
    { key: 'due_date', title: 'Due date' }, { key: 'cheque_number', title: 'Cheque no.' }, { key: 'bank', title: 'Bank' },
    { key: 'partner', title: direction === 'inbound' ? 'Customer' : 'Vendor' }, { key: 'number', title: 'Payment' },
    { key: 'amount', title: 'Amount' }, { key: 'currency', title: 'Currency' }, { key: 'state', title: 'Status' },
  ]
  return (
    <>
      <PageHead title={direction === 'inbound' ? 'Cheques Received — حافظة أوراق القبض' : 'Cheques Issued — أوراق الدفع'} sub="Post-dated cheques stay in the notes account until the bank clears them. A bounced cheque reopens the invoices it paid." />
      {error && <div className="alert bad">{error}</div>}
      <div className="card">
        <div className="toolbar">
          <select value={direction} onChange={(e) => setDirection(e.target.value)}><option value="inbound">Received from customers</option><option value="outbound">Issued to vendors</option></select>
          <select value={state} onChange={(e) => setState(e.target.value)}><option value="">All states</option>{['in_hand', 'deposited', 'issued', 'cleared', 'endorsed', 'bounced'].map((x) => <option key={x} value={x}>{x.replace('_', ' ')}</option>)}</select>
          <span style={{ flex: 1 }} /><span className="muted">Outstanding: <b>{money(total)}</b></span>
          <Export name={`cheques-${direction}`} rows={data?.rows || []} cols={exportCols} />
        </div>
        <DataTable rows={data?.rows} empty="No cheques." cols={[
          { key: 'due_date', title: 'Due date', render: (r) => <>{fmtDate(r.due_date)} {r.overdue && <span className="badge bad">past due</span>}</> },
          { key: 'cheque_number', title: 'Cheque no.', render: (r) => <span className="mono">{r.cheque_number}</span> }, { key: 'bank', title: 'Bank' },
          { key: 'partner', title: direction === 'inbound' ? 'Customer' : 'Vendor', render: (r) => <>{r.partner}{r.state === 'endorsed' && r.endorsed_to && <div className="muted" style={{ fontSize: 11 }}>→ endorsed to {r.endorsed_to}</div>}</> },
          { key: 'number', title: 'Payment' },
          { key: 'amount', title: 'Amount', num: true, render: (r) => money(r.amount, r.currency) },
          { key: 'state', title: 'Status', render: (r) => <span className={`badge ${r.state === 'cleared' || r.state === 'endorsed' ? 'ok' : r.state === 'bounced' ? 'bad' : 'warn'}`}>{r.state?.replace('_', ' ')}</span> },
          { key: 'a', title: '', render: (r) => can('billing.cheque.manage') && <div className="row" style={{ justifyContent: 'flex-end' }}>
            {r.state === 'in_hand' && <button className="sm" onClick={() => act(r.id, 'cheque-deposit', {}, 'Deposited')}>Deposit</button>}
            {['in_hand', 'deposited', 'issued'].includes(r.state) && <button className="sm" onClick={() => { const d = window.prompt('Clearing date (YYYY-MM-DD):', today()); if (d) act(r.id, 'cheque-clear', { date: d }, 'Cleared — moved to bank') }}>Cleared</button>}
            {direction === 'inbound' && ['in_hand', 'deposited'].includes(r.state) && <button className="sm" onClick={() => setEndorsing(r)}>Endorse</button>}
            {['in_hand', 'deposited', 'issued'].includes(r.state) && <button className="sm danger" onClick={() => {
              const reason = window.prompt(direction === 'inbound' ? 'Bounce reason (e.g. insufficient funds):' : 'Reason for stopping the cheque:')
              if (!reason) return
              const feeStr = window.prompt('Bank fee charged for this bounce, if any (leave blank for none):')
              act(r.id, 'cheque-bounce', { reason, fee: feeStr ? feeStr.trim() : undefined }, 'Reversed')
            }}>{direction === 'inbound' ? 'Bounced' : 'Stop / cancel'}</button>}
          </div> },
        ]} />
      </div>
      {endorsing && <EndorseModal cheque={endorsing} onClose={() => setEndorsing(null)} onDone={() => { setEndorsing(null); reload() }} />}
    </>
  )
}

function EndorseModal({ cheque, onClose, onDone }: any) {
  const [toPartner, setToPartner] = useState<any>(null)
  const [date, setDate] = useState(today())
  const { run, busy } = useAction()
  const confirm = async () => {
    const r = await run(() => post(`/payments/${cheque.id}/cheque-endorse/`, { to_partner: toPartner, date }), 'Endorsed')
    if (r) onDone()
  }
  return (
    <Modal onClose={onClose} title={`Endorse cheque ${cheque.cheque_number}`}
      footer={<button className="primary" disabled={busy || !toPartner} onClick={confirm}>Endorse</button>}>
      <div className="alert info">Signs this cheque over to settle a payable instead of depositing it — the notes receivable moves straight to the vendor's payable, with no cash movement.</div>
      <div className="grid form">
        <Field label="Endorse to vendor"><PartnerSelect kind="vendor" value={toPartner} onChange={setToPartner} /></Field>
        <Field label="Date"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
      </div>
    </Modal>
  )
}

export function ChequeInstalmentPlans() {
  const { can, company } = useAuth()
  const { data, error, reload } = useFetch<any[]>('/cheque-instalment-plans/')
  const [edit, setEdit] = useState<any>(null)
  const { run } = useAction()
  const generate = async (plan: any) => {
    if (!window.confirm(`Generate ${plan.number_of_instalments} post-dated cheques for "${plan.description || plan.partner_name}"? This books every cheque immediately.`)) return
    const bank = window.prompt('Drawee bank for these cheques (optional):', '') || ''
    if (await run(() => post(`/cheque-instalment-plans/${plan.id}/generate/`, { cheque_bank: bank }), 'Cheques generated')) reload()
  }
  const exportCols = [
    { key: 'direction', title: 'Direction' }, { key: 'partner_name', title: 'Partner' }, { key: 'description', title: 'Description' },
    { key: 'total_amount', title: 'Total' }, { key: 'currency', title: 'Currency' }, { key: 'first_due_date', title: 'First due' },
    { key: 'number_of_instalments', title: 'Instalments' }, { key: 'frequency_months', title: 'Frequency (months)' },
    { key: 'cheque_count', title: 'Cheques' }, { key: 'state', title: 'State' },
  ]
  return (
    <>
      <PageHead title="Post-Dated Cheque Instalment Plans" sub="Real-estate style schedules: split a sale or purchase into equal post-dated cheques, due month by month. Generating a plan books every cheque at once; each is then deposited/cleared/bounced individually as its due date arrives."
        actions={<>{can('billing.payment.create') && <button className="primary" onClick={() => setEdit({ direction: 'inbound', partner: null, journal: null, invoice: null, description: '', currency: company?.base_currency, total_amount: '', first_due_date: today(), number_of_instalments: 6, frequency_months: 1 })}>+ New plan</button>}<Export name="cheque-instalment-plans" rows={data || []} cols={exportCols} /></>} />
      {error && <div className="alert bad">{error}</div>}
      <div className="card">
        <DataTable rows={data} onRow={(r) => r.state === 'draft' && setEdit(r)} cols={[
          { key: 'direction', title: 'Direction', render: (r) => r.direction === 'inbound' ? 'Receipt' : 'Payment' },
          { key: 'partner_name', title: 'Partner' },
          { key: 'description', title: 'Description' },
          { key: 'total_amount', title: 'Total', num: true, render: (r) => money(r.total_amount, r.currency) },
          { key: 'first_due_date', title: 'First due', render: (r) => fmtDate(r.first_due_date) },
          { key: 'number_of_instalments', title: 'Instalments', num: true, render: (r) => `${r.number_of_instalments} × every ${r.frequency_months}mo` },
          { key: 'cheque_count', title: 'Cheques', num: true },
          { key: 'state', title: '', render: (r) => <StateBadge state={r.state} /> },
          { key: 'a', title: '', render: (r) => r.state === 'draft' && can('billing.cheque.manage') && <button className="sm primary" onClick={(e) => { e.stopPropagation(); generate(r) }}>Generate</button> },
        ]} />
      </div>
      {edit && <ChequeInstalmentPlanForm initial={edit} onClose={() => { setEdit(null); reload() }} />}
    </>
  )
}

function ChequeInstalmentPlanForm({ initial, onClose }: any) {
  const [f, setF] = useState<any>(initial)
  const { run, busy } = useAction()
  const invoicePath = f.direction === 'inbound' ? '/invoices/' : '/bills/'
  const editable = !f.id || f.state === 'draft'
  const save = async () => {
    const { id, partner_name, journal_code, invoice_number, cheque_count, state, ...b } = f
    void partner_name; void journal_code; void invoice_number; void cheque_count; void state
    if (await run(() => (id ? patch(`/cheque-instalment-plans/${id}/`, b) : post('/cheque-instalment-plans/', b)), 'Saved')) onClose()
  }
  return (
    <Modal wide title={f.id ? (f.description || `Plan #${f.id}`) : 'New instalment plan'} onClose={onClose}
      footer={editable && <button className="primary" disabled={busy || !f.partner || !f.journal || !num(f.total_amount) || !f.number_of_instalments} onClick={save}>Save</button>}>
      <div className="grid form">
        <Field label="Direction"><select value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value, invoice: null })} disabled={!editable}><option value="inbound">Receipt from customer</option><option value="outbound">Payment to vendor</option></select></Field>
        <Field label={f.direction === 'inbound' ? 'Customer' : 'Vendor'}><PartnerSelect kind={f.direction === 'inbound' ? 'customer' : 'vendor'} value={f.partner} onChange={(v) => setF({ ...f, partner: v, invoice: null })} disabled={!editable} /></Field>
        <Field label="Bank / cash journal"><JournalSelect types={['bank', 'cash']} value={f.journal} onChange={(v) => setF({ ...f, journal: v })} disabled={!editable} /></Field>
        <Field label="Linked invoice (optional)"><SimpleSelect path={invoicePath} query={{ partner: f.partner || undefined, state: 'posted' }} value={f.invoice} onChange={(v) => setF({ ...f, invoice: v })} labelOf={(x: any) => `${x.number} — ${money(x.amount_residual)} open`} disabled={!editable} /></Field>
        <Field label="Description"><input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} disabled={!editable} /></Field>
        <Field label="Currency"><CurrencySelect value={f.currency} onChange={(v) => setF({ ...f, currency: v })} disabled={!editable} /></Field>
        <Field label="Total amount"><input className="num" value={f.total_amount} onChange={(e) => setF({ ...f, total_amount: e.target.value })} disabled={!editable} /></Field>
        <Field label="First instalment due"><input type="date" value={f.first_due_date} onChange={(e) => setF({ ...f, first_due_date: e.target.value })} disabled={!editable} /></Field>
        <Field label="Number of instalments"><input className="num" value={f.number_of_instalments} onChange={(e) => setF({ ...f, number_of_instalments: Number(e.target.value) || '' })} disabled={!editable} /></Field>
        <Field label="Months between instalments"><input className="num" value={f.frequency_months} onChange={(e) => setF({ ...f, frequency_months: Number(e.target.value) || 1 })} disabled={!editable} /></Field>
      </div>
      {!editable && <div className="alert info">This plan has already been generated; its cheques are managed individually on the Cheques page.</div>}
    </Modal>
  )
}

export function GuaranteeCheques() {
  const { can, company } = useAuth()
  const [direction, setDirection] = useState('')
  const [state, setState] = useState('')
  const { data, error, reload } = useFetch<any[]>('/guarantee-cheques/', { direction: direction || undefined, state: state || undefined })
  const [edit, setEdit] = useState<any>(null)
  const { run } = useAction()
  const act = async (id: number, path: string, body: any, msg: string) => { if (await run(() => post(`/guarantee-cheques/${id}/${path}/`, body), msg)) reload() }
  const exportCols = [
    { key: 'direction', title: 'Direction' }, { key: 'partner_name', title: 'Partner' }, { key: 'cheque_number', title: 'Cheque no.' },
    { key: 'bank', title: 'Bank' }, { key: 'amount', title: 'Amount' }, { key: 'currency', title: 'Currency' },
    { key: 'due_date', title: 'Due / expiry' }, { key: 'reason', title: 'Reason' }, { key: 'state', title: 'Status' },
  ]
  return (
    <>
      <PageHead title="Guarantee Cheques — شيكات ضمان" sub="Tender / performance-guarantee cheques held purely as collateral. These never touch the general ledger — they stay off balance sheet until returned or forfeited."
        actions={can('billing.cheque.manage') && <button className="primary" onClick={() => setEdit({ direction: 'held_from_customer', partner: null, cheque_number: '', bank: '', amount: '', currency: company?.base_currency, due_date: null, reason: '', received_date: today(), notes: '' })}>+ New guarantee cheque</button>} />
      {error && <div className="alert bad">{error}</div>}
      <div className="card">
        <div className="toolbar">
          <select value={direction} onChange={(e) => setDirection(e.target.value)}><option value="">All directions</option><option value="held_from_customer">Held from customer</option><option value="given_to_supplier">Given to supplier</option></select>
          <select value={state} onChange={(e) => setState(e.target.value)}><option value="">All states</option><option value="held">Held</option><option value="returned">Returned</option><option value="forfeited">Forfeited</option></select>
          <span style={{ flex: 1 }} /><Export name="guarantee-cheques" rows={data || []} cols={exportCols} />
        </div>
        <DataTable rows={data} onRow={(r) => r.state === 'held' && setEdit(r)} cols={[
          { key: 'direction', title: 'Direction', render: (r) => r.direction === 'held_from_customer' ? 'Held from customer' : 'Given to supplier' },
          { key: 'partner_name', title: 'Partner' },
          { key: 'cheque_number', title: 'Cheque no.', render: (r) => <span className="mono">{r.cheque_number}</span> }, { key: 'bank', title: 'Bank' },
          { key: 'amount', title: 'Amount', num: true, render: (r) => money(r.amount, r.currency) },
          { key: 'due_date', title: 'Due / expiry', render: (r) => fmtDate(r.due_date) },
          { key: 'reason', title: 'Reason' },
          { key: 'state', title: 'Status', render: (r) => <span className={`badge ${r.state === 'returned' ? 'ok' : r.state === 'forfeited' ? 'bad' : 'warn'}`}>{r.state}</span> },
          { key: 'a', title: '', render: (r) => r.state === 'held' && can('billing.cheque.manage') && <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button className="sm" onClick={(e) => { e.stopPropagation(); const d = window.prompt('Return date (YYYY-MM-DD):', today()); if (d) act(r.id, 'return', { date: d }, 'Returned') }}>Return</button>
            <button className="sm danger" onClick={(e) => { e.stopPropagation(); const notes = window.prompt('Forfeiture notes (book the actual cash collection/payment separately):'); if (notes !== null) act(r.id, 'forfeit', { notes }, 'Forfeited') }}>Forfeit</button>
          </div> },
        ]} />
      </div>
      {edit && <GuaranteeChequeForm initial={edit} onClose={() => { setEdit(null); reload() }} />}
    </>
  )
}

function GuaranteeChequeForm({ initial, onClose }: any) {
  const [f, setF] = useState<any>(initial)
  const { run, busy } = useAction()
  const editable = !f.id || f.state === 'held'
  const save = async () => {
    const { id, partner_name, state, returned_date, ...b } = f
    void partner_name; void state; void returned_date
    if (await run(() => (id ? patch(`/guarantee-cheques/${id}/`, b) : post('/guarantee-cheques/', b)), 'Saved')) onClose()
  }
  return (
    <Modal title={f.id ? `Guarantee cheque ${f.cheque_number}` : 'New guarantee cheque'} onClose={onClose}
      footer={editable && <button className="primary" disabled={busy || !f.partner || !f.cheque_number || !num(f.amount)} onClick={save}>Save</button>}>
      <div className="grid form">
        <Field label="Direction"><select value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })} disabled={!editable}><option value="held_from_customer">Held from customer</option><option value="given_to_supplier">Given to supplier</option></select></Field>
        <Field label="Partner"><PartnerSelect kind={f.direction === 'held_from_customer' ? 'customer' : 'vendor'} value={f.partner} onChange={(v) => setF({ ...f, partner: v })} disabled={!editable} /></Field>
        <Field label="Cheque number"><input value={f.cheque_number} onChange={(e) => setF({ ...f, cheque_number: e.target.value })} disabled={!editable} /></Field>
        <Field label="Drawee bank"><input value={f.bank} onChange={(e) => setF({ ...f, bank: e.target.value })} disabled={!editable} /></Field>
        <Field label="Amount"><input className="num" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} disabled={!editable} /></Field>
        <Field label="Currency"><CurrencySelect value={f.currency} onChange={(v) => setF({ ...f, currency: v })} disabled={!editable} /></Field>
        <Field label="Received date"><input type="date" value={f.received_date} onChange={(e) => setF({ ...f, received_date: e.target.value })} disabled={!editable} /></Field>
        <Field label="Due / expiry date (optional)"><input type="date" value={f.due_date || ''} onChange={(e) => setF({ ...f, due_date: e.target.value || null })} disabled={!editable} /></Field>
        <Field label="Reason"><input value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} disabled={!editable} placeholder="e.g. tender guarantee for contract X" /></Field>
      </div>
      <Field label="Notes"><input value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} disabled={!editable} /></Field>
      <div className="alert info">This never creates a journal entry — it only tracks custody until the cheque is returned or forfeited.</div>
    </Modal>
  )
}
