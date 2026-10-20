import { useState } from 'react'
import { AccountSelect, ProductSelect, SimpleSelect, TaxSelect, WarehouseSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, Tabs, useAction } from '../components/ui'
import { patch, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, label, money, num, qty, today } from '../lib/format'
import { invalidateOptions, useFetch } from '../lib/hooks'

export function Products() {
  const { can } = useAuth()
  const [search, setSearch] = useState('')
  const { data, reload } = useFetch<any>('/products/', { search, page_size: 200 })
  const [edit, setEdit] = useState<any>(null)
  return (
    <>
      <PageHead title="Products" sub="Stockable products are tracked and valued (FIFO or weighted average, set on the category)." actions={can('inventory.product.manage') && <button className="primary" onClick={() => setEdit({})}>+ New product</button>} />
      <div className="card">
        <div className="toolbar"><input placeholder="Search SKU, name, barcode…" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
        <DataTable rows={data?.results} onRow={setEdit} cols={[
          { key: 'sku', title: 'SKU', render: (r) => <span className="mono">{r.sku}</span> }, { key: 'name', title: 'Name' },
          { key: 'type', title: 'Type', render: (r) => label(r.type) }, { key: 'category_name', title: 'Category' },
          { key: 'sale_price', title: 'Sale price', num: true, render: (r) => money(r.sale_price) },
          { key: 'qty_on_hand', title: 'On hand', num: true, render: (r) => r.type === 'stock' ? <>{qty(r.qty_on_hand)} {num(r.qty_on_hand) <= num(r.reorder_level) && num(r.reorder_level) > 0 && <span className="badge warn">Reorder</span>}</> : '—' },
          { key: 'average_cost', title: 'Unit cost', num: true, render: (r) => r.type === 'stock' ? money(r.average_cost, undefined, 4) : '—' },
        ]} />
      </div>
      {edit && <ProductForm initial={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); invalidateOptions(); reload() }} />}
    </>
  )
}

function ProductForm({ initial, onClose, onSaved }: any) {
  const [f, setF] = useState<any>({ sku: '', name: '', barcode: '', type: 'stock', category: null, uom: 'Unit', sale_price: '0', purchase_price: '0', sale_tax: null, purchase_tax: null, income_account: null, expense_account: null, reorder_level: '0', active: true, ...initial })
  const { run, busy } = useAction()
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const save = async () => { const { id, qty_on_hand, average_cost, category_name, ...b } = f; void qty_on_hand; void average_cost; void category_name; const r = await run(() => (id ? patch(`/products/${id}/`, b) : post('/products/', b)), 'Saved'); if (r) onSaved() }
  return (
    <Modal title={f.id ? f.name : 'New product'} onClose={onClose} footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={busy} onClick={save}>Save</button></>}>
      <div className="grid form">
        <Field label="SKU"><input className="mono" value={f.sku} onChange={(e) => set('sku', e.target.value)} /></Field>
        <Field label="Barcode"><input value={f.barcode} onChange={(e) => set('barcode', e.target.value)} /></Field>
      </div>
      <Field label="Name"><input value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
      <div className="grid form">
        <Field label="Type"><select value={f.type} onChange={(e) => set('type', e.target.value)}><option value="stock">Stockable</option><option value="consumable">Consumable</option><option value="service">Service</option></select></Field>
        <Field label="Category" hint="Sets costing method and GL accounts"><SimpleSelect allowEmpty={false} path="/product-categories/" labelOf={(c) => `${c.name} (${c.cost_method})`} value={f.category} onChange={(v) => set('category', v)} /></Field>
        <Field label="Unit of measure"><input value={f.uom} onChange={(e) => set('uom', e.target.value)} /></Field>
        <Field label="Sale price"><input className="num" value={f.sale_price} onChange={(e) => set('sale_price', e.target.value)} /></Field>
        <Field label="Purchase price"><input className="num" value={f.purchase_price} onChange={(e) => set('purchase_price', e.target.value)} /></Field>
        <Field label="Reorder level"><input className="num" value={f.reorder_level} onChange={(e) => set('reorder_level', e.target.value)} /></Field>
        <Field label="Sales tax"><TaxSelect scope="sale" value={f.sale_tax} onChange={(v) => set('sale_tax', v)} /></Field>
        <Field label="Purchase tax"><TaxSelect scope="purchase" value={f.purchase_tax} onChange={(v) => set('purchase_tax', v)} /></Field>
        <Field label="Income account (override)"><AccountSelect allowEmpty value={f.income_account} onChange={(v) => set('income_account', v)} filter={(a) => a.account_class === 'income'} /></Field>
        <Field label="Expense account (override)"><AccountSelect allowEmpty value={f.expense_account} onChange={(v) => set('expense_account', v)} filter={(a) => a.account_class === 'expense'} /></Field>
      </div>
      <label className="check"><input type="checkbox" checked={f.active} onChange={(e) => set('active', e.target.checked)} />Active</label>
    </Modal>
  )
}

