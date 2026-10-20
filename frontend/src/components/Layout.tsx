import { useState, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Field, Modal, useAction } from './ui'

type Item = { to: string; label: string; ico: string; perm: string | string[] }
const NAV: [string, Item[]][] = [
  ['Overview', [
    { to: '/', label: 'Dashboard', ico: '◧', perm: 'ledger.report.view' },
    { to: '/approvals', label: 'Approvals', ico: '✓', perm: 'approvals.request.view' },
  ]],
  ['Accounting', [
    { to: '/accounts', label: 'Chart of Accounts', ico: '⊞', perm: 'ledger.account.view' },
    { to: '/journal-entries', label: 'Journal Entries', ico: '≡', perm: 'ledger.entry.view' },
    { to: '/periods', label: 'Fiscal Periods', ico: '◷', perm: 'ledger.entry.view' },
    { to: '/reports', label: 'Financial Reports', ico: '▤', perm: 'ledger.report.view' },
  ]],
  ['Receivables', [
    { to: '/sales-orders', label: 'Sales Orders', ico: '↗', perm: 'sales.order.view' },
    { to: '/invoices', label: 'Customer Invoices', ico: '⎘', perm: 'billing.invoice.view' },
    { to: '/receipts', label: 'Customer Receipts', ico: '⇙', perm: 'billing.payment.view' },
    { to: '/customers', label: 'Customers', ico: '☺', perm: 'partners.partner.view' },
  ]],
  ['Payables', [
    { to: '/purchase-orders', label: 'Purchase Orders', ico: '↙', perm: 'purchasing.order.view' },
    { to: '/bills', label: 'Vendor Bills', ico: '⎗', perm: 'billing.bill.view' },
    { to: '/vendor-payments', label: 'Vendor Payments', ico: '⇗', perm: 'billing.payment.view' },
    { to: '/vendors', label: 'Vendors', ico: '⚑', perm: 'partners.partner.view' },
  ]],
  ['Inventory', [
    { to: '/products', label: 'Products', ico: '▣', perm: 'inventory.product.view' },
    { to: '/stock', label: 'Stock & Moves', ico: '⇄', perm: 'inventory.stock.view' },
  ]],
  ['Administration', [
    { to: '/settings', label: 'Accounting Setup', ico: '⚙', perm: ['ledger.account.manage', 'ledger.tax.manage', 'core.company.manage'] },
    { to: '/users', label: 'Users & Roles', ico: '⚇', perm: 'iam.user.view' },
    { to: '/integrations', label: 'Integrations', ico: '⌁', perm: ['integrations.apikey.manage', 'integrations.webhook.manage'] },
    { to: '/audit', label: 'Audit Trail', ico: '⌕', perm: 'audit.log.view' },
  ]],
]

export default function Layout({ children }: { children: ReactNode }) {
  const { me, company, can, logout, switchCompany } = useAuth()
  const [pwd, setPwd] = useState(false)
  const loc = useLocation()
  const current = NAV.flatMap(([, i]) => i).find((i) => i.to === loc.pathname || (i.to !== '/' && loc.pathname.startsWith(i.to)))
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark">L</div>Ledgerline</div>
        {me && me.companies.length > 1 && (
          <div className="company-switch">
            <select value={company?.id} onChange={(e) => switchCompany(Number(e.target.value))} aria-label="Company">
              {me.companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
        )}
        <nav className="nav">
          {NAV.map(([group, items]) => {
            const visible = items.filter((i) => can(i.perm))
            if (!visible.length) return null
            return <div key={group}><div className="nav-group">{group}</div>
              {visible.map((i) => <NavLink key={i.to} to={i.to} end={i.to === '/'}><span className="ico">{i.ico}</span>{i.label}</NavLink>)}
            </div>
          })}
        </nav>
        <div className="sidebar-foot">
          <div className="who">{me?.full_name}</div>
          <div>{company?.roles.map((r) => r.name).join(', ') || (me?.is_superuser ? 'Superuser' : '')}</div>
          <div style={{ marginTop: 4 }}>Level {company?.level ?? 0} · Approval limit {company?.approval_limit === null ? 'unlimited' : Number(company?.approval_limit ?? 0).toLocaleString()}</div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="sm" onClick={() => setPwd(true)}>Password</button>
            <button className="sm" onClick={logout}>Sign out</button>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="crumbs">{company?.name} {current && <>/ <b style={{ color: 'var(--text)' }}>{current.label}</b></>}</div>
          <div className="spacer" />
          <span className="badge">{company?.base_currency}</span>
          {me && !me.mfa_enabled && <span className="badge warn" title="Enable MFA from your administrator">MFA off</span>}
        </header>
        <main className="content">{children}</main>
      </div>
      {(pwd || me?.must_change_password) && <ChangePassword forced={!!me?.must_change_password} onClose={() => setPwd(false)} />}
    </div>
  )
}

function ChangePassword({ onClose, forced }: { onClose: () => void; forced: boolean }) {
  const { reload } = useAuth()
  const [f, setF] = useState({ old_password: '', new_password: '', confirm: '' })
  const { run, busy } = useAction()
  const save = async () => {
    if (f.new_password !== f.confirm) return alert('Passwords do not match')
    const r = await run(() => post('/auth/change-password/', f), 'Password changed')
    if (r) { await reload(); onClose() }
  }
  return (
    <Modal title={forced ? 'Please set a new password' : 'Change password'} onClose={forced ? () => {} : onClose}
      footer={<><button className="primary" disabled={busy} onClick={save}>Save</button></>}>
      {forced && <div className="alert warn">Your password was set by an administrator or has expired.</div>}
      <Field label="Current password"><input type="password" value={f.old_password} onChange={(e) => setF({ ...f, old_password: e.target.value })} /></Field>
      <Field label="New password" hint="At least 12 characters with upper & lower case, a digit and a symbol."><input type="password" value={f.new_password} onChange={(e) => setF({ ...f, new_password: e.target.value })} /></Field>
      <Field label="Confirm new password"><input type="password" value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} /></Field>
    </Modal>
  )
}
