import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { get, list, post } from '../lib/api'
import { disablePush, enablePush, getPushSubscriptionState, pushSupported } from '../lib/push'

type Notif = {
  id: number
  company: number | null
  category: string
  title: string
  body: string
  url: string
  read_at: string | null
  created_at: string
}

function timeAgo(iso: string) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

/** Bell icon + dropdown feed, polled every ~20s. Lives in the topbar; notifications span every
 * company the user belongs to, so this doesn't filter by the currently-selected company. */
export default function NotificationBell() {
  const [open, setOpen] = useState(false)
  const [count, setCount] = useState(0)
  const [items, setItems] = useState<Notif[] | null>(null)
  const [pushState, setPushState] = useState<'unsupported' | 'denied' | 'subscribed' | 'available'>('unsupported')
  const ref = useRef<HTMLDivElement>(null)
  const nav = useNavigate()

  useEffect(() => { if (pushSupported()) getPushSubscriptionState().then(setPushState) }, [])

  const togglePush = async () => {
    if (pushState === 'subscribed') { await disablePush(); setPushState('available'); return }
    const ok = await enablePush()
    setPushState(ok ? 'subscribed' : (Notification.permission === 'denied' ? 'denied' : 'available'))
  }

  const pollCount = useCallback(() => {
    get<{ count: number }>('/notifications/unread-count/').then((d) => setCount(d.count)).catch(() => {})
  }, [])

  useEffect(() => {
    pollCount()
    const id = window.setInterval(pollCount, 20_000)
    return () => window.clearInterval(id)
  }, [pollCount])

  useEffect(() => {
    const onClick = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const toggle = () => {
    const next = !open
    setOpen(next)
    if (next) list<Notif>('/notifications/', { page_size: 20 }).then(setItems).catch(() => setItems([]))
  }

  const markAllRead = async () => {
    await post('/notifications/mark-all-read/').catch(() => {})
    setItems((cur) => cur?.map((n) => ({ ...n, read_at: n.read_at || new Date().toISOString() })) ?? cur)
    setCount(0)
  }

  const openNotif = async (n: Notif) => {
    if (!n.read_at) {
      post(`/notifications/${n.id}/read/`).catch(() => {})
      setItems((cur) => cur?.map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)) ?? cur)
      setCount((c) => Math.max(0, c - 1))
    }
    setOpen(false)
    if (n.url) nav(n.url)
  }

  return (
    <div className="notif-bell-wrap" ref={ref}>
      <button className="notif-bell" onClick={toggle} aria-label="Notifications">
        🔔
        {count > 0 && <span className="notif-dot">{count > 99 ? '99+' : count}</span>}
      </button>
      {open && (
        <div className="notif-panel">
          <div className="notif-panel-head">
            <b>Notifications</b>
            <div className="row">
              {pushState !== 'unsupported' && pushState !== 'denied' && (
                <button className="sm ghost" onClick={togglePush} title="Get notifications even when this tab isn't open">
                  {pushState === 'subscribed' ? 'Disable push' : 'Enable push'}
                </button>
              )}
              <button className="sm ghost" onClick={markAllRead}>Mark all read</button>
            </div>
          </div>
          <div className="notif-list">
            {items === null && <div className="empty">Loading…</div>}
            {items !== null && !items.length && <div className="empty">No notifications yet.</div>}
            {items?.map((n) => (
              <button key={n.id} className={`notif-item ${n.read_at ? '' : 'unread'}`} onClick={() => openNotif(n)}>
                <div className="notif-item-row"><span className="notif-title">{n.title}</span><span className="notif-time">{timeAgo(n.created_at)}</span></div>
                {n.body && <div className="notif-body">{n.body}</div>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
