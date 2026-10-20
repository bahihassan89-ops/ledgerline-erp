import { useEffect, useState } from 'react'
import { CurrencySelect, JournalSelect, PartnerSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, StateBadge, useAction } from '../components/ui'
import { list, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, money, num, today } from '../lib/format'
import { useFetch } from '../lib/hooks'

export default function Payments({ direction }: { direction: 'inbound' | 'outbound' }) {
  const inbound = direction === 'inbound'
  const { can } = useAuth()
  const [search, setSearch] = useState('')
  const { data, reload } = useFetch<any>('/payments/', { direction, search, page_size: 100 })
  const [open, setOpen] = useState<any>(null)
  return (
    <>
      <PageHead title={inbound ? 'Customer Receipts' : 'Vendor Payments'} sub={inbound ? 'Money received from customers, applied to their invoices.' : 'Money paid to vendors, applied to their bills.'}
        actions={can('billing.payment.create') && <button className="primary" onClick={() => setOpen({})}>+ New {inbound ? 'receipt' : 'payment'}</button>} />
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
  const [f, setF] = useState<any>(() => ({ partner: p?.partner ?? null, journal: p?.journal ?? null, date: p?.date ?? today(), amount: p ? String(num(p.amount)) : '', currency: p?.currency ?? company?.base_currency, reference: p?.reference ?? '', memo: p?.memo ?? '' }))
  const [open, setOpen] = useState<any[]>([])
  const [alloc, setAlloc] = useState<Record<number, string>>({})
  const { run, busy } = useAction()
  const editable = !p || p.state === 'draft'
  const canAllocate = editable || (p?.state === 'posted' && num(p.amount_unallocated) > 0)

  useEffect(() => {
    if (!f.partner) return setOpen([])
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
      </div>
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
