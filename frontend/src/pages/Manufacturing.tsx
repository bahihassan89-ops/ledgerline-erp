import { useState } from 'react'
import { CostCenterSelect, ProductSelect, SimpleSelect, WarehouseSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, Tabs, useAction } from '../components/ui'
import { del, patch, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, label, money, num, qty, today } from '../lib/format'
import { invalidateOptions, useFetch, useOptions } from '../lib/hooks'

const TONE: Record<string, string> = { draft: '', confirmed: 'info', in_progress: 'warn', done: 'ok', cancelled: 'bad' }

export default function Manufacturing() {
  const { can } = useAuth()
  const [tab, setTab] = useState('orders')
  const mos = useFetch<any>('/manufacturing-orders/', { page_size: 200 })
  const boms = useFetch<any[]>('/boms/')
  const wcs = useFetch<any[]>('/work-centers/')
  const wip = useFetch<any>('/manufacturing-orders/wip/')
  const [mo, setMo] = useState<any>(null)
  const [bom, setBom] = useState<any>(null)
  const [wc, setWc] = useState<any>(null)
  const reload = () => { mos.reload(); boms.reload(); wcs.reload(); wip.reload(); invalidateOptions() }
  return (
    <>
      <PageHead title="Production — الإنتاج" sub="Actual absorption costing: components at FIFO/average cost + labour and overhead at work-center rates → work in progress → finished goods."
        actions={can('manufacturing.order.manage') && tab === 'orders' ? <button className="primary" onClick={() => setMo({})}>+ Manufacturing order</button>
          : can('manufacturing.bom.manage') && tab === 'boms' ? <button className="primary" onClick={() => setBom({})}>+ Bill of materials</button>
            : can('manufacturing.bom.manage') && tab === 'wc' ? <button className="primary" onClick={() => setWc({})}>+ Work center</button> : null} />
      <Tabs tabs={[['orders', 'Manufacturing orders'], ['boms', 'Bills of materials'], ['wc', 'Work centers'], ['wip', 'Work in progress']]} value={tab} onChange={setTab} />
      {tab === 'orders' && <div className="card"><DataTable rows={mos.data?.results} onRow={setMo} empty="No manufacturing orders." cols={[
        { key: 'number', title: 'Number', render: (r) => <span className="mono">{r.number || `Draft #${r.id}`}</span> }, { key: 'product_name', title: 'Product' },
        { key: 'quantity', title: 'Qty', num: true, render: (r) => `${qty(r.produced_qty)} / ${qty(r.quantity)}` }, { key: 'planned_date', title: 'Planned', render: (r) => fmtDate(r.planned_date) },
        { key: 'cost', title: 'Actual cost', num: true, render: (r) => money(num(r.material_cost) + num(r.labour_cost) + num(r.overhead_cost)) },
        { key: 'wip_balance', title: 'In WIP', num: true, render: (r) => money(r.wip_balance) },
        { key: 'state', title: 'State', render: (r) => <span className={`badge ${TONE[r.state]}`}>{label(r.state)}</span> }]} /></div>}
      {tab === 'boms' && <div className="card"><DataTable rows={boms.data} onRow={setBom} cols={[
        { key: 'code', title: 'BOM' }, { key: 'product_name', title: 'Product' }, { key: 'quantity', title: 'Output qty', num: true, render: (r) => qty(r.quantity) },
        { key: 'components', title: 'Components', num: true, render: (r) => r.components.length }, { key: 'operations', title: 'Operations', num: true, render: (r) => r.operations.length },
        { key: 'cost', title: 'Planned unit cost', num: true, render: (r) => money(r.planned_unit_cost.total) }, { key: 'active', title: '', render: (r) => r.active ? '' : <span className="badge">inactive</span> }]} /></div>}
      {tab === 'wc' && <div className="card"><DataTable rows={wcs.data} onRow={setWc} cols={[{ key: 'code', title: 'Code' }, { key: 'name', title: 'Name' },
        { key: 'labour_rate', title: 'Labour / hour', num: true, render: (r) => money(r.labour_rate) }, { key: 'overhead_rate', title: 'Overhead / hour', num: true, render: (r) => money(r.overhead_rate) }]} /></div>}
      {tab === 'wip' && wip.data && <div className="card">
        <div className="toolbar"><span className={`badge ${wip.data.reconciled ? 'ok' : 'bad'}`}>{wip.data.reconciled ? '✓ WIP agrees with GL' : `✗ GL shows ${money(wip.data.general_ledger)}`}</span><span style={{ flex: 1 }} />Total WIP <b>{money(wip.data.total)}</b></div>
        <DataTable rows={wip.data.rows} empty="No open orders." cols={[{ key: 'number', title: 'Order' }, { key: 'product', title: 'Product' }, { key: 'quantity', title: 'Qty', num: true }, { key: 'produced', title: 'Produced', num: true }, { key: 'wip', title: 'WIP', num: true, render: (r) => money(r.wip) }]} />
      </div>}
      {mo && <MoEditor initial={mo} onClose={() => { setMo(null); reload() }} />}
      {bom && <BomEditor initial={bom} onClose={() => { setBom(null); reload() }} />}
      {wc && <WcEditor initial={wc} onClose={() => { setWc(null); reload() }} />}
    </>
  )
}

