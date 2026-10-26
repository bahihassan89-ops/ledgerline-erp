import { useMemo, useState } from 'react'
import { DataTable, Modal, PageHead } from '../components/ui'
import { get } from '../lib/api'
import { label } from '../lib/format'
import { useFetch } from '../lib/hooks'

const STATUS_TONE: Record<string, string> = { online: 'ok', away: 'warn', offline: '' }
const STATUS_NAME: Record<string, string> = { online: 'Online', away: 'Away', offline: 'Offline' }

function StatusDot({ status }: { status: string }) {
  return <span className={`badge ${STATUS_TONE[status] ?? ''}`}>{STATUS_NAME[status] ?? label(status)}</span>
}

function fmtMinutes(m: number) {
  const total = Math.round(m || 0)
  const h = Math.floor(total / 60), mm = total % 60
  return h ? `${h}h ${mm}m` : `${mm}m`
}

function Kpi({ label, value }: { label: string; value: string | number }) {
  return <div className="card kpi-card"><div className="kpi-label">{label}</div><div className="kpi-value">{value}</div></div>
}

export default function TeamActivity() {
  const { data, error, reload } = useFetch<any[]>('/team-activity/')
  const rows = data || []
  const [hist, setHist] = useState<{ user: any; events: any[] | null } | null>(null)

  const kpis = useMemo(() => {
    const online = rows.filter((r) => r.status === 'online')
    const signedIn = rows.filter((r) => r.status !== 'offline')
    const companies = new Set(signedIn.filter((r) => r.company).map((r) => r.company))
    const avgActive = rows.length ? rows.reduce((s, r) => s + Number(r.active_today_minutes || 0), 0) / rows.length : 0
    const longestIdle = signedIn.reduce((max, r) => {
      const idleMin = (Date.now() - new Date(r.last_heartbeat).getTime()) / 60000
      return Math.max(max, idleMin)
    }, 0)
    return { online: online.length, companies: companies.size, avgActive, longestIdle }
  }, [rows])

  const openHistory = (r: any) => {
    setHist({ user: r, events: null })
    get(`/team-activity/${r.user}/history/`).then((events) => setHist((h) => h && h.user.user === r.user ? { ...h, events } : h))
  }

  return (
    <>
      <PageHead title="Team Activity" sub="Who's online, which company and module they're working in, and active hours today. Live, refreshes automatically." />
      {error && <div className="alert bad">{error}</div>}
      <div className="grid kpi" style={{ marginBottom: 16 }}>
        <Kpi label="Online now" value={kpis.online} />
        <Kpi label="Companies with active users" value={kpis.companies} />
        <Kpi label="Avg active time today" value={fmtMinutes(kpis.avgActive)} />
        <Kpi label="Longest idle (signed in)" value={fmtMinutes(kpis.longestIdle)} />
      </div>

      <div className="card"><DataTable rows={rows} onRow={openHistory} empty="No presence data yet." cols={[
        { key: 'full_name', title: 'User' },
        { key: 'company_name', title: 'Company', render: (r) => r.company_name || '—' },
        { key: 'module_code', title: 'Module', render: (r) => r.module_code ? label(r.module_code) : '—' },
        { key: 'section', title: 'Section', render: (r) => r.section || '—' },
        { key: 'status', title: 'Status', render: (r) => <StatusDot status={r.status} /> },
        { key: 'active_today_minutes', title: 'Active today', render: (r) => fmtMinutes(r.active_today_minutes) },
        { key: 'last_heartbeat', title: 'Last seen', render: (r) => r.last_heartbeat ? new Date(r.last_heartbeat).toLocaleString() : '—' },
      ]} foot={<tr><td colSpan={7}><button className="sm" onClick={() => reload()}>Refresh</button></td></tr>} /></div>

      {hist && <Modal wide title={`${hist.user.full_name} — history`} onClose={() => setHist(null)}>
        {!hist.events ? <div className="empty">Loading…</div> : !hist.events.length ? <div className="empty">No recorded events yet.</div> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>When</th><th>Event</th><th>Company</th><th>Module</th><th>Section</th></tr></thead>
              <tbody>{hist.events.map((e) => (
                <tr key={e.id}>
                  <td>{new Date(e.at).toLocaleString()}</td>
                  <td>{label(e.event)}</td>
                  <td>{e.company_name || '—'}</td>
                  <td>{e.module_code ? label(e.module_code) : '—'}</td>
                  <td>{e.section || '—'}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Modal>}
    </>
  )
}
