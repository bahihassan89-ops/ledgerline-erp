import { useMemo, useState } from 'react'
import { CurrencySelect, PartnerSelect, ProductSelect, SimpleSelect, TaxSelect, WarehouseSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, StateBadge, useAction } from '../components/ui'
import { del, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, money, num, qty, today } from '../lib/format'
import { useFetch, useOptions } from '../lib/hooks'
import { DocEditor } from './Documents'

type Kind = 'sales' | 'purchase'
const CFG = {
  sales: { title: 'Sales Orders', base: '/sales-orders/', p: 'sales.order', partner: 'customer' as const, scope: 'sale' as const, doc: 'quotation', move: 'deliver', moveLabel: 'Deliver', done: 'qty_delivered', inv: 'create-invoice', invLabel: 'Create invoice', invPerm: 'billing.invoice.create', invKind: 'invoices' as const, ref: 'customer_reference', price: 'sale_price', tax: 'sale_tax' },
  purchase: { title: 'Purchase Orders', base: '/purchase-orders/', p: 'purchasing.order', partner: 'vendor' as const, scope: 'purchase' as const, doc: 'RFQ', move: 'receive', moveLabel: 'Receive goods', done: 'qty_received', inv: 'create-bill', invLabel: 'Create vendor bill', invPerm: 'billing.bill.create', invKind: 'bills' as const, ref: 'vendor_reference', price: 'purchase_price', tax: 'purchase_tax' },
}

export default function Orders({ kind }: { kind: Kind }) {
  const cfg = CFG[kind]
  const { can } = useAuth()
  const [f, setF] = useState({ search: '', state: '' })
  const { data, reload } = useFetch<any>(cfg.base, { ...f, page_size: 100 })
  const [open, setOpen] = useState<any>(null)
  return (
    <>
      <PageHead title={cfg.title} sub={kind === 'sales' ? 'Quotation → confirmed order (credit check & approval) → delivery → invoice.' : 'RFQ → approved order → goods receipt → vendor bill (3-way match).'}
        actions={can(`${cfg.p}.create`) && <button className="primary" onClick={() => setOpen({})}>+ New {cfg.doc}</button>} />
      <div className="card">
        <div className="toolbar">
          <input placeholder="Search…" value={f.search} onChange={(e) => setF({ ...f, search: e.target.value })} />
          <select value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })}><option value="">All states</option>{['draft', 'to_approve', 'approved', 'confirmed', 'done', 'cancelled'].map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}</select>
        </div>
        <DataTable rows={data?.results} onRow={setOpen} cols={[
          { key: 'number', title: 'Number', render: (r) => <span className="mono">{r.number || `${cfg.doc} #${r.id}`}</span> },
          { key: 'date', title: 'Date', render: (r) => fmtDate(r.date) },
          { key: 'partner_name', title: kind === 'sales' ? 'Customer' : 'Vendor' },
          { key: 'progress', title: kind === 'sales' ? 'Delivered / invoiced' : 'Received / billed', render: (r) => {
            const q = r.lines.reduce((s: number, l: any) => s + num(l.quantity), 0)
            const m = r.lines.reduce((s: number, l: any) => s + num(l[cfg.done]), 0)
            const b = r.lines.reduce((s: number, l: any) => s + num(kind === 'sales' ? l.qty_invoiced : l.qty_billed), 0)
            return <span className="muted">{q ? Math.round((m / q) * 100) : 0}% / {q ? Math.round((b / q) * 100) : 0}%</span>
          } },
          { key: 'amount_total', title: 'Total', num: true, render: (r) => money(r.amount_total, r.currency) },
          { key: 'state', title: 'State', render: (r) => <StateBadge state={r.state === 'draft' ? 'draft' : r.state} /> },
        ]} />
      </div>
      {open && <OrderEditor kind={kind} order={open.id ? open : null} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  )
}

type L = { id?: number; product: number | null; description: string; quantity: string; unit_price: string; discount_pct: string; tax: number | null; [k: string]: any }