function WcEditor({ initial, onClose }: any) {
  const [f, setF] = useState<any>({ code: '', name: '', labour_rate: '0', overhead_rate: '0', cost_center: null, active: true, ...initial })
  const { run, busy } = useAction()
  const save = async () => { const { id, ...b } = f; if (await run(() => (id ? patch(`/work-centers/${id}/`, b) : post('/work-centers/', b)), 'Saved')) onClose() }
  return (
    <Modal title={f.id ? f.name : 'New work center'} onClose={onClose} footer={<button className="primary" disabled={busy} onClick={save}>Save</button>}>
      <div className="grid form"><Field label="Code"><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field><Field label="Name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Direct labour cost per hour"><input className="num" value={f.labour_rate} onChange={(e) => setF({ ...f, labour_rate: e.target.value })} /></Field>
        <Field label="Overhead absorbed per hour" hint="Budgeted factory overhead ÷ budgeted hours"><input className="num" value={f.overhead_rate} onChange={(e) => setF({ ...f, overhead_rate: e.target.value })} /></Field>
        <Field label="Cost center"><CostCenterSelect value={f.cost_center} onChange={(v) => setF({ ...f, cost_center: v })} /></Field></div>
    </Modal>
  )
}

function BomEditor({ initial, onClose }: any) {
  const [f, setF] = useState<any>({ code: '', product: null, quantity: '1', active: true, notes: '', components: [], operations: [], ...initial })
  const wcs = useOptions<any>('/work-centers/')
  const { run, busy } = useAction()
  const save = async () => {
    const { id, product_name, planned_unit_cost, ...b } = f; void product_name; void planned_unit_cost
    b.components = b.components.map(({ product_sku, ...c }: any) => { void product_sku; return c })
    if (await run(() => (id ? put(`/boms/${id}/`, b) : post('/boms/', b)), 'Saved')) onClose()
  }
  const setC = (i: number, p: any) => setF({ ...f, components: f.components.map((c: any, j: number) => (j === i ? { ...c, ...p } : c)) })
  const setO = (i: number, p: any) => setF({ ...f, operations: f.operations.map((c: any, j: number) => (j === i ? { ...c, ...p } : c)) })
  return (
    <Modal wide title={f.id ? `BOM ${f.code}` : 'New bill of materials'} onClose={onClose} footer={<button className="primary" disabled={busy} onClick={save}>Save</button>}>
      <div className="grid form"><Field label="BOM code"><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="Finished product"><ProductSelect value={f.product} onChange={(v) => setF({ ...f, product: v })} /></Field>
        <Field label="Output quantity"><input className="num" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} /></Field></div>
      {f.planned_unit_cost && <div className="muted">Planned unit cost: material {money(f.planned_unit_cost.material)} + conversion {money(f.planned_unit_cost.conversion)} = <b>{money(f.planned_unit_cost.total)}</b></div>}
      <div className="card"><div className="card-head"><h3>Components</h3><span style={{ flex: 1 }} /><button className="sm" onClick={() => setF({ ...f, components: [...f.components, { product: null, quantity: '1', scrap_pct: '0' }] })}>+ Component</button></div>
        <table className="data lines"><thead><tr><th>Component</th><th className="num">Qty per output</th><th className="num">Expected loss %</th><th /></tr></thead>
          <tbody>{f.components.map((c: any, i: number) => <tr key={i}><td><ProductSelect value={c.product} onChange={(v) => setC(i, { product: v })} /></td><td><input className="num" value={c.quantity} onChange={(e) => setC(i, { quantity: e.target.value })} /></td><td><input className="num" value={c.scrap_pct} onChange={(e) => setC(i, { scrap_pct: e.target.value })} /></td><td><button className="sm ghost" onClick={() => setF({ ...f, components: f.components.filter((_: any, j: number) => j !== i) })}>✕</button></td></tr>)}</tbody></table></div>
      <div className="card"><div className="card-head"><h3>Operations</h3><span style={{ flex: 1 }} /><button className="sm" onClick={() => setF({ ...f, operations: [...f.operations, { sequence: (f.operations.length + 1) * 10, name: '', work_center: wcs[0]?.id, minutes: '0' }] })}>+ Operation</button></div>
        <table className="data lines"><thead><tr><th>Operation</th><th>Work center</th><th className="num">Minutes per output</th><th /></tr></thead>
          <tbody>{f.operations.map((o: any, i: number) => <tr key={i}><td><input value={o.name} onChange={(e) => setO(i, { name: e.target.value })} /></td>
            <td><select value={o.work_center ?? ''} onChange={(e) => setO(i, { work_center: Number(e.target.value) })}>{wcs.map((w) => <option key={w.id} value={w.id}>{w.code} {w.name}</option>)}</select></td>
            <td><input className="num" value={o.minutes} onChange={(e) => setO(i, { minutes: e.target.value })} /></td><td><button className="sm ghost" onClick={() => setF({ ...f, operations: f.operations.filter((_: any, j: number) => j !== i) })}>✕</button></td></tr>)}</tbody></table></div>
    </Modal>
  )
}

