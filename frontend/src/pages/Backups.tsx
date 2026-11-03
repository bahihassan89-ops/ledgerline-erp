import { useEffect, useRef, useState } from 'react'
import { PageHead } from '../components/ui'
import { post } from '../lib/api'
import { useFetch } from '../lib/hooks'

type BackupStatus = {
  last_backup_at: string | null
  last_status: 'ok' | 'error' | null
  last_trigger: 'manual' | 'scheduled' | 'cli' | null
  requested_by: string | null
  last_size_kb: number | null
  last_filename?: string
  pending: boolean
}

const TRIGGER_LABEL: Record<string, string> = { manual: 'Manual (Back up now)', scheduled: 'Scheduled', cli: 'Command line' }

function fmtSize(kb: number | null) {
  if (!kb) return '—'
  if (kb < 1024) return `${kb} KB`
  return `${(kb / 1024).toFixed(1)} MB`
}

export default function Backups() {
  const { data, error, reload } = useFetch<BackupStatus>('/ops/backup/')
  const [busy, setBusy] = useState(false)
  const [requestError, setRequestError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  // While a backup is pending -- either because we just triggered one, or because the status
  // already reports "pending": true (another admin clicked the button, or a scheduled run is in
  // flight) -- poll every 3s so the page updates itself without a manual refresh.
  useEffect(() => {
    if (data?.pending) timer.current = window.setInterval(reload, 3000)
    return () => { if (timer.current) { window.clearInterval(timer.current); timer.current = null } }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.pending])

  const backupNow = async () => {
    setBusy(true)
    setRequestError(null)
    try {
      await post('/ops/backup/')
      await reload()
    } catch (e: any) {
      setRequestError(e.message || 'Could not request a backup.')
    } finally {
      setBusy(false)
    }
  }

  const pending = !!data?.pending
  const lastStatus = data?.last_status

  return (
    <>
      <PageHead title="Backups" sub="Encrypted, on-demand or scheduled backups of the database and attachments. No cloud destination is configured yet -- backups are kept on the server until one is added." />
      {error && <div className="alert bad">{error}</div>}
      {requestError && <div className="alert bad">{requestError}</div>}

      <div className="card" style={{ maxWidth: 640 }}>
        <div className="grid kpi" style={{ marginBottom: 16 }}>
          <div className="card kpi-card">
            <div className="kpi-label">Last backup</div>
            <div className="kpi-value" style={{ fontSize: 18 }}>
              {data?.last_backup_at ? new Date(data.last_backup_at).toLocaleString() : 'Never'}
            </div>
          </div>
          <div className="card kpi-card">
            <div className="kpi-label">Status</div>
            <div className="kpi-value">
              {pending
                ? <span className="badge warn">In progress…</span>
                : lastStatus === 'ok' ? <span className="badge ok">OK</span>
                : lastStatus === 'error' ? <span className="badge bad">Failed</span>
                : <span className="badge">—</span>}
            </div>
          </div>
          <div className="card kpi-card">
            <div className="kpi-label">Triggered by</div>
            <div className="kpi-value" style={{ fontSize: 18 }}>
              {data?.last_trigger ? TRIGGER_LABEL[data.last_trigger] ?? data.last_trigger : '—'}
              {data?.requested_by ? ` (${data.requested_by})` : ''}
            </div>
          </div>
          <div className="card kpi-card">
            <div className="kpi-label">Size</div>
            <div className="kpi-value">{fmtSize(data?.last_size_kb ?? null)}</div>
          </div>
        </div>

        <button disabled={pending || busy} onClick={backupNow}>
          {pending ? 'Backup in progress…' : busy ? 'Requesting…' : 'Back up now'}
        </button>
        <p className="muted" style={{ marginTop: 12 }}>
          Runs immediately instead of waiting for the next scheduled backup. The dump and attachments
          are AES-256 encrypted before being written to disk; old sets are pruned automatically
          (14 daily, plus one per month for 12 months).
        </p>
      </div>
    </>
  )
}