function OrderEditor({ kind, order, onClose, onChanged }: { kind: Kind; order: any; onClose: () => void; onChanged: () => void }) {
  const cfg = CFG[kind]
  const { can, company } = useAuth()
  const [o, setO] = useState<any>(order)
  const editable = !o || o.state === 'draft'
  const warehouses = useOptions<any>('/warehouses/')
  const [h, setH] = useState<any>(() => ({ partner: o?.partner ?? null, date: o?.date ?? today(), currency: o?.currency ?? company?.base_currency, warehouse: o?.warehouse ?? null, payment_term: o?.payment_term ?? null, [cfg.ref]: o?.[cfg.ref] ?? '', notes: o?.notes ?? '' }))
  const [lines, setLines] = useState<L[]>(() => o ? o.lines.map((l: any) => ({ ...l, quantity: String(num(l.quantity)), unit_price: String(num(l.unit_price)), discount_pct: String(num(l.discount_pct)) })) : [])
  const products = useOptions<any>('/products/', { active: true })
  const taxes = useOptions<any>('/taxes/')
  const pById = useMemo(() => Object.fromEntries(products.map((p) => [p.id, p])), [products])
  const tById = useMemo(() => Object.fromEntries(taxes.map((t) => [t.id, t])), [taxes])
  const [move, setMove] = useState(false)
  const [invoice, setInvoice] = useState<any>(null)
  const { run, busy } = useAction()
  const wh = h.warehouse ?? warehouses[0]?.id
  const calc = (l: L) => { const g = num(l.quantity) * num(l.unit_price) * (1 - num(l.discount_pct) / 100); const t = l.tax ? tById[l.tax] : null; const r = t ? num(t.rate) / 100 : 0; return t?.price_include ? { sub: g / (1 + r), tax: g - g / (1 + r) } : { sub: g, tax: g * r } }
  const tot = lines.reduce((s, l) => { const c = calc(l); return { sub: s.sub + c.sub, tax: s.tax + c.tax } }, { sub: 0, tax: 0 })
  const setLine = (i: number, p: Partial<L>) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...p } : l)))
  const pick = (i: number, id: number | null) => { const p = id ? pById[id] : null; setLine(i, p ? { product: id, description: p.name, unit_price: String(num(p[cfg.price])), tax: p[cfg.tax] ?? null } : { product: null }) }
  const body = () => ({ ...h, warehouse: wh, lines: lines.map((l) => ({ product: l.product, description: l.description, quantity: l.quantity || '0', unit_price: l.unit_price || '0', discount_pct: l.discount_pct || '0', tax: l.tax })) })
  const save = async () => { const r = await run(() => (o ? put(`${cfg.base}${o.id}/`, body()) : post(cfg.base, body())), 'Saved'); if (r) { setO(r); onChanged() } return r }
  const confirm = async () => { const s = editable ? await save() : o; if (!s) return; const r = await run(() => post(`${cfg.base}${s.id}/confirm/`)); if (r) { setO(r); onChanged() } }
  const makeInvoice = async () => { const r = await run(() => post(`${cfg.base}${o.id}/${cfg.inv}/`, {}), 'Draft created'); if (r) setInvoice(r) }
  const outstandingMove = o?.lines?.some((l: any) => num(l.quantity) > num(l[cfg.done]))
  return (
    <Modal wide onClose={onClose} title={<>{o?.number || (o ? `${cfg.doc} #${o.id}` : `New ${cfg.doc}`)} {o && <StateBadge state={o.state} />}</>}
      footer={<>
        {o && editable && can(`${cfg.p}.create`) && <button className="danger" onClick={async () => { if (window.confirm('Delete?') && await run(() => del(`${cfg.base}${o.id}/`)) !== undefined) { onChanged(); onClose() } }}>Delete</button>}
        {o && ['draft', 'approved', 'confirmed', 'to_approve'].includes(o.state) && can(`${cfg.p}.cancel`) && <button className="danger" onClick={async () => { const r = await run(() => post(`${cfg.base}${o.id}/cancel/`, {}), 'Cancelled'); if (r) { setO(r); onChanged() } }}>Cancel order</button>}
        <span style={{ flex: 1 }} />
        {o?.state === 'confirmed' && outstandingMove && can('inventory.stock.move') && <button onClick={() => setMove(true)}>{cfg.moveLabel}</button>}
        {o?.state === 'confirmed' && can(cfg.invPerm) && <button onClick={makeInvoice}>{cfg.invLabel}</button>}
        {editable && can(`${cfg.p}.create`) && <button disabled={busy} onClick={save}>Save</button>}
        {(editable || o?.state === 'approved') && can(`${cfg.p}.confirm`) && <button className="primary" disabled={busy || !lines.length} onClick={confirm}>Confirm order</button>}
      </>}>
      {o?.state === 'to_approve' && <div className="alert warn">Above an approval threshold — waiting in the Approvals inbox.</div>}
      <div className="grid form">
        <Field label={kind === 'sales' ? 'Customer' : 'Vendor'}><PartnerSelect kind={cfg.partner} value={h.partner} onChange={(v) => setH({ ...h, partner: v })} disabled={!editable} /></Field>
        <Field label="Order date"><input type="date" value={h.date} onChange={(e) => setH({ ...h, date: e.target.value })} disabled={!editable} /></Field>
        <Field label="Warehouse"><WarehouseSelect value={wh} onChange={(v) => setH({ ...h, warehouse: v })} disabled={!editable} /></Field>
        <Field label="Currency"><CurrencySelect value={h.currency} onChange={(v) => setH({ ...h, currency: v })} disabled={!editable} /></Field>
        <Field label="Payment terms"><SimpleSelect path="/payment-terms/" labelOf={(t) => t.name} value={h.payment_term} onChange={(v) => setH({ ...h, payment_term: v })} disabled={!editable} /></Field>
        <Field label={kind === 'sales' ? 'Customer PO / reference' : 'Vendor quote reference'}><input value={h[cfg.ref]} onChange={(e) => setH({ ...h, [cfg.ref]: e.target.value })} disabled={!editable} /></Field>
      </div>
      <div className="card">
        <div className="table-wrap"><table className="data lines">
          <thead><tr><th style={{ width: '22%' }}>Product</th><th>Description</th><th className="num" style={{ width: 90 }}>Qty</th><th className="num" style={{ width: 120 }}>Unit price</th><th className="num" style={{ width: 70 }}>Disc %</th><th style={{ width: 150 }}>Tax</th><th className="num">Subtotal</th>{o && o.state !== 'draft' && <><th className="num">{kind === 'sales' ? 'Delivered' : 'Received'}</th><th className="num">{kind === 'sales' ? 'Invoiced' : 'Billed'}</th></>}<th /></tr></thead>
          <tbody>{lines.map((l, i) => (
            <tr key={i}>
              <td><ProductSelect value={l.product} onChange={(v) => pick(i, v)} disabled={!editable} /></td>
              <td><input value={l.description} onChange={(e) => setLine(i, { description: e.target.value })} disabled={!editable} /></td>
              <td><input className="num" value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} disabled={!editable} /></td>
              <td><input className="num" value={l.unit_price} onChange={(e) => setLine(i, { unit_price: e.target.value })} disabled={!editable} /></td>
              <td><input className="num" value={l.discount_pct} onChange={(e) => setLine(i, { discount_pct: e.target.value })} disabled={!editable} /></td>
              <td><TaxSelect scope={cfg.scope} value={l.tax} onChange={(v) => setLine(i, { tax: v })} disabled={!editable} /></td>
              <td className="num">{money(calc(l).sub)}</td>
              {o && o.state !== 'draft' && <><td className="num">{qty(l[cfg.done])}</td><td className="num">{qty(kind === 'sales' ? l.qty_invoiced : l.qty_billed)}</td></>}
              <td>{editable && <button className="sm ghost" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button>}</td>
            </tr>))}</tbody>
        </table></div>
        <div className="balance-bar">
          {editable && <button className="sm" style={{ marginRight: 'auto' }} onClick={() => setLines([...lines, { product: null, description: '', quantity: '1', unit_price: '0', discount_pct: '0', tax: null }])}>+ Add line</button>}
          <span>Untaxed <b>{money(tot.sub)}</b></span><span>Tax <b>{money(tot.tax)}</b></span><span>Total <b>{money(tot.sub + tot.tax, h.currency)}</b></span>
        </div>
      </div>
      {move && <MoveDialog kind={kind} order={o} onClose={() => setMove(false)} onDone={(no: any) => { setMove(false); setO(no); setLines(no.lines.map((l: any) => ({ ...l, quantity: String(num(l.quantity)), unit_price: String(num(l.unit_price)), discount_pct: String(num(l.discount_pct)) }))); onChanged() }} />}
      {invoice && <DocEditor kind={cfg.invKind} doc={invoice} onClose={() => setInvoice(null)} onChanged={onChanged} />}
    </Modal>
  )
}

