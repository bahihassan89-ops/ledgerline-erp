import { useMemo, useState } from 'react'
import { AccountSelect, CostCenterSelect, CurrencySelect, JournalSelect, PartnerSelect, ProductSelect, SimpleSelect, TaxSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, StateBadge, useAction } from '../components/ui'
import { del, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, money, num, today } from '../lib/format'
import { useFetch, useOptions } from '../lib/hooks'
import { EntryById } from './JournalEntries'

type Kind = 'invoices' | 'bills'
const CFG = {
  invoices: { title: 'Customer Invoices', base: '/invoices/', types: [['out_invoice', 'Invoice'], ['out_refund', 'Credit note']], partner: 'customer' as const, p: 'billing.invoice', scope: 'sale' as const, acc: 'income', what: 'invoice' },
  bills: { title: 'Vendor Bills', base: '/bills/', types: [['in_invoice', 'Bill'], ['in_refund', 'Debit note']], partner: 'vendor' as const, p: 'billing.bill', scope: 'purchase' as const, acc: 'expense', what: 'bill' },
}

export default function Documents({ kind }: { kind: Kind }) {
  const cfg = CFG[kind]
  const { can } = useAuth()
  const [f, setF] = useState({ search: '', state: '', payment_state: '', move_type: '' })
  const { data, reload } = useFetch<any>(cfg.base, { ...f, page_size: 100 })
  const [open, setOpen] = useState<any>(null)
  const totalDue = (data?.results || []).filter((r: any) => r.state === 'posted').reduce((s: number, r: any) => s + num(r.amount_residual) * num(r.rate) * (r.move_type.endsWith('refund') ? -1 : 1), 0)
  return (
    <>
      <PageHead title={cfg.title} sub={kind === 'invoices' ? 'Accounts receivable: what customers owe you.' : 'Accounts payable: what you owe vendors. Bills from purchase orders are matched to receipts (3-way match).'}
        actions={can(`${cfg.p}.create`) && <button className="primary" onClick={() => setOpen({ move_type: cfg.types[0][0] })}>+ New {cfg.what}</button>} />
      <div className="card">
        <div className="toolbar">
          <input placeholder="Search number, reference, partner…" value={f.search} onChange={(e) => setF({ ...f, search: e.target.value })} />
          <select value={f.move_type} onChange={(e) => setF({ ...f, move_type: e.target.value })}><option value="">All types</option>{cfg.types.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <select value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })}><option value="">All states</option><option value="draft">Draft</option><option value="to_approve">Waiting approval</option><option value="posted">Posted</option><option value="cancelled">Cancelled</option></select>
          <select value={f.payment_state} onChange={(e) => setF({ ...f, payment_state: e.target.value })}><option value="">Any payment</option><option value="not_paid">Unpaid</option><option value="partial">Partially paid</option><option value="paid">Paid</option></select>
          <span style={{ flex: 1 }} /><span className="muted">Open balance (base): <b>{money(totalDue)}</b></span>
        </div>
        <DataTable rows={data?.results} onRow={setOpen} cols={[
          { key: 'number', title: 'Number', render: (r) => <span className="mono">{r.number || `Draft #${r.id}`}</span> },
          { key: 'type', title: 'Type', render: (r) => r.move_type.endsWith('refund') ? <span className="badge warn">{cfg.types[1][1]}</span> : cfg.types[0][1] },
          { key: 'partner_name', title: kind === 'invoices' ? 'Customer' : 'Vendor' },
          { key: 'reference', title: 'Reference' },
          { key: 'date', title: 'Date', render: (r) => fmtDate(r.date) },
          { key: 'due_date', title: 'Due', render: (r) => <>{fmtDate(r.due_date)} {r.days_overdue > 0 && <span className="badge bad">{r.days_overdue}d late</span>}</> },
          { key: 'amount_total', title: 'Total', num: true, render: (r) => money(r.amount_total, r.currency) },
          { key: 'amount_residual', title: 'Amount due', num: true, render: (r) => r.state === 'posted' ? money(r.amount_residual, r.currency) : '—' },
          { key: 'state', title: 'Status', render: (r) => <>{<StateBadge state={r.state} />} {r.state === 'posted' && <StateBadge state={r.payment_state} />}</> },
        ]} />
      </div>
      {open && <DocEditor kind={kind} doc={open.id ? open : null} defaults={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  )
}

