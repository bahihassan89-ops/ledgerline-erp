import { useState } from 'react'
import { Export, PageHead, Tabs } from '../components/ui'
import { useAuth } from '../lib/auth'
import { money, num, today, yearStart } from '../lib/format'
import { useFetch } from '../lib/hooks'

// Cross-company reports for central roles (CEO, Central Financial Controller, Central
// Accounting Manager). See apps.ledger.views.GlobalReportView and
// apps.ledger.reports.intercompany_reconciliation / consolidated_trial_balance on the backend --
// both are deliberately non-eliminating (same documented policy as branch_income_statement):
// nothing here posts or removes an intercompany balance automatically, it's a reconciliation
// and reporting aid only.
export default function GroupReports() {
  const { canGlobal } = useAuth()
  const [tab, setTab] = useState(canGlobal('report.intercompany') ? 'intercompany-reconciliation' : 'consolidated')
  const tabs: [string, string][] = [
    ...(canGlobal('report.intercompany') ? [['intercompany-reconciliation', 'Intercompany Reconciliation'] as [string, string]] : []),
    ...(canGlobal('report.consolidated') ? [['consolidated', 'Consolidated'] as [string, string]] : []),
  ]
  return (
    <>
      <PageHead title="Group Reports" sub="Across every active company in the install." />
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === 'intercompany-reconciliation' && <IntercompanyReconciliation />}
      {tab === 'consolidated' && <Consolidated />}
    </>
  )
}

function M(v: any) { return money(v) }

function IntercompanyReconciliation() {
  const [asOf, setAsOf] = useState(today())
  const { data, loading, error } = useFetch<any>('/global-reports/intercompany-reconciliation/', { as_of: asOf })
  const cols = [{ key: 'company', title: 'Company' }, { key: 'partner', title: 'Partner' }, { key: 'linked_company', title: 'Linked company' },
    { key: 'our_balance', title: 'Our balance' }, { key: 'their_balance', title: 'Their balance' }, { key: 'difference', title: 'Difference' }, { key: 'reconciled', title: 'Reconciled' }]
  return (
    <div className="card">
      <div className="toolbar"><label className="row">As of <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label><span style={{ flex: 1 }} />
        {data && <Export name="intercompany-reconciliation" rows={data.rows} cols={cols} />}</div>
      <div className="card-body">
        {error && <div className="alert bad">{error}</div>}
        {loading && !data ? <div className="empty">Loading…</div> : data && (
          data.rows.length ? <table className="data">
            <thead><tr><th>Company</th><th>Partner</th><th>Linked company</th><th className="num">Our balance</th><th className="num">Their balance</th><th className="num">Difference</th><th>Status</th></tr></thead>
            <tbody>{data.rows.map((r: any) => <tr key={r.partner_id}>
              <td>{r.company}</td><td>{r.partner}</td><td>{r.linked_company}{r.mirror_partner_id == null && <span className="muted"> (no mirroring partner record found)</span>}</td>
              <td className="num">{M(r.our_balance)}</td><td className="num">{r.their_balance != null ? M(r.their_balance) : '—'}</td><td className="num">{r.difference != null ? M(r.difference) : '—'}</td>
              <td>{r.reconciled ? <span className="badge ok">✓ Agrees</span> : <span className="badge bad">✗ Differs</span>}</td>
            </tr>)}</tbody>
          </table> : <div className="empty">No intercompany partner links set up yet. Mark a partner as intercompany on its record and pick the company it represents.</div>
        )}
      </div>
      <p className="muted" style={{ padding: '0 16px 16px' }}>Our balance = receivable − payable against that partner, from our own books. "Their balance" is read from the mirroring partner record (if any) in the linked company's own books. Nothing is eliminated automatically — this is a reconciliation aid only.</p>
    </div>
  )
}

function Consolidated() {
  const [dateFrom, setDateFrom] = useState(yearStart())
  const [dateTo, setDateTo] = useState(today())
  const [level, setLevel] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const query: any = { date_from: dateFrom, date_to: dateTo }
  if (level) query.level = level
  const { data, loading, error } = useFetch<any>('/global-reports/consolidated/', query)
  const cols = ['code', 'name', 'opening', 'debit', 'credit', 'closing'].map((k) => ({ key: k, title: k }))
  return (
    <div className="stack">
      <div className="card">
        <div className="toolbar">
          <label className="row">From <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></label>
          <label className="row">To <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></label>
          <select value={level} onChange={(e) => setLevel(e.target.value)}><option value="">All levels</option>{[1, 2, 3, 4].map((l) => <option key={l} value={l}>Up to level {l}</option>)}</select>
          <span style={{ flex: 1 }} />{data && <Export name="consolidated-trial-balance" rows={data.consolidated.rows} cols={cols} />}
        </div>
        <div className="card-body">
          {error && <div className="alert bad">{error}</div>}
          {loading && !data ? <div className="empty">Loading…</div> : data && (
            <table className="data">
              <thead><tr><th>Code</th><th>Account</th><th className="num">Opening</th><th className="num">Debit</th><th className="num">Credit</th><th className="num">Closing</th></tr></thead>
              <tbody>{data.consolidated.rows.map((r: any) => <tr key={r.code}><td className="mono">{r.code}</td><td>{r.name}</td><td className="num">{M(r.opening)}</td><td className="num">{M(r.debit)}</td><td className="num">{M(r.credit)}</td><td className="num">{M(r.closing)}</td></tr>)}</tbody>
              <tfoot><tr><td colSpan={2}>Total</td><td className="num">{M(data.consolidated.totals.opening)}</td><td className="num">{M(data.consolidated.totals.debit)}</td><td className="num">{M(data.consolidated.totals.credit)}</td><td className="num">{M(data.consolidated.totals.closing)}</td></tr></tfoot>
            </table>
          )}
        </div>
      </div>
      <p className="muted">Straight sum of each company's own trial balance by account code — no intercompany balances are eliminated (see Intercompany Reconciliation to check the two sides agree) and rows merge by account code, so this assumes every company shares one chart-of-accounts template.</p>
      {data && <div className="stack">
        <h3>By company</h3>
        {data.companies.map((c: any) => (
          <div className="card" key={c.company_id}>
            <div className="card-head" style={{ cursor: 'pointer' }} onClick={() => setExpanded(expanded === c.company_id ? null : c.company_id)}>
              <h2>{c.company}</h2><span className={`badge ${c.trial_balance.balanced ? 'ok' : 'bad'}`}>{c.trial_balance.balanced ? '✓ Balanced' : '✗ Out of balance'}</span>
              <span style={{ flex: 1 }} /><span className="muted">{num(c.trial_balance.rows.length)} accounts</span>
            </div>
            {expanded === c.company_id && <table className="data">
              <thead><tr><th>Code</th><th>Account</th><th className="num">Opening</th><th className="num">Debit</th><th className="num">Credit</th><th className="num">Closing</th></tr></thead>
              <tbody>{c.trial_balance.rows.map((r: any) => <tr key={r.account_id}><td className="mono">{r.code}</td><td>{r.name}</td><td className="num">{M(r.opening)}</td><td className="num">{M(r.debit)}</td><td className="num">{M(r.credit)}</td><td className="num">{M(r.closing)}</td></tr>)}</tbody>
            </table>}
          </div>
        ))}
      </div>}
    </div>
  )
}
