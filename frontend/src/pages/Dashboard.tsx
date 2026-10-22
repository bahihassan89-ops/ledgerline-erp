import { Link } from 'react-router-dom'
import { PageHead } from '../components/ui'
import { useAuth } from '../lib/auth'
import { money, num } from '../lib/format'
import { useFetch } from '../lib/hooks'

function Kpi({ label, value, foot, tone }: any) {
  return (
    <div className="card kpi-card"><div className="kpi-label">{label}</div>
      <div className="kpi-value" style={{ color: tone }}>{value}</div>{foot && <div className="kpi-foot">{foot}</div>}</div>
  )
}

export default function Dashboard() {
  const { company, me } = useAuth()
  const { data: d } = useFetch<any>('/dashboard/')
  const cur = d?.currency
  const max = Math.max(1, ...(d?.monthly || []).flatMap((m: any) => [num(m.income), num(m.expense)]))
  return (
    <>
      <PageHead title={`Good ${new Date().getHours() < 12 ? 'morning' : 'afternoon'}, ${me?.full_name.split(' ')[0]}`} sub={`${company?.name} · figures in ${cur ?? ''}`} />
      {!d ? <div className="empty">Loading…</div> : <div className="stack">
        <div className="grid kpi">
          <Kpi label="Cash & bank" value={money(d.cash)} />
          <Kpi label="Receivables" value={money(d.receivables)} foot={<Link to="/reports?r=ar-aging">{money(d.overdue_receivables)} overdue · {d.overdue_count} invoices</Link>} tone={num(d.overdue_receivables) > 0 ? undefined : undefined} />
          <Kpi label="Payables" value={money(d.payables)} foot={<Link to="/reports?r=ap-aging">AP aging →</Link>} />
          <Kpi label="Inventory value" value={money(d.inventory)} />
          <Kpi label="Revenue this month" value={money(d.revenue_mtd)} foot={`Year to date ${money(d.revenue_ytd)}`} />
          <Kpi label="Net profit YTD" value={money(d.net_profit_ytd)} tone={num(d.net_profit_ytd) < 0 ? 'var(--bad)' : 'var(--ok)'} />
          <Kpi label="Waiting approval" value={d.pending_approvals} foot={<Link to="/approvals">Open approvals →</Link>} />
          <Kpi label="Draft journal entries" value={d.draft_entries} foot={<Link to="/journal-entries?state=draft">Review drafts →</Link>} />
        </div>
        <div className="card">
          <div className="card-head"><h2>Income vs expenses — last 12 months</h2><span style={{ flex: 1 }} />
            <div className="legend"><span><i style={{ background: '#2f62e3' }} />Income</span><span><i style={{ background: '#c3cee3' }} />Expenses</span></div></div>
          <div className="card-body">
            <div className="bars">
              {d.monthly.map((m: any) => (
                <div className="col" key={m.month} title={`${m.month}\nIncome ${money(m.income)}\nExpenses ${money(m.expense)}`}>
                  <div className="pair">
                    <div className="b in" style={{ height: `${(num(m.income) / max) * 100}%` }} />
                    <div className="b out" style={{ height: `${(num(m.expense) / max) * 100}%` }} />
                  </div>
                  <div className="m">{new Date(m.month + '-01').toLocaleDateString('en', { month: 'short' })}</div>
                </div>))}
            </div>
          </div>
        </div>
      </div>}
    </>
  )
}