type L = { id?: number; product: number | null; description: string; account: number | null; quantity: string; unit_price: string; discount_pct: string; tax: number | null; cost_center: number | null; purchase_line?: number; sales_line?: number }

export function DocEditor({ kind, doc, defaults, onClose, onChanged }: { kind: Kind; doc: any; defaults?: any; onClose: () => void; onChanged: () => void }) {
  const cfg = CFG[kind]
  const { can, company } = useAuth()
  const [d, setD] = useState<any>(doc)
  const editable = !d || d.state === 'draft'
  const [h, setH] = useState<any>(() => ({ move_type: d?.move_type ?? defaults?.move_type ?? cfg.types[0][0], partner: d?.partner ?? null, date: d?.date ?? today(), due_date: d?.due_date ?? '', payment_term: d?.payment_term ?? null, currency: d?.currency ?? company?.base_currency, reference: d?.reference ?? '', notes: d?.notes ?? '' }))
  const [lines, setLines] = useState<L[]>(() => d ? d.lines.map((l: any) => ({ ...l, quantity: String(num(l.quantity)), unit_price: String(num(l.unit_price)), discount_pct: String(num(l.discount_pct)) })) : [])
  const products = useOptions<any>('/products/', { active: true })
  const cats = useOptions<any>('/product-categories/')
  const taxes = useOptions<any>('/taxes/')
  const settings = useFetch<any>('/accounting-settings/').data
  const [showEntry, setShowEntry] = useState(false)
  const [pay, setPay] = useState(false)
  const { run, busy } = useAction()
  const pById = useMemo(() => Object.fromEntries(products.map((p) => [p.id, p])), [products])
  const tById = useMemo(() => Object.fromEntries(taxes.map((t) => [t.id, t])), [taxes])

  const calc = (l: L) => {
    const gross = num(l.quantity) * num(l.unit_price) * (1 - num(l.discount_pct) / 100)
    const t = l.tax ? tById[l.tax] : null
    const r = t ? num(t.rate) / 100 : 0
    if (t?.price_include) { const sub = gross / (1 + r); return { sub, tax: gross - sub } }
    return { sub: gross, tax: gross * r }
  }
  const totals = lines.reduce((s, l) => { const c = calc(l); return { sub: s.sub + c.sub, tax: s.tax + c.tax } }, { sub: 0, tax: 0 })
  const setLine = (i: number, patch: Partial<L>) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)))
  const pickProduct = (i: number, id: number | null) => {
    const p = id ? pById[id] : null
    if (!p) return setLine(i, { product: null })
    const cat = cats.find((c) => c.id === p.category)
    const account = kind === 'invoices' ? (p.income_account || cat?.income_account)
      : (p.type === 'stock' ? settings?.grni_account : (p.expense_account || cat?.expense_account))
    setLine(i, { product: id, description: p.name, account: account ?? null, unit_price: String(num(kind === 'invoices' ? p.sale_price : p.purchase_price)), tax: (kind === 'invoices' ? p.sale_tax : p.purchase_tax) ?? null })
  }
  const body = () => ({ ...h, due_date: h.due_date || null, lines: lines.map((l) => ({ id: l.id, product: l.product, description: l.description || '-', account: l.account, quantity: l.quantity || '0', unit_price: l.unit_price || '0', discount_pct: l.discount_pct || '0', tax: l.tax, cost_center: l.cost_center })) })
  const save = async () => { const r = await run(() => (d ? put(`${cfg.base}${d.id}/`, body()) : post(cfg.base, body())), 'Saved'); if (r) { setD(r); onChanged() }; return r }
  const doPost = async () => {
    const saved = editable ? await save() : d
    if (!saved) return
    const r = await run(() => post(`${cfg.base}${saved.id}/post/`), undefined)
    if (r) { setD(r); onChanged() }
  }
  const act = async (path: string, msg: string, payload?: any) => { const r = await run(() => post(`${cfg.base}${d.id}/${path}/`, payload), msg); if (r) { onChanged(); setD(r) } }
  const isRefund = h.move_type.endsWith('refund')
  return (
    <Modal wide onClose={onClose} title={<>{d?.number || (d ? `Draft #${d.id}` : `New ${isRefund ? cfg.types[1][1].toLowerCase() : cfg.what}`)} {d && <StateBadge state={d.state} />} {d?.state === 'posted' && <StateBadge state={d.payment_state} />}</>}
      footer={<>
        {d && editable && can(`${cfg.p}.create`) && <button className="danger" onClick={async () => { if (window.confirm('Delete this draft?') && await run(() => del(`${cfg.base}${d.id}/`)) !== undefined) { onChanged(); onClose() } }}>Delete</button>}
        {d?.journal_entry && <button onClick={() => setShowEntry(true)}>View journal entry</button>}
        <span style={{ flex: 1 }} />
        {d?.state === 'posted' && can(`${cfg.p}.cancel`) && !d.move_type.endsWith('refund') && <button onClick={() => act('refund', `${cfg.types[1][1]} draft created`, { reason: window.prompt('Reason for credit/debit note?') || '' })}>Create {cfg.types[1][1].toLowerCase()}</button>}
        {d && d.state !== 'cancelled' && can(`${cfg.p}.cancel`) && (d.state !== 'posted' || num(d.amount_residual) === num(d.amount_total)) && <button className="danger" onClick={() => { const r = window.prompt('Reason for cancelling (audit trail):'); if (r) act('cancel', 'Cancelled', { reason: r }) }}>Cancel</button>}
        {d?.state === 'posted' && num(d.amount_residual) > 0 && can('billing.payment.post') && <button onClick={() => setPay(true)}>Register payment</button>}
        {editable && can(`${cfg.p}.create`) && <button disabled={busy} onClick={save}>Save draft</button>}
        {(editable || d?.state === 'approved') && can(`${cfg.p}.post`) && <button className="primary" disabled={busy || !lines.length} onClick={doPost}>Post</button>}
      </>}>
      {d?.state === 'to_approve' && <div className="alert warn">Waiting for approval — see the Approvals inbox. It can be posted once approved.</div>}
      {d?.state === 'approved' && <div className="alert info">Approved. Ready to post.</div>}
      {d?.origin_number && <div className="alert info">{isRefund ? cfg.types[1][1] : 'Document'} against {d.origin_number}</div>}
      <div className="grid form">
        {!d && <Field label="Type"><select value={h.move_type} onChange={(e) => setH({ ...h, move_type: e.target.value })}>{cfg.types.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>}
        <Field label={kind === 'invoices' ? 'Customer' : 'Vendor'}><PartnerSelect kind={cfg.partner} value={h.partner} onChange={(v) => setH({ ...h, partner: v })} disabled={!editable} /></Field>
        <Field label={kind === 'invoices' ? 'Invoice date' : 'Bill date'}><input type="date" value={h.date} onChange={(e) => setH({ ...h, date: e.target.value })} disabled={!editable} /></Field>
        <Field label="Payment terms"><SimpleSelect path="/payment-terms/" labelOf={(t) => t.name} value={h.payment_term} onChange={(v) => setH({ ...h, payment_term: v })} disabled={!editable} /></Field>
        <Field label="Due date" hint="Empty = from payment terms"><input type="date" value={h.due_date || ''} onChange={(e) => setH({ ...h, due_date: e.target.value })} disabled={!editable} /></Field>
        <Field label="Currency"><CurrencySelect value={h.currency} onChange={(v) => setH({ ...h, currency: v })} disabled={!editable} /></Field>
        <Field label={kind === 'bills' ? "Vendor's bill number" : 'Customer reference / PO'}><input value={h.reference} onChange={(e) => setH({ ...h, reference: e.target.value })} disabled={!editable} /></Field>
      </div>
      <div className="card">
        <div className="table-wrap"><table className="data lines">
          <thead><tr><th style={{ width: '17%' }}>Product</th><th>Description</th><th style={{ width: '17%' }}>Account</th><th className="num" style={{ width: 80 }}>Qty</th><th className="num" style={{ width: 110 }}>Unit price</th><th className="num" style={{ width: 70 }}>Disc %</th><th style={{ width: 130 }}>Tax</th><th style={{ width: 110 }}>Cost center</th><th className="num">Subtotal</th><th /></tr></thead>
          <tbody>{lines.map((l, i) => (
            <tr key={i}>
              <td><ProductSelect value={l.product} onChange={(v) => pickProduct(i, v)} disabled={!editable || !!l.purchase_line || !!l.sales_line} /></td>
              <td><input value={l.description} onChange={(e) => setLine(i, { description: e.target.value })} disabled={!editable} /></td>
              <td><AccountSelect value={l.account} onChange={(v) => setLine(i, { account: v })} disabled={!editable || !!l.purchase_line} filter={(a) => !a.reconcilable} /></td>
              <td><input className="num" value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} disabled={!editable} /></td>
              <td><input className="num" value={l.unit_price} onChange={(e) => setLine(i, { unit_price: e.target.value })} disabled={!editable} /></td>
              <td><input className="num" value={l.discount_pct} onChange={(e) => setLine(i, { discount_pct: e.target.value })} disabled={!editable} /></td>
              <td><TaxSelect scope={cfg.scope} value={l.tax} onChange={(v) => setLine(i, { tax: v })} disabled={!editable} /></td>
              <td><CostCenterSelect value={l.cost_center} onChange={(v) => setLine(i, { cost_center: v })} disabled={!editable} /></td>
              <td className="num">{money(calc(l).sub)}</td>
              <td>{editable && <button className="sm ghost" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button>}</td>
            </tr>))}
          </tbody>
        </table></div>
        <div className="balance-bar">
          {editable && <button className="sm" style={{ marginRight: 'auto' }} onClick={() => setLines([...lines, { product: null, description: '', account: null, quantity: '1', unit_price: '0', discount_pct: '0', tax: null, cost_center: null }])}>+ Add line</button>}
          <span>Untaxed <b>{money(totals.sub)}</b></span><span>Tax <b>{money(totals.tax)}</b></span><span>Total <b>{money(totals.sub + totals.tax, h.currency)}</b></span>
          {d?.state === 'posted' && <span className="bad">Due {money(d.amount_residual, d.currency)}</span>}
        </div>
      </div>
      <Field label="Notes"><textarea rows={2} value={h.notes} onChange={(e) => setH({ ...h, notes: e.target.value })} disabled={!editable} /></Field>
      {d?.allocations?.length > 0 && <div className="card"><div className="card-head"><h3>Payments & credits applied</h3></div>
        <DataTable rows={d.allocations} cols={[
          { key: 'date', title: 'Date', render: (a) => fmtDate(a.date) },
          { key: 'src', title: 'Source', render: (a) => a.payment_number || a.credit_note_number || a.invoice_number },
          { key: 'fx', title: 'FX difference', render: (a) => a.fx_entry_number || '—' },
          { key: 'amount', title: 'Amount', num: true, render: (a) => money(a.amount) }]} /></div>}
      {d?.state === 'posted' && h.currency !== company?.base_currency && <div className="muted">Booked at rate {num(d.rate)} → {money(d.amount_total_base, company?.base_currency)}</div>}
      {showEntry && <EntryById id={d.journal_entry} onClose={() => setShowEntry(false)} />}
      {pay && <RegisterPayment base={cfg.base} doc={d} onClose={() => setPay(false)} onDone={(inv: any) => { setPay(false); setD(inv); onChanged() }} />}
    </Modal>
  )
}

function RegisterPayment({ base, doc, onClose, onDone }: any) {
  const [f, setF] = useState({ journal: null as number | null, amount: String(num(doc.amount_residual)), date: today(), reference: '' })
  const { run, busy } = useAction()
  const go = async () => { const r = await run(() => post(`${base}${doc.id}/register-payment/`, f), 'Payment posted and applied'); if (r) onDone(r.invoice) }
  return (
    <Modal title={`Register payment — ${doc.number}`} onClose={onClose} footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={busy || !f.journal} onClick={go}>Post payment</button></>}>
      <Field label="Bank / cash journal"><JournalSelect types={['bank', 'cash']} value={f.journal} onChange={(v) => setF({ ...f, journal: v })} /></Field>
      <div className="grid form">
        <Field label={`Amount (${doc.currency})`}><input className="num" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        <Field label="Payment date"><input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
      </div>
      <Field label="Bank reference / cheque no."><input value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
      <div className="muted">Foreign-currency differences are posted automatically to realised FX gain/loss.</div>
    </Modal>
  )
}