function MoveDialog({ kind, order, onClose, onDone }: any) {
  const cfg = CFG[kind as Kind]
  const [q, setQ] = useState<Record<number, string>>(() => Object.fromEntries(order.lines.map((l: any) => [l.id, String(num(l.quantity) - num(l[cfg.done]))])))
  const [date, setDate] = useState(today())
  const { run, busy } = useAction()
  const go = async () => { const r = await run(() => post(`${cfg.base}${order.id}/${cfg.move}/`, { quantities: q, date }), kind === 'sales' ? 'Delivered — cost of goods sold posted' : 'Received — inventory and GRNI posted'); if (r) onDone(r.order) }
  return (
    <Modal title={cfg.moveLabel} onClose={onClose} footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={busy} onClick={go}>Validate</button></>}>
      <Field label="Date"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
      <table className="data"><thead><tr><th>Product</th><th className="num">Outstanding</th><th className="num">Quantity now</th></tr></thead>
        <tbody>{order.lines.map((l: any) => <tr key={l.id}><td>{l.product_sku} {l.description}</td><td className="num">{qty(num(l.quantity) - num(l[cfg.done]))}</td><td className="num"><input className="num" style={{ width: 100 }} value={q[l.id] ?? ''} onChange={(e) => setQ({ ...q, [l.id]: e.target.value })} /></td></tr>)}</tbody></table>
    </Modal>
  )
}