function MoEditor({ initial, onClose }: any) {
  const { can } = useAuth()
  const [m, setM] = useState<any>(initial.id ? initial : { product: null, bom: null, quantity: '1', warehouse: null, planned_date: today(), notes: '' })
  const [dlg, setDlg] = useState<'consume' | 'ops' | 'produce' | null>(null)
  const costs = useFetch<any>(m.id && m.state !== 'draft' ? `/manufacturing-orders/${m.id}/costs/` : null, undefined, [m.state, m.wip_balance])
  const { run, busy } = useAction()
  const draft = !m.id || m.state === 'draft'
  const open = ['confirmed', 'in_progress'].includes(m.state)
  const act = async (path: string, body?: any, msg?: string) => { const r = await run(() => post(`/manufacturing-orders/${m.id}/${path}/`, body), msg); if (r) { setM(r); setDlg(null) } }
  const save = async () => { const { id, components, operations, product_name, ...b } = m; void components; void operations; void product_name; const r = await run(() => (id ? patch(`/manufacturing-orders/${id}/`, b) : post('/manufacturing-orders/', b)), 'Saved'); if (r) setM(r); return r }
  return (
    <Modal wide onClose={onClose} title={<>{m.number || 'New manufacturing order'} {m.state && <span className={`badge ${TONE[m.state]}`}>{label(m.state)}</span>}</>}
      footer={can('manufacturing.order.manage') && <>{m.id && draft && <button className="danger" onClick={async () => { if (await run(() => del(`/manufacturing-orders/${m.id}/`)) !== undefined) onClose() }}>Delete</button>}
        {open && !num(m.material_cost) && !num(m.labour_cost) && <button className="danger" onClick={() => { if (window.confirm('Cancel this manufacturing order?')) act('cancel', {}, 'Cancelled') }}>Cancel</button>}<span style={{ flex: 1 }} />
        {draft && <><button disabled={busy} onClick={save}>Save</button><button className="primary" disabled={busy} onClick={async () => { const s = await save(); if (s) { const r = await run(() => post(`/manufacturing-orders/${s.id}/confirm/`), 'Confirmed'); if (r) setM(r) } }}>Confirm</button></>}
        {open && <><button onClick={() => setDlg('consume')}>Issue components</button><button onClick={() => setDlg('ops')}>Record labour hours</button><button className="primary" onClick={() => setDlg('produce')}>Produce</button></>}</>}>
      <div className="grid form">
        <Field label="Product"><ProductSelect value={m.product} onChange={(v) => setM({ ...m, product: v, bom: null })} disabled={!draft} /></Field>
        <Field label="Bill of materials"><SimpleSelect allowEmpty={false} path="/boms/" labelOf={(b) => `${b.code} — ${b.product_name}`} value={m.bom} onChange={(v) => setM({ ...m, bom: v })} disabled={!draft} /></Field>
        <Field label="Quantity to produce"><input className="num" value={m.quantity} onChange={(e) => setM({ ...m, quantity: e.target.value })} disabled={!draft} /></Field>
        <Field label="Warehouse"><WarehouseSelect value={m.warehouse} onChange={(v) => setM({ ...m, warehouse: v })} disabled={!draft} /></Field>
        <Field label="Planned date"><input type="date" value={m.planned_date} onChange={(e) => setM({ ...m, planned_date: e.target.value })} disabled={!draft} /></Field>
      </div>
      {m.components?.length > 0 && <div className="grid two">
        <div className="card"><div className="card-head"><h3>Components</h3></div><DataTable rows={m.components} cols={[{ key: 'product_sku', title: 'Item' }, { key: 'planned_qty', title: 'Planned', num: true, render: (c) => qty(c.planned_qty) }, { key: 'consumed_qty', title: 'Issued', num: true, render: (c) => qty(c.consumed_qty) }, { key: 'consumed_value', title: 'Cost', num: true, render: (c) => money(c.consumed_value) }]} /></div>
        <div className="card"><div className="card-head"><h3>Operations</h3></div><DataTable rows={m.operations} cols={[{ key: 'name', title: 'Operation' }, { key: 'work_center_code', title: 'WC' }, { key: 'planned_minutes', title: 'Plan min', num: true, render: (o) => num(o.planned_minutes) }, { key: 'actual_minutes', title: 'Actual min', num: true, render: (o) => num(o.actual_minutes) }, { key: 'c', title: 'Cost', num: true, render: (o) => money(num(o.labour_cost) + num(o.overhead_cost)) }]} /></div>
      </div>}
      {costs.data && <div className="card card-body"><div className="grid kpi">
        <div><div className="kpi-label">Planned cost</div><div className="kpi-value">{money(costs.data.planned.total)}</div></div>
        <div><div className="kpi-label">Actual cost</div><div className="kpi-value">{money(costs.data.actual.total)}</div><div className="kpi-foot">Material {money(costs.data.actual.material)} · Labour {money(costs.data.actual.labour)} · Overhead {money(costs.data.actual.overhead)}</div></div>
        <div><div className="kpi-label">Finished goods value</div><div className="kpi-value">{money(costs.data.output_value)}</div><div className="kpi-foot">Unit cost {costs.data.actual_unit_cost ? money(costs.data.actual_unit_cost) : '—'} · Scrap {money(costs.data.scrap_value)}</div></div>
        <div><div className="kpi-label">Still in WIP</div><div className="kpi-value">{money(costs.data.wip_balance)}</div>{costs.data.variance && <div className="kpi-foot">Variance vs plan {money(costs.data.variance)}</div>}</div>
      </div></div>}
      {dlg === 'consume' && <QtyDialog title="Issue components to WIP" rows={m.components.map((c: any) => ({ id: c.id, label: `${c.product_sku} ${c.product_name}`, value: String(Math.max(0, num(c.planned_qty) - num(c.consumed_qty))) }))} onClose={() => setDlg(null)} onOk={(v: any, date: string) => act('consume', { quantities: v, date }, 'Components issued')} />}
      {dlg === 'ops' && <QtyDialog title="Record actual minutes worked" rows={m.operations.map((o: any) => ({ id: o.id, label: `${o.name} (${o.work_center_code})`, value: String(Math.max(0, num(o.planned_minutes) - num(o.actual_minutes))) }))} onClose={() => setDlg(null)} onOk={(v: any, date: string) => act('record-operations', { minutes: v, date }, 'Labour & overhead absorbed')} />}
      {dlg === 'produce' && <ProduceDialog mo={m} onClose={() => setDlg(null)} onOk={(b: any) => act('produce', b, 'Finished goods received')} />}
    </Modal>
  )
}