export function Stock() {
  const { can } = useAuth()
  const [tab, setTab] = useState('levels')
  const levels = useFetch<any>('/stock-levels/', { page_size: 500 })
  const moves = useFetch<any>('/stock-moves/', { page_size: 200 })
  const [op, setOp] = useState<string | null>(null)
  const reload = () => { levels.reload(); moves.reload() }
  return (
    <>
      <PageHead title="Stock & Moves" sub="Every movement posts to the general ledger at cost."
        actions={<>{can('inventory.stock.adjust') && <><button onClick={() => setOp('opening')}>Opening stock</button><button onClick={() => setOp('adjust')}>Count adjustment</button></>}{can('inventory.stock.move') && <button onClick={() => setOp('transfer')}>Transfer</button>}</>} />
      <Tabs tabs={[['levels', 'Stock on hand'], ['moves', 'Movements']]} value={tab} onChange={setTab} />
      <div className="card">
        {tab === 'levels' ? <DataTable rows={levels.data?.results} cols={[{ key: 'product_sku', title: 'SKU' }, { key: 'product_name', title: 'Product' }, { key: 'warehouse_code', title: 'Warehouse' }, { key: 'quantity', title: 'On hand', num: true, render: (r) => qty(r.quantity) }]} />
          : <DataTable rows={moves.data?.results} cols={[
            { key: 'date', title: 'Date', render: (r) => fmtDate(r.date) }, { key: 'number', title: 'Number', render: (r) => <span className="mono">{r.number}</span> },
            { key: 'move_type', title: 'Type', render: (r) => label(r.move_type) }, { key: 'product_sku', title: 'Product' }, { key: 'warehouse_code', title: 'WH' },
            { key: 'quantity', title: 'Qty', num: true, render: (r) => qty(r.quantity) }, { key: 'unit_cost', title: 'Unit cost', num: true, render: (r) => money(r.unit_cost, undefined, 4) },
            { key: 'value', title: 'Value', num: true, render: (r) => money(r.value) }, { key: 'reference', title: 'Reference' }, { key: 'entry_number', title: 'GL entry', render: (r) => <span className="mono">{r.entry_number || '—'}</span> }]} />}
      </div>
      {op && <StockOp op={op} onClose={() => setOp(null)} onDone={() => { setOp(null); reload() }} />}
    </>
  )
}

function StockOp({ op, onClose, onDone }: any) {
  const [f, setF] = useState<any>({ product: null, warehouse: null, date: today(), quantity: '', counted_qty: '', unit_cost: '', to_warehouse: null, counter_account: null, reason: '' })
  const { run, busy } = useAction()
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const go = async () => {
    const b = Object.fromEntries(Object.entries(f).filter(([, v]) => v !== '' && v !== null))
    if (await run(() => post(`/stock-moves/${op}/`, b), 'Done')) onDone()
  }
  const title = { opening: 'Opening stock', adjust: 'Inventory count adjustment', transfer: 'Warehouse transfer' }[op as string]
  return (
    <Modal title={title} onClose={onClose} footer={<><button onClick={onClose}>Cancel</button><button className="primary" disabled={busy} onClick={go}>Validate</button></>}>
      <Field label="Product"><ProductSelect value={f.product} onChange={(v) => set('product', v)} /></Field>
      <div className="grid form">
        <Field label={op === 'transfer' ? 'From warehouse' : 'Warehouse'}><WarehouseSelect value={f.warehouse} onChange={(v) => set('warehouse', v)} /></Field>
        {op === 'transfer' && <Field label="To warehouse"><WarehouseSelect value={f.to_warehouse} onChange={(v) => set('to_warehouse', v)} /></Field>}
        <Field label="Date"><input type="date" value={f.date} onChange={(e) => set('date', e.target.value)} /></Field>
        {op === 'adjust' ? <Field label="Counted quantity" hint="The system posts the difference"><input className="num" value={f.counted_qty} onChange={(e) => set('counted_qty', e.target.value)} /></Field>
          : <Field label="Quantity"><input className="num" value={f.quantity} onChange={(e) => set('quantity', e.target.value)} /></Field>}
        {op !== 'transfer' && <Field label="Unit cost" hint={op === 'adjust' ? 'Gains only; empty = current average' : undefined}><input className="num" value={f.unit_cost} onChange={(e) => set('unit_cost', e.target.value)} /></Field>}
      </div>
      {op === 'opening' && <Field label="Offset account" hint="Usually Opening Balance Equity"><AccountSelect value={f.counter_account} onChange={(v) => set('counter_account', v)} filter={(a) => a.account_class === 'equity'} /></Field>}
      <Field label="Reason / reference"><input value={f.reason} onChange={(e) => set('reason', e.target.value)} /></Field>
    </Modal>
  )
}
