import { useSearchParams } from 'react-router-dom'
import { AccountSelect } from '../components/pickers'
import { Export, PageHead, Tabs } from '../components/ui'
import { useAuth } from '../lib/auth'
import { fmtDate, label, money, num, today, yearStart } from '../lib/format'
import { useFetch } from '../lib/hooks'

const REPORTS: [string, string][] = [
  ['trial-balance', 'Trial Balance'], ['balance-sheet', 'Balance Sheet'], ['income-statement', 'Income Statement'],
  ['cash-flow', 'Cash Flow'], ['general-ledger', 'General Ledger'], ['ar-aging', 'AR Aging'], ['ap-aging', 'AP Aging'],
  ['vat-return', 'VAT Return'], ['withholding', 'WHT Form 41'], ['withholding-customers', 'WHT by Customers'],
  ['subledger-reconciliation', 'Reconciliation'], ['inventory-valuation', 'Inventory Valuation'],
  ['liquidity', 'Liquidity'], ['ecl-receivables', 'ECL Receivables'], ['petty-cash', 'Petty Cash'],
]

export default function Reports() {
  const [sp, setSp] = useSearchParams()
  const r = sp.get('r') || 'trial-balance'
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); setSp(n) }
  const p = { date_from: sp.get('date_from') || yearStart(), date_to: sp.get('date_to') || today(), as_of: sp.get('as_of') || today(), level: sp.get('level') || '', account: sp.get('account') || '', horizon_days: sp.get('horizon_days') || '30' }
  const pointInTime = ['balance-sheet', 'ar-aging', 'ap-aging', 'subledger-reconciliation', 'liquidity', 'ecl-receivables', 'petty-cash'].includes(r)
  const query: any = pointInTime ? { as_of: p.as_of } : { date_from: p.date_from, date_to: p.date_to }
  if (['trial-balance', 'balance-sheet', 'income-statement'].includes(r) && p.level) query.level = p.level
  if (r === 'general-ledger') query.account = p.account
  if (r === 'liquidity') query.horizon_days = p.horizon_days
  const compare = sp.get('compare') === '1'
  if (compare && r === 'income-statement') {
    const shift = (d: string) => `${Number(d.slice(0, 4)) - 1}${d.slice(4)}`
    query.compare_from = shift(p.date_from); query.compare_to = shift(p.date_to)
  }
  if (compare && r === 'balance-sheet') query.compare_as_of = `${Number(p.as_of.slice(0, 4)) - 1}${p.as_of.slice(4)}`
  const ready = r !== 'general-ledger' || !!p.account
  const { data, loading, error } = useFetch<any>(ready ? `/reports/${r}/` : null, query)
  const { company } = useAuth()
  return (
    <>
      <PageHead title="Financial Reports" sub={`${company?.name} · amounts in ${company?.base_currency} · posted entries only`} actions={<button onClick={() => window.print()}>Print</button>} />
      <Tabs tabs={REPORTS} value={r} onChange={(v) => { const n = new URLSearchParams(sp); n.set('r', v); setSp(n) }} />
      <div className="card">
        <div className="toolbar">
          {pointInTime ? <label className="row">As of <input type="date" value={p.as_of} onChange={(e) => set('as_of', e.target.value)} /></label>
            : r !== 'inventory-valuation' && <><label className="row">From <input type="date" value={p.date_from} onChange={(e) => set('date_from', e.target.value)} /></label>
              <label className="row">To <input type="date" value={p.date_to} onChange={(e) => set('date_to', e.target.value)} /></label></>}
          {['trial-balance', 'balance-sheet', 'income-statement'].includes(r) && <select value={p.level} onChange={(e) => set('level', e.target.value)}>
            <option value="">All levels</option>{[1, 2, 3, 4].map((l) => <option key={l} value={l}>Up to level {l}</option>)}</select>}
          {['balance-sheet', 'income-statement'].includes(r) && <label className="check"><input type="checkbox" checked={compare} onChange={(e) => set('compare', e.target.checked ? '1' : '')} />Compare with prior year</label>}
          {r === 'general-ledger' && <div style={{ minWidth: 320 }}><AccountSelect value={p.account ? Number(p.account) : null} onChange={(v) => set('account', v ? String(v) : '')} /></div>}
          {r === 'liquidity' && <label className="row">Horizon <input className="num" style={{ width: 70 }} type="number" value={p.horizon_days} onChange={(e) => set('horizon_days', e.target.value)} /> days</label>}
        </div>
        <div className="card-body">
          {error && <div className="alert bad">{error}</div>}
          {!ready ? <div className="empty">Choose an account.</div> : loading && !data ? <div className="empty">Loading…</div> : data && <Report r={r} d={data} onDrill={(id: number) => { const n = new URLSearchParams(sp); n.set('r', 'general-ledger'); n.set('account', String(id)); setSp(n) }} />}
        </div>
      </div>
    </>
  )
}