function QtyDialog({ title, rows, onClose, onOk }: any) {
  const [v, setV] = useState<Record<number, string>>(Object.fromEntries(rows.map((r: any) => [r.id, r.value])))
  const [date, setDate] = useState(today())
  return (
    <Modal title={title} onClose={onClose} footer={<button className="primary" onClick={() => onOk(v, date)}>Post</button>}>
      <Field label="Date"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
      <table className="data"><tbody>{rows.map((r: any) => <tr key={r.id}><td>{r.label}</td><td className="num"><input className="num" style={{ width: 120 }} value={v[r.id]} onChange={(e) => setV({ ...v, [r.id]: e.target.value })} /></td></tr>)}</tbody></table>
    </Modal>
  )
}

function ProduceDialog({ mo, onClose, onOk }: any) {
  const left = num(mo.quantity) - num(mo.produced_qty) - num(mo.scrapped_qty)
  const [f, setF] = useState<any>({ quantity: String(left), scrap_quantity: '0', finish: true, date: today() })
  return (
    <Modal title="Produce finished goods" onClose={onClose} footer={<button className="primary" onClick={() => onOk(f)}>Post</button>}>
      <div className="grid form"><Field label="Good quantity"><input className="num" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} /></Field>
        <Field label="Scrapped quantity"><input className="num" value={f.scrap_quantity} onChange={(e) => setF({ ...f, scrap_quantity: e.target.value })} /></Field>
        <Field label="Date"><input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field></div>
      <label className="check"><input type="checkbox" checked={f.finish} onChange={(e) => setF({ ...f, finish: e.target.checked })} />Finish the order (all remaining WIP goes into these goods / scrap)</label>
      <div className="muted">WIP now: {money(mo.wip_balance)}. Finished goods are valued at actual cost; scrap is expensed to “Production scrap and variances”.</div>
    </Modal>
  )
}