function CashFlowSection({ title, s, first }: any) {
  return <><tr className="group"><td>{title}</td><td /></tr>{first}{s.rows.map((x: any, i: number) => <tr key={i}><td style={{ paddingLeft: 28 }}>{x.label}</td><td className="num">{money(x.amount)}</td></tr>)}<tr className="lvl1"><td>Net cash from {title.toLowerCase()}</td><td className="num">{money(s.total)}</td></tr></>
}

function Report({ r, d, onDrill }: { r: string; d: any; onDrill: (id: number) => void }) {
  const M = (v: any) => money(v)
  if (r === 'trial-balance') {
    const cols = [{ key: 'code', title: 'Code' }, { key: 'name', title: 'Account' }, { key: 'opening', title: 'Opening' }, { key: 'debit', title: 'Debit' }, { key: 'credit', title: 'Credit' }, { key: 'closing', title: 'Closing' }]
    return <>
      <div className="row" style={{ marginBottom: 10 }}><span className={`badge ${d.balanced ? 'ok' : 'bad'}`}>{d.balanced ? '✓ Debits equal credits' : '✗ OUT OF BALANCE'}</span><span style={{ flex: 1 }} /><Export name="trial-balance" rows={d.rows} cols={cols} /></div>
      <table className="data"><thead><tr><th>Account</th><th className="num">Opening</th><th className="num">Debit</th><th className="num">Credit</th><th className="num">Closing</th></tr></thead>
        <tbody>{d.rows.map((x: any) => <tr key={x.account_id} className={`${x.level === 1 ? 'lvl1' : x.is_group ? 'group' : 'clickable'}`} onClick={() => !x.is_group && onDrill(x.account_id)}>
          <td style={{ paddingLeft: 12 + (x.level - 1) * 18 }}><span className="mono">{x.code}</span> {x.name}</td>
          <td className="num">{M(x.opening)}</td><td className="num">{M(x.debit)}</td><td className="num">{M(x.credit)}</td><td className="num">{M(x.closing)}</td></tr>)}</tbody>
        <tfoot><tr><td>Total</td><td className="num">{M(d.totals.opening)}</td><td className="num">{M(d.totals.debit)}</td><td className="num">{M(d.totals.credit)}</td><td className="num">{M(d.totals.closing)}</td></tr></tfoot></table>
      <p className="muted">Balances: positive = debit, (brackets) = credit.</p>
    </>
  }
  if (r === 'balance-sheet' || r === 'income-statement') {
    const isBS = r === 'balance-sheet'
    const flatCols = [{ key: 'section', title: 'Section' }, { key: 'code', title: 'Code' }, { key: 'name', title: 'Account' }, { key: 'amount', title: 'Amount' }, ...(d.previous ? [{ key: 'previous', title: 'Prior year' }] : [])]
    const flatRows = d.sections.flatMap((s: any) => s.rows.map((x: any) => ({ section: s.label, code: x.code, name: x.name, amount: x.amount, previous: x.previous })))
    return <div className="stack">
      <div className="row">{isBS ? <span className={`badge ${d.balanced ? 'ok' : 'bad'}`}>{d.balanced ? '✓ Assets = Liabilities + Equity' : '✗ Not balanced'}</span> : <span className="muted">{fmtDate(d.date_from)} – {fmtDate(d.date_to)}</span>}<span style={{ flex: 1 }} /><Export name={r} rows={flatRows} cols={flatCols} /></div>
      {d.sections.map((s: any) => <div key={s.class}>
        <table className="data"><thead><tr><th>{s.label}</th><th className="num">Amount</th>{d.previous && <th className="num">Prior year</th>}</tr></thead>
          <tbody>{s.rows.map((x: any) => <tr key={x.account_id} className={x.level === 1 ? 'lvl1' : x.is_group ? 'group' : 'clickable'} onClick={() => !x.is_group && onDrill(x.account_id)}>
            <td style={{ paddingLeft: 12 + (x.level - 1) * 18 }}><span className="mono">{x.code}</span> {x.name}</td><td className="num">{M(x.amount)}</td>{d.previous && <td className="num muted">{M(x.previous)}</td>}</tr>)}
            {isBS && s.class === 'equity' && <tr><td style={{ paddingLeft: 30 }}><i>Current year result (not yet closed)</i></td><td className="num">{M(d.current_year_result)}</td></tr>}
          </tbody>
          <tfoot><tr><td>Total {s.label}</td><td className="num">{M(isBS && s.class === 'equity' ? d.total_equity : s.total)}</td></tr></tfoot></table></div>)}
      {isBS ? <table className="data"><tfoot><tr><td>Total assets</td><td className="num">{M(d.total_assets)}</td></tr><tr><td>Total liabilities + equity</td><td className="num">{M(d.total_liabilities_and_equity)}</td></tr></tfoot></table>
        : <table className="data"><tbody><tr><td>Revenue</td><td className="num">{M(d.revenue)}</td></tr><tr><td>Cost of sales</td><td className="num">({M(d.cost_of_sales)})</td></tr></tbody>
          <tfoot><tr><td>Gross profit</td><td className="num">{M(d.gross_profit)}</td></tr><tr><td>Net profit / (loss)</td><td className="num" style={{ color: num(d.net_profit) < 0 ? 'var(--bad)' : 'var(--ok)' }}>{M(d.net_profit)}</td></tr></tfoot></table>}
    </div>
  }
  if (r === 'general-ledger') {
    const cols = ['date', 'number', 'journal', 'partner', 'description', 'debit', 'credit', 'balance'].map((k) => ({ key: k, title: label(k) }))
    return <>
      <div className="row" style={{ marginBottom: 10 }}><h3>{d.account.code} {d.account.name}</h3><span style={{ flex: 1 }} />Opening <b>{M(d.opening_balance)}</b> · Closing <b>{M(d.closing_balance)}</b><Export name={`gl-${d.account.code}`} rows={d.rows} cols={cols} /></div>
      <table className="data"><thead><tr><th>Date</th><th>Entry</th><th>Jrnl</th><th>Partner</th><th>Description</th><th className="num">Debit</th><th className="num">Credit</th><th className="num">Balance</th></tr></thead>
        <tbody><tr className="group"><td colSpan={7}>Opening balance</td><td className="num">{M(d.opening_balance)}</td></tr>
          {d.rows.map((x: any, i: number) => <tr key={i}><td>{fmtDate(x.date)}</td><td className="mono">{x.number}</td><td>{x.journal}</td><td>{x.partner}</td><td>{x.description}</td>
            <td className="num">{num(x.debit) ? M(x.debit) : ''}</td><td className="num">{num(x.credit) ? M(x.credit) : ''}</td><td className="num">{M(x.balance)}</td></tr>)}</tbody></table>
    </>
  }
  if (r === 'ar-aging' || r === 'ap-aging') {
    const B = { current: 'Not due', '1_30': '1–30', '31_60': '31–60', '61_90': '61–90', over_90: '90+' } as Record<string, string>
    const cols = [{ key: 'code', title: 'Code' }, { key: 'partner', title: 'Partner' }, ...d.buckets.map((b: string) => ({ key: b, title: B[b] })), { key: 'total', title: 'Total' }]
    return <>
      <div className="row" style={{ marginBottom: 10 }}><span className="muted">Days past due date, as of {fmtDate(d.as_of)}. Unapplied payments reduce the total.</span><span style={{ flex: 1 }} /><Export name={r} rows={d.rows} cols={cols} /></div>
      <table className="data"><thead><tr><th>Partner</th>{d.buckets.map((b: string) => <th key={b} className="num">{B[b]}</th>)}<th className="num">Unapplied</th><th className="num">Total</th></tr></thead>
        <tbody>{d.rows.map((x: any) => <tr key={x.partner_id}><td>{x.code} {x.partner}</td>{d.buckets.map((b: string) => <td key={b} className="num" style={{ color: b !== 'current' && num(x[b]) > 0 ? 'var(--bad)' : undefined }}>{num(x[b]) ? M(x[b]) : '—'}</td>)}<td className="num">{x.unallocated ? `(${M(x.unallocated)})` : '—'}</td><td className="num"><b>{M(x.total)}</b></td></tr>)}</tbody>
        <tfoot><tr><td>Total</td>{d.buckets.map((b: string) => <td key={b} className="num">{M(d.totals[b])}</td>)}<td className="num">{M(d.totals.unallocated)}</td><td className="num">{M(d.totals.total)}</td></tr></tfoot></table>
    </>
  }
  if (r === 'cash-flow') {
    const cfCols = [{ key: 'section', title: 'Section' }, { key: 'label', title: 'Line' }, { key: 'amount', title: 'Amount' }]
    const cfRows = [
      { section: 'Operating activities', label: 'Net profit / (loss) for the period', amount: d.net_profit },
      ...([['Operating activities', d.operating], ['Investing activities', d.investing], ['Financing activities', d.financing]] as const)
        .flatMap(([section, s]) => s.rows.map((x: any) => ({ section, label: x.label, amount: x.amount }))),
      { section: 'Totals', label: 'Net change in cash', amount: d.net_change },
      { section: 'Totals', label: 'Cash at beginning of period', amount: d.opening_cash },
      { section: 'Totals', label: 'Cash at end of period', amount: d.closing_cash },
    ]
    return <>
      <div className="row" style={{ marginBottom: 10 }}><span className={`badge ${d.reconciled ? 'ok' : 'bad'}`}>{d.reconciled ? '✓ Reconciles to the actual change in cash' : `✗ Actual change ${M(d.actual_cash_change)}`}</span><span className="muted">Indirect method (EAS 4)</span><span style={{ flex: 1 }} /><Export name="cash-flow" rows={cfRows} cols={cfCols} /></div>
      <table className="data"><tbody>
        <CashFlowSection title="Operating activities" s={d.operating} first={<tr><td style={{ paddingLeft: 28 }}>Net profit / (loss) for the period</td><td className="num">{M(d.net_profit)}</td></tr>} />
        <CashFlowSection title="Investing activities" s={d.investing} /><CashFlowSection title="Financing activities" s={d.financing} />
      </tbody><tfoot><tr><td>Net change in cash</td><td className="num">{M(d.net_change)}</td></tr><tr><td>Cash at beginning of period</td><td className="num">{M(d.opening_cash)}</td></tr><tr><td>Cash at end of period</td><td className="num">{M(d.closing_cash)}</td></tr></tfoot></table>
    </>
  }
  if (r === 'vat-return') {
    const cols = [{ key: 'scope', title: 'Scope' }, { key: 'name', title: 'Tax code' }, { key: 'eta', title: 'ETA' }, { key: 'rate', title: 'Rate' }, { key: 'base', title: 'Taxable value' }, { key: 'tax', title: 'VAT' }]
    return <>
      <div className="row" style={{ marginBottom: 10 }}><span style={{ flex: 1 }} /><Export name="vat-return" rows={d.rows} cols={cols} /></div>
      <table className="data"><thead><tr><th>Scope</th><th>Tax code</th><th>ETA</th><th className="num">Rate</th><th className="num">Taxable value</th><th className="num">VAT</th></tr></thead>
        <tbody>{d.rows.map((x: any) => <tr key={x.code}><td>{x.scope === 'sale' ? 'Sales (output)' : 'Purchases (input)'}</td><td>{x.name}</td><td className="mono">{x.eta}</td><td className="num">{num(x.rate)}%</td><td className="num">{M(x.base)}</td><td className="num">{M(x.tax)}</td></tr>)}</tbody>
        <tfoot><tr><td colSpan={5}>Output VAT</td><td className="num">{M(d.output_vat)}</td></tr><tr><td colSpan={5}>Input VAT</td><td className="num">({M(d.input_vat)})</td></tr><tr><td colSpan={5}>Net VAT {d.position}</td><td className="num">{M(d.net_vat)}</td></tr></tfoot></table>
      <p className="muted">{d.note}</p>
    </>
  }
  if (r === 'withholding' || r === 'withholding-customers') {
    const cols = ['partner', 'tax_number', 'national_id', 'tax', 'eta_subtype', 'rate', 'base', 'withheld'].map((k) => ({ key: k, title: label(k) }))
    return <>
      <div className="row" style={{ marginBottom: 10 }}><span className={`badge ${num(d.total_withheld) === num(d.gl_movement) ? 'ok' : 'bad'}`}>{num(d.total_withheld) === num(d.gl_movement) ? '✓ Agrees with the GL account' : `✗ GL movement ${M(d.gl_movement)}`}</span><span style={{ flex: 1 }} /><Export name={r} rows={d.rows} cols={cols} /></div>
      <table className="data"><thead><tr><th>{r === 'withholding' ? 'Vendor' : 'Customer'}</th><th>Tax reg. no.</th><th>Type</th><th>ETA code</th><th className="num">Rate</th><th className="num">Base</th><th className="num">Withheld</th><th>Documents</th></tr></thead>
        <tbody>{d.rows.map((x: any, i: number) => <tr key={i}><td>{x.partner}</td><td className="mono">{x.tax_number || x.national_id}</td><td>{x.tax}</td><td className="mono">{x.eta_subtype}</td><td className="num">{num(x.rate)}%</td><td className="num">{M(x.base)}</td><td className="num">{M(x.withheld)}</td><td className="muted">{x.documents.join(', ')}</td></tr>)}</tbody>
        <tfoot><tr><td colSpan={6}>Total withheld</td><td className="num">{M(d.total_withheld)}</td><td /></tr></tfoot></table>
      <p className="muted">{d.note}</p>
    </>
  }
  if (r === 'vat') {
    const cols = [{ key: 'name', title: 'Tax code' }, { key: 'scope', title: 'Scope' }, { key: 'rate', title: 'Rate' }, { key: 'taxable_base', title: 'Taxable base' }, { key: 'tax_amount', title: 'Tax' }]
    return <>
      <div className="row" style={{ marginBottom: 10 }}><span style={{ flex: 1 }} /><Export name="vat" rows={d.rows} cols={cols} /></div>
      <table className="data"><thead><tr><th>Tax code</th><th>Scope</th><th className="num">Rate</th><th className="num">Taxable base</th><th className="num">Tax</th></tr></thead>
        <tbody>{d.rows.map((x: any) => <tr key={x.tax}><td>{x.name}</td><td>{label(x.scope)}</td><td className="num">{num(x.rate)}%</td><td className="num">{M(x.taxable_base)}</td><td className="num">{M(x.tax_amount)}</td></tr>)}</tbody>
        <tfoot><tr><td colSpan={4}>Output VAT (sales)</td><td className="num">{M(d.output_tax)}</td></tr><tr><td colSpan={4}>Input VAT (purchases)</td><td className="num">({M(d.input_tax)})</td></tr><tr><td colSpan={4}>Net VAT payable / (refundable)</td><td className="num">{M(d.net_payable)}</td></tr></tfoot></table>
    </>
  }
  if (r === 'subledger-reconciliation') {
    const cols = [{ key: 'type', title: 'Type' }, { key: 'subledger', title: 'Sub-ledger' }, { key: 'general_ledger', title: 'General ledger' }, { key: 'difference', title: 'Difference' }]
    const rows = (['receivable', 'payable'] as const).map((k) => ({ type: k === 'receivable' ? 'Accounts receivable' : 'Accounts payable', subledger: d[k].subledger, general_ledger: d[k].general_ledger, difference: d[k].difference }))
    return <div className="stack">
      <div className="row"><span style={{ flex: 1 }} /><Export name="subledger-reconciliation" rows={rows} cols={cols} /></div>
      <div className="grid two">{(['receivable', 'payable'] as const).map((k) => <div key={k} className="card card-body stack">
        <h2>{k === 'receivable' ? 'Accounts receivable' : 'Accounts payable'} <span className={`badge ${d[k].reconciled ? 'ok' : 'bad'}`}>{d[k].reconciled ? '✓ Reconciled' : '✗ Difference'}</span></h2>
        <dl className="kv"><dt>Sub-ledger (open documents)</dt><dd className="num">{M(d[k].subledger)}</dd><dt>General ledger control</dt><dd className="num">{M(d[k].general_ledger)}</dd><dt>Difference</dt><dd className="num">{M(d[k].difference)}</dd></dl></div>)}</div>
    </div>
  }
  if (r === 'inventory-valuation') {
    const ok = num(d.total_value) === num(d.general_ledger_value)
    const cols = ['sku', 'name', 'category', 'cost_method', 'quantity', 'average_cost', 'value'].map((k) => ({ key: k, title: label(k) }))
    return <>
      <div className="row" style={{ marginBottom: 10 }}><span className={`badge ${ok ? 'ok' : 'bad'}`}>{ok ? '✓ Stock valuation equals GL inventory accounts' : `✗ GL shows ${M(d.general_ledger_value)}`}</span><span style={{ flex: 1 }} /><Export name="inventory-valuation" rows={d.rows} cols={cols} /></div>
      <table className="data"><thead><tr><th>SKU</th><th>Product</th><th>Category</th><th>Method</th><th className="num">Qty</th><th className="num">Avg cost</th><th className="num">Value</th></tr></thead>
        <tbody>{d.rows.map((x: any) => <tr key={x.product_id}><td className="mono">{x.sku}</td><td>{x.name}</td><td>{x.category}</td><td>{x.cost_method.toUpperCase()}</td><td className="num">{num(x.quantity)}</td><td className="num">{money(x.average_cost, undefined, 4)}</td><td className="num">{M(x.value)}</td></tr>)}</tbody>
        <tfoot><tr><td colSpan={6}>Total</td><td className="num">{M(d.total_value)}</td></tr></tfoot></table>
    </>
  }
  if (r === 'liquidity') {
    const cols = ['label', 'amount'].map((k) => ({ key: k, title: label(k) }))
    const rows = [
      { label: 'Cash & bank (GL)', amount: d.cash_and_bank },
      { label: 'Receivables due soon', amount: d.receivables_due_soon }, { label: 'Receivables overdue', amount: d.receivables_overdue },
      { label: 'Payables due soon', amount: d.payables_due_soon }, { label: 'Payables overdue', amount: d.payables_overdue },
      { label: `Projected cash by ${fmtDate(d.horizon_date)}`, amount: d.projected_cash_base_currency },
    ]
    const chequeRows = (dir: 'receivable' | 'payable') => Object.entries(d[`cheques_${dir}_by_currency`] as Record<string, string>)
    return <div className="stack">
      <div className="row" style={{ marginBottom: 10 }}><span className="muted">As of {fmtDate(d.as_of)} · next {d.horizon_days} days (to {fmtDate(d.horizon_date)})</span><span style={{ flex: 1 }} /><Export name="liquidity" rows={rows} cols={cols} /></div>
      <table className="data"><tbody>
        <tr className="group"><td colSpan={2}>Cash position</td></tr>
        <tr><td style={{ paddingLeft: 28 }}>Cash & bank (GL balance, today)</td><td className="num">{M(d.cash_and_bank)}</td></tr>
        <tr className="group"><td colSpan={2}>Receivables ({M(d.receivables_total)} total open)</td></tr>
        <tr><td style={{ paddingLeft: 28 }}>Due within the horizon</td><td className="num">{M(d.receivables_due_soon)}</td></tr>
        <tr><td style={{ paddingLeft: 28 }}>Already overdue</td><td className="num" style={{ color: 'var(--bad)' }}>{M(d.receivables_overdue)}</td></tr>
        <tr className="group"><td colSpan={2}>Payables ({M(d.payables_total)} total open)</td></tr>
        <tr><td style={{ paddingLeft: 28 }}>Due within the horizon</td><td className="num">({M(d.payables_due_soon)})</td></tr>
        <tr><td style={{ paddingLeft: 28 }}>Already overdue</td><td className="num" style={{ color: 'var(--bad)' }}>({M(d.payables_overdue)})</td></tr>
      </tbody><tfoot><tr><td>Projected cash by {fmtDate(d.horizon_date)}</td><td className="num"><b>{M(d.projected_cash_base_currency)}</b></td></tr></tfoot></table>
      <div className="grid two">
        <div className="card card-body stack"><h3>Cheques receivable (in hand / deposited, due within horizon)</h3>
          {chequeRows('receivable').length ? chequeRows('receivable').map(([cur, amt]) => <div key={cur} className="row"><span>{cur}</span><span style={{ flex: 1 }} /><b>{money(amt)}</b></div>) : <span className="muted">None</span>}</div>
        <div className="card card-body stack"><h3>Cheques payable (issued, due within horizon)</h3>
          {chequeRows('payable').length ? chequeRows('payable').map(([cur, amt]) => <div key={cur} className="row"><span>{cur}</span><span style={{ flex: 1 }} /><b>{money(amt)}</b></div>) : <span className="muted">None</span>}</div>
      </div>
      <p className="muted">Cheque amounts are shown per their own currency and are not converted into the base-currency figures above -- treat them as an indicative guide, not a number to add onto the cash position.</p>
    </div>
  }
  if (r === 'petty-cash') {
    const cols = ['code', 'name', 'account', 'float_amount', 'balance', 'topup_amount'].map((k) => ({ key: k, title: label(k) }))
    return <>
      <div className="row" style={{ marginBottom: 10 }}><span className="muted">Imprest / petty-cash funds as of {fmtDate(d.as_of)}</span><span style={{ flex: 1 }} /><Export name="petty-cash" rows={d.rows} cols={cols} /></div>
      <table className="data"><thead><tr><th>Journal</th><th>Account</th><th className="num">Float</th><th className="num">Balance</th><th>Status</th><th className="num">Top-up due</th></tr></thead>
        <tbody>{d.rows.map((x: any) => <tr key={x.journal_id}><td>{x.code} {x.name}</td><td className="mono">{x.account}</td><td className="num">{M(x.float_amount)}</td><td className="num">{M(x.balance)}</td>
          <td>{x.due_for_topup ? <span className="badge bad">Due for top-up</span> : <span className="badge ok">OK</span>}</td><td className="num">{x.due_for_topup ? M(x.topup_amount) : '—'}</td></tr>)}</tbody></table>
      {!d.rows.length && <p className="muted">No journals are marked as imprest / petty-cash funds. Mark one in Settings → Journals.</p>}
    </>
  }
  if (r === 'ecl-receivables') {
    const B = { current: 'Not due', '1_30': '1–30', '31_60': '31–60', '61_90': '61–90', over_90: '90+' } as Record<string, string>
    const buckets = Object.keys(d.rates)
    const cols = [{ key: 'code', title: 'Code' }, { key: 'partner', title: 'Partner' }, ...buckets.map((b) => ({ key: b, title: B[b] })), { key: 'total_balance', title: 'Total balance' }, { key: 'total_provision', title: 'Provision' }]
    const rows = d.rows.map((x: any) => ({ code: x.code, partner: x.partner, ...Object.fromEntries(buckets.map((b) => [b, x.buckets[b].provision])), total_balance: x.total_balance, total_provision: x.total_provision }))
    return <>
      <div className="row" style={{ marginBottom: 10 }}><span className="muted">As of {fmtDate(d.as_of)} · rates: {buckets.map((b) => `${B[b]} ${num(d.rates[b])}%`).join(' · ')}</span><span style={{ flex: 1 }} /><Export name="ecl-receivables" rows={rows} cols={cols} /></div>
      <table className="data"><thead><tr><th>Partner</th>{buckets.map((b) => <th key={b} className="num">{B[b]} provision</th>)}<th className="num">Balance</th><th className="num">Provision</th></tr></thead>
        <tbody>{d.rows.map((x: any) => <tr key={x.partner_id}><td>{x.code} {x.partner}</td>{buckets.map((b) => <td key={b} className="num">{num(x.buckets[b].provision) ? M(x.buckets[b].provision) : '—'}</td>)}<td className="num">{M(x.total_balance)}</td><td className="num"><b>{M(x.total_provision)}</b></td></tr>)}</tbody>
        <tfoot><tr><td>Total</td>{buckets.map((b) => <td key={b} />)}<td className="num">{M(d.total_balance)}</td><td className="num">{M(d.total_provision)}</td></tr></tfoot></table>
      <p className="muted">Provision = each AR aging bucket × its ECL rate (Settings → Default accounts). Rates default to 0%, so an all-zero provision means no rates have been set yet.</p>
    </>
  }
  return null
}
