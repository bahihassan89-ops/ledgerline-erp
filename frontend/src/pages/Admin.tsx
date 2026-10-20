import { useState } from 'react'
import { AccountSelect, CurrencySelect, JournalSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, StateBadge, Tabs, useAction } from '../components/ui'
import { get, patch, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, label, money, num } from '../lib/format'
import { invalidateOptions, useFetch } from '../lib/hooks'
import { EntryById } from './JournalEntries'

// ------------------------------------------------------------------ approvals
export function Approvals() {
  const [tab, setTab] = useState('mine')
  const { data, reload } = useFetch<any>('/approval-requests/', tab === 'mine' ? { mine: 1 } : tab === 'pending' ? { state: 'pending' } : {})
  const [cur, setCur] = useState<any>(null)
  const { run } = useAction()
  const decide = async (approve: boolean) => {
    const comment = approve ? (window.prompt('Comment (optional):') ?? '') : window.prompt('Reason for rejection (required):')
    if (!approve && !comment) return
    if (await run(() => post(`/approval-requests/${cur.id}/${approve ? 'approve' : 'reject'}/`, { comment }), approve ? 'Approved' : 'Rejected')) { setCur(null); reload() }
  }
  return (
    <>
      <PageHead title="Approvals" sub="Documents above an approval threshold. You cannot approve what you prepared, and only within your level and limit." />
      <Tabs tabs={[['mine', 'Waiting for me'], ['pending', 'All pending'], ['all', 'History']]} value={tab} onChange={setTab} />
      <div className="card"><DataTable rows={data?.results} onRow={setCur} empty="Nothing waiting." cols={[
        { key: 'created_at', title: 'Requested', render: (r) => fmtDate(r.created_at.slice(0, 10)) }, { key: 'document_type', title: 'Document', render: (r) => label(r.document_type) },
        { key: 'document_label', title: 'Reference' }, { key: 'amount', title: 'Amount (base)', num: true, render: (r) => money(r.amount) }, { key: 'requested_by_name', title: 'By' },
        { key: 'step', title: 'Step', render: (r) => r.state === 'pending' ? `${r.current_step + 1} of ${r.levels.length} · needs level ${r.required_level}` : '' },
        { key: 'state', title: 'State', render: (r) => <StateBadge state={r.state} /> }]} /></div>
      {cur && <Modal title={`${label(cur.document_type)} — ${cur.document_label}`} onClose={() => setCur(null)}
        footer={cur.state === 'pending' && <><button className="danger" onClick={() => decide(false)}>Reject</button><button className="primary" onClick={() => decide(true)}>Approve</button></>}>
        <dl className="kv"><dt>Amount</dt><dd>{money(cur.amount)}</dd><dt>Rule</dt><dd>{cur.rule_name}</dd><dt>Requested by</dt><dd>{cur.requested_by_name}</dd><dt>Chain</dt><dd>{cur.levels.map((l: number) => `L${l}`).join(' → ')}</dd></dl>
        {cur.decisions.map((d: any, i: number) => <div key={i} className={`alert ${d.approved ? 'ok' : 'bad'}`}>Step {d.step + 1}: {d.approved ? 'Approved' : 'Rejected'} by {d.user_name} {d.comment && `— “${d.comment}”`}</div>)}
        <div className="muted">Open the document from its module to review the details before deciding.</div>
      </Modal>}
    </>
  )
}

// ------------------------------------------------------------------ periods
export function Periods() {
  const { can } = useAuth()
  const { data, reload } = useFetch<any[]>('/fiscal-years/')
  const { run } = useAction()
  const [closing, setClosing] = useState<number | null>(null)
  const setState = async (p: any, state: string) => {
    if (state === 'locked' && !window.confirm(`Lock ${p.name}? This is permanent: nobody can post into it again.`)) return
    if (await run(() => post(`/fiscal-periods/${p.id}/set_state/`, { state }), `${p.name}: ${state}`)) reload()
  }
  const openYear = async () => { const y = window.prompt('Start year of the new fiscal year:', String(new Date().getFullYear() + 1)); if (y && await run(() => post('/fiscal-years/open/', { start_year: Number(y) }), 'Fiscal year created')) reload() }
  const closeYear = async (fy: any) => { if (!window.confirm(`Run year-end closing for ${fy.name}? Income and expense balances will be transferred to Retained Earnings.`)) return; const r = await run(() => post(`/fiscal-years/${fy.id}/close_year/`), 'Year closed'); if (r) { reload(); if (r.closing_entry) setClosing(null) } }
  return (
    <>
      <PageHead title="Fiscal Years & Periods" sub="Open → Closed (only privileged users may post) → Locked (nobody may post, permanent)." actions={can('ledger.period.manage') && <button className="primary" onClick={openYear}>+ Open fiscal year</button>} />
      <div className="stack">{data?.map((fy) => (
        <div className="card" key={fy.id}>
          <div className="card-head"><h2>Fiscal year {fy.name}</h2><span className="muted">{fmtDate(fy.start_date)} – {fmtDate(fy.end_date)}</span>{fy.closed && <span className="badge ok">Closed · {fy.closing_entry_number || 'no P&L'}</span>}<span style={{ flex: 1 }} />
            {!fy.closed && can('ledger.year.close') && <button onClick={() => closeYear(fy)}>Year-end close</button>}
            {fy.closing_entry && <button className="sm" onClick={() => setClosing(fy.closing_entry)}>Closing entry</button>}</div>
          <DataTable rows={fy.periods} cols={[{ key: 'name', title: 'Period' }, { key: 'start_date', title: 'From', render: (p) => fmtDate(p.start_date) }, { key: 'end_date', title: 'To', render: (p) => fmtDate(p.end_date) },
            { key: 'state', title: 'State', render: (p) => <StateBadge state={p.state} /> },
            { key: 'a', title: '', render: (p) => <div className="row" style={{ justifyContent: 'flex-end' }}>
              {p.state === 'open' && can('ledger.period.close') && <button className="sm" onClick={() => setState(p, 'closed')}>Close</button>}
              {p.state === 'closed' && can('ledger.period.close') && <button className="sm" onClick={() => setState(p, 'open')}>Reopen</button>}
              {p.state !== 'locked' && can('ledger.period.lock') && <button className="sm danger" onClick={() => setState(p, 'locked')}>Lock</button>}</div> }]} />
        </div>))}</div>
      {closing && <EntryById id={closing} onClose={() => setClosing(null)} />}
    </>
  )
}

// ------------------------------------------------------------------ settings
export function Settings() {
  const [tab, setTab] = useState('company')
  return (
    <>
      <PageHead title="Accounting Setup" />
      <Tabs tabs={[['company', 'Company'], ['defaults', 'Default accounts'], ['journals', 'Journals'], ['taxes', 'Taxes'], ['rates', 'Exchange rates'], ['rules', 'Approval rules'], ['cc', 'Cost centers']]} value={tab} onChange={setTab} />
      {tab === 'company' && <CompanyTab />}{tab === 'defaults' && <DefaultsTab />}{tab === 'journals' && <SimpleCrud path="/journals/" title="Journal" fields={JOURNAL_FIELDS} />}
      {tab === 'taxes' && <SimpleCrud path="/taxes/" title="Tax" fields={TAX_FIELDS} />}{tab === 'rates' && <SimpleCrud path="/exchange-rates/" title="Exchange rate" fields={RATE_FIELDS} />}
      {tab === 'rules' && <SimpleCrud path="/approval-rules/" title="Approval rule" fields={RULE_FIELDS} />}{tab === 'cc' && <SimpleCrud path="/cost-centers/" title="Cost center" fields={[{ k: 'code', l: 'Code' }, { k: 'name', l: 'Name' }, { k: 'active', l: 'Active', t: 'bool' }]} />}
    </>
  )
}

function CompanyTab() {
  const { data, setData } = useFetch<any>('/company/current/')
  const { run, busy } = useAction()
  const { can } = useAuth()
  if (!data) return null
  const set = (k: string, v: any) => setData({ ...data, [k]: v })
  const ro = !can('core.company.manage')
  return <div className="card card-body stack">
    <div className="grid form">
      {[['name', 'Trading name'], ['name_ar', 'الاسم التجاري بالعربية'], ['legal_name', 'Legal name'], ['tax_number', 'Tax registration no. (9 digits)'], ['registration_number', 'Commercial registration'], ['tax_office', 'Tax office (المأمورية)'], ['country', 'Country']].map(([k, l]) => <Field key={k} label={l}><input value={data[k] || ''} onChange={(e) => set(k, e.target.value)} disabled={ro} /></Field>)}
      <Field label="Base currency" hint="Fixed at company creation"><input value={data.base_currency} disabled /></Field>
      <Field label="Books locked up to" hint="No posting on or before this date"><input type="date" value={data.lock_date || ''} onChange={(e) => set('lock_date', e.target.value || null)} disabled={ro} /></Field>
      <Field label="Bill price tolerance %" hint="3-way match: allowed excess over PO price"><input className="num" value={data.bill_price_tolerance_pct} onChange={(e) => set('bill_price_tolerance_pct', e.target.value)} disabled={ro} /></Field>
    </div>
    <Field label="Address"><textarea rows={2} value={data.address} onChange={(e) => set('address', e.target.value)} disabled={ro} /></Field>
    {!ro && <div><button className="primary" disabled={busy} onClick={() => run(() => patch('/company/current/', data), 'Company saved')}>Save</button></div>}
  </div>
}

const DEFAULTS: [string, string, (a: any) => boolean][] = [
  ['receivable_account', 'Accounts receivable', (a) => a.account_type === 'receivable'], ['payable_account', 'Accounts payable', (a) => a.account_type === 'payable'],
  ['grni_account', 'Goods received not invoiced', (a) => a.account_type === 'grni'], ['price_variance_account', 'Purchase price variance', (a) => a.account_class === 'expense'],
  ['inventory_adjustment_account', 'Inventory adjustments', (a) => a.account_class === 'expense'], ['fx_gain_account', 'Realised FX gain', (a) => a.account_class === 'income'],
  ['fx_loss_account', 'Realised FX loss', (a) => a.account_class === 'expense'], ['retained_earnings_account', 'Retained earnings', (a) => a.account_type === 'retained_earnings'],
]
function DefaultsTab() {
  const { data, setData } = useFetch<any>('/accounting-settings/')
  const { run, busy } = useAction()
  if (!data) return null
  return <div className="card card-body stack">
    <div className="grid form">{DEFAULTS.map(([k, l, f]) => <Field key={k} label={l}><AccountSelect value={data[k]} onChange={(v) => setData({ ...data, [k]: v })} filter={f} /></Field>)}
      {[['sales_journal', 'Sales journal'], ['purchase_journal', 'Purchase journal'], ['inventory_journal', 'Inventory journal'], ['general_journal', 'General journal'], ['closing_journal', 'Closing journal']].map(([k, l]) => <Field key={k} label={l}><JournalSelect value={data[k]} onChange={(v) => setData({ ...data, [k]: v })} /></Field>)}</div>
    <div><button className="primary" disabled={busy} onClick={() => run(() => patch('/accounting-settings/', data), 'Saved')}>Save</button></div>
  </div>
}

type FieldDef = { k: string; l: string; t?: 'bool' | 'num' | 'date' | 'account' | 'currency' | 'select' | 'levels'; opts?: [string, string][]; f?: (a: any) => boolean; show?: (r: any) => any }
const JOURNAL_FIELDS: FieldDef[] = [{ k: 'code', l: 'Code' }, { k: 'name', l: 'Name' }, { k: 'type', l: 'Type', t: 'select', opts: [['general', 'General'], ['bank', 'Bank'], ['cash', 'Cash'], ['sale', 'Sales'], ['purchase', 'Purchases'], ['inventory', 'Inventory']] },
  { k: 'default_account', l: 'Bank / cash account', t: 'account', f: (a) => ['bank', 'cash'].includes(a.account_type) }, { k: 'currency', l: 'Currency (optional)', t: 'currency' }, { k: 'allow_manual', l: 'Allow manual entries', t: 'bool' }, { k: 'active', l: 'Active', t: 'bool' }]
const TAX_FIELDS: FieldDef[] = [{ k: 'code', l: 'Code' }, { k: 'name', l: 'Name' }, { k: 'name_ar', l: 'الاسم بالعربية' }, { k: 'scope', l: 'Scope', t: 'select', opts: [['sale', 'Sales'], ['purchase', 'Purchases']] },
  { k: 'kind', l: 'Kind', t: 'select', opts: [['vat', 'VAT (added)'], ['withholding', 'Withholding (deducted)']] }, { k: 'rate', l: 'Rate %', t: 'num' },
  { k: 'eta_type', l: 'ETA tax type (T1, T4…)' }, { k: 'eta_subtype', l: 'ETA sub-type (V009, W004…)' }, { k: 'min_base', l: 'Withholding from amount (EGP)', t: 'num' },
  { k: 'price_include', l: 'Prices include tax', t: 'bool' }, { k: 'account', l: 'Tax account', t: 'account', f: (a) => ['tax_input', 'tax_output', 'wht_payable', 'wht_receivable'].includes(a.account_type) }, { k: 'active', l: 'Active', t: 'bool' }]
const RATE_FIELDS: FieldDef[] = [{ k: 'currency', l: 'Currency', t: 'currency' }, { k: 'date', l: 'Effective date', t: 'date' }, { k: 'rate', l: '1 unit = ? base currency', t: 'num' }]
const RULE_FIELDS: FieldDef[] = [{ k: 'name', l: 'Name' }, { k: 'document_type', l: 'Document', t: 'select', opts: [['purchase_order', 'Purchase order'], ['sales_order', 'Sales order'], ['vendor_bill', 'Vendor bill'], ['customer_invoice', 'Customer invoice'], ['payment', 'Payment'], ['journal_entry', 'Manual journal entry']] },
  { k: 'min_amount', l: 'From amount (base)', t: 'num' }, { k: 'approver_levels', l: 'Approver levels in order', t: 'levels', show: (r) => r.approver_levels.map((l: number) => `L${l}`).join(' → ') }, { k: 'active', l: 'Active', t: 'bool' }]

function SimpleCrud({ path, title, fields }: { path: string; title: string; fields: FieldDef[] }) {
  const { data, reload } = useFetch<any>(path, { page_size: 500 })
  const rows = Array.isArray(data) ? data : data?.results
  const [edit, setEdit] = useState<any>(null)
  const { run, busy } = useAction()
  const save = async () => {
    const { id, ...b } = edit
    if (typeof b.approver_levels === 'string') b.approver_levels = b.approver_levels.split(/[ ,>→]+/).filter(Boolean).map(Number)
    if (await run(() => (id ? patch(`${path}${id}/`, b) : post(path, b)), 'Saved')) { setEdit(null); invalidateOptions(); reload() }
  }
  const render = (f: FieldDef, r: any) => f.show ? f.show(r) : f.t === 'bool' ? (r[f.k] ? '✓' : '') : f.t === 'num' ? num(r[f.k]).toLocaleString() : f.t === 'select' ? f.opts?.find(([k]) => k === r[f.k])?.[1] : f.t === 'account' ? (r[f.k] ?? '') : r[f.k]
  return <div className="card">
    <div className="toolbar"><span style={{ flex: 1 }} /><button className="primary" onClick={() => setEdit(Object.fromEntries(fields.map((f) => [f.k, f.t === 'bool' ? true : f.t === 'levels' ? '4' : null])))}>+ New {title.toLowerCase()}</button></div>
    <DataTable rows={rows} onRow={(r) => setEdit({ ...r, approver_levels: r.approver_levels?.join(', ') })} cols={fields.filter((f) => f.t !== 'account').map((f) => ({ key: f.k, title: f.l, render: (r: any) => render(f, r) }))} />
    {edit && <Modal title={edit.id ? `Edit ${title.toLowerCase()}` : `New ${title.toLowerCase()}`} onClose={() => setEdit(null)} footer={<><button onClick={() => setEdit(null)}>Cancel</button><button className="primary" disabled={busy} onClick={save}>Save</button></>}>
      {fields.map((f) => {
        const v = edit[f.k]; const set = (x: any) => setEdit({ ...edit, [f.k]: x })
        if (f.t === 'bool') return <label key={f.k} className="check"><input type="checkbox" checked={!!v} onChange={(e) => set(e.target.checked)} />{f.l}</label>
        return <Field key={f.k} label={f.l} hint={f.t === 'levels' ? 'e.g. "4" or "4, 5" (Controller then CFO)' : undefined}>
          {f.t === 'select' ? <select value={v ?? ''} onChange={(e) => set(e.target.value)}><option value="">Select…</option>{f.opts!.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
            : f.t === 'account' ? <AccountSelect allowEmpty value={v} onChange={set} filter={f.f} />
              : f.t === 'currency' ? <CurrencySelect allowEmpty value={v} onChange={set} />
                : <input type={f.t === 'date' ? 'date' : 'text'} className={f.t === 'num' ? 'num' : ''} value={v ?? ''} onChange={(e) => set(e.target.value)} />}
        </Field>
      })}
    </Modal>}
  </div>
}

// ------------------------------------------------------------------ users & roles
export function Users() {
  const { can, me } = useAuth()
  const [tab, setTab] = useState('users')
  const users = useFetch<any>('/users/', { page_size: 500 })
  const roles = useFetch<any[]>('/roles/')
  const perms = useFetch<any[]>('/permissions/')
  const [u, setU] = useState<any>(null)
  const [r, setR] = useState<any>(null)
  const { run, busy } = useAction()
  const saveUser = async () => {
    const { id, roles: _r, locked, mfa_enabled, last_login, ...b } = u; void _r; void locked; void mfa_enabled; void last_login
    if (!b.password) delete b.password
    if (await run(() => (id ? patch(`/users/${id}/`, b) : post('/users/', b)), 'User saved')) { setU(null); users.reload() }
  }
  const saveRole = async () => { const { id, ...b } = r; if (await run(() => (id ? patch(`/roles/${id}/`, b) : post('/roles/', b)), 'Role saved')) { setR(null); roles.reload() } }
  const act = async (id: number, a: string, msg: string) => { if (window.confirm(`${msg}?`) && await run(() => post(`/users/${id}/${a}/`), 'Done')) users.reload() }
  const modules = Array.from(new Set((perms.data || []).map((p) => p.module)))
  return (
    <>
      <PageHead title="Users & Roles" sub="Roles carry a managerial level (0–5), an approval limit and fine-grained permissions. You can only grant rights you hold yourself."
        actions={can('iam.user.manage') && (tab === 'users' ? <button className="primary" onClick={() => setU({ username: '', email: '', first_name: '', last_name: '', is_active: true, role_ids: [], password: '' })}>+ New user</button> : can('iam.role.manage') && <button className="primary" onClick={() => setR({ name: '', level: 1, permissions: [], approval_limit: '0', active: true })}>+ New role</button>)} />
      <Tabs tabs={[['users', 'Users'], ['roles', 'Roles & permissions']]} value={tab} onChange={setTab} />
      {tab === 'users' ? <div className="card"><DataTable rows={users.data?.results} onRow={(x) => can('iam.user.manage') && setU({ ...x, role_ids: x.roles.map((y: any) => y.id), password: '' })} cols={[
        { key: 'username', title: 'Username' }, { key: 'name', title: 'Name', render: (x) => `${x.first_name} ${x.last_name}` }, { key: 'email', title: 'Email' },
        { key: 'roles', title: 'Roles', render: (x) => x.roles.map((y: any) => <span key={y.id} className="badge" style={{ marginRight: 4 }}>{y.name}</span>) },
        { key: 'mfa', title: 'MFA', render: (x) => x.mfa_enabled ? <span className="badge ok">On</span> : <span className="badge">Off</span> },
        { key: 'st', title: 'Status', render: (x) => <>{!x.is_active && <span className="badge bad">Disabled</span>} {x.locked && <span className="badge bad">Locked</span>}</> },
        { key: 'last_login', title: 'Last login', render: (x) => x.last_login ? new Date(x.last_login).toLocaleString() : '—' },
        { key: 'a', title: '', render: (x) => can('iam.user.manage') && x.id !== me?.id && <div className="row" onClick={(e) => e.stopPropagation()}>{x.locked && <button className="sm" onClick={() => act(x.id, 'unlock', 'Unlock this account')}>Unlock</button>}{x.mfa_enabled && <button className="sm" onClick={() => act(x.id, 'reset_mfa', 'Reset MFA (user must re-enrol)')}>Reset MFA</button>}</div> }]} /></div>
        : <div className="card"><DataTable rows={roles.data} onRow={(x) => can('iam.role.manage') && setR({ ...x })} cols={[{ key: 'name', title: 'Role' }, { key: 'level', title: 'Level', render: (x) => `L${x.level}` },
          { key: 'approval_limit', title: 'Approval limit', num: true, render: (x) => x.approval_limit === null ? 'Unlimited' : money(x.approval_limit) }, { key: 'n', title: 'Permissions', num: true, render: (x) => x.permissions.length }, { key: 'description', title: 'Description' }]} /></div>}
      {u && <Modal title={u.id ? u.username : 'New user'} onClose={() => setU(null)} footer={<><button onClick={() => setU(null)}>Cancel</button><button className="primary" disabled={busy} onClick={saveUser}>Save</button></>}>
        <div className="grid form">{[['username', 'Username'], ['email', 'Email'], ['first_name', 'First name'], ['last_name', 'Last name']].map(([k, l]) => <Field key={k} label={l}><input value={u[k]} onChange={(e) => setU({ ...u, [k]: e.target.value })} disabled={k === 'username' && !!u.id} /></Field>)}</div>
        <Field label={u.id ? 'Reset password (leave empty to keep)' : 'Initial password'} hint="User must change it at first login."><input type="password" autoComplete="new-password" value={u.password} onChange={(e) => setU({ ...u, password: e.target.value })} /></Field>
        <Field label="Roles in this company">{roles.data?.map((x) => <label key={x.id} className="check"><input type="checkbox" checked={u.role_ids.includes(x.id)} onChange={(e) => setU({ ...u, role_ids: e.target.checked ? [...u.role_ids, x.id] : u.role_ids.filter((i: number) => i !== x.id) })} />{x.name} <span className="muted">L{x.level}</span></label>)}</Field>
        <label className="check"><input type="checkbox" checked={u.is_active} onChange={(e) => setU({ ...u, is_active: e.target.checked })} />Active</label>
      </Modal>}
      {r && <Modal wide title={r.id ? r.name : 'New role'} onClose={() => setR(null)} footer={<><button onClick={() => setR(null)}>Cancel</button><button className="primary" disabled={busy} onClick={saveRole}>Save</button></>}>
        <div className="grid form"><Field label="Name"><input value={r.name} onChange={(e) => setR({ ...r, name: e.target.value })} /></Field>
          <Field label="Managerial level" hint="Used by approval rules"><select value={r.level} onChange={(e) => setR({ ...r, level: Number(e.target.value) })}>{[0, 1, 2, 3, 4, 5].map((l) => <option key={l} value={l}>L{l}</option>)}</select></Field>
          <Field label="Approval limit" hint="Empty = unlimited"><input className="num" value={r.approval_limit ?? ''} onChange={(e) => setR({ ...r, approval_limit: e.target.value === '' ? null : e.target.value })} /></Field></div>
        <Field label="Description"><input value={r.description || ''} onChange={(e) => setR({ ...r, description: e.target.value })} /></Field>
        <div className="grid two">{modules.map((m) => <div key={m} className="card card-body"><h3 style={{ marginBottom: 8 }}>{label(m)}</h3>
          {perms.data!.filter((p) => p.module === m).map((p) => <label key={p.code} className="check" style={{ marginBottom: 4 }}><input type="checkbox" checked={r.permissions.includes(p.code)} onChange={(e) => setR({ ...r, permissions: e.target.checked ? [...r.permissions, p.code] : r.permissions.filter((c: string) => c !== p.code) })} />{p.description} <span className="mono muted" style={{ fontSize: 11 }}>{p.code}</span></label>)}</div>)}</div>
      </Modal>}
    </>
  )
}

// ------------------------------------------------------------------ integrations
export function Integrations() {
  const { can } = useAuth()
  const keys = useFetch<any>(can('integrations.apikey.manage') ? '/api-keys/' : null)
  const hooks = useFetch<any>(can('integrations.webhook.manage') ? '/webhooks/' : null)
  const deliveries = useFetch<any>(can('integrations.webhook.manage') ? '/webhook-deliveries/' : null)
  const events = useFetch<string[]>(can('integrations.webhook.manage') ? '/webhooks/events/' : null)
  const perms = useFetch<any[]>('/permissions/')
  const [k, setK] = useState<any>(null)
  const [h, setH] = useState<any>(null)
  const [secret, setSecret] = useState<any>(null)
  const { run, busy } = useAction()
  const saveKey = async () => { const r = await run(() => post('/api-keys/', k)); if (r) { setK(null); setSecret({ title: 'API key created', value: r.key, note: r.warning }); keys.reload() } }
  const saveHook = async () => { const r = await run(() => (h.id ? patch(`/webhooks/${h.id}/`, h) : post('/webhooks/', h))); if (r) { setH(null); if (r.secret) setSecret({ title: 'Webhook signing secret', value: r.secret, note: r.warning }); hooks.reload() } }
  return (
    <>
      <PageHead title="Integrations" sub={<>Connect other apps through the REST API (<a href="/api/docs/" target="_blank" rel="noreferrer">interactive API docs</a>) and signed webhooks.</>} />
      <div className="stack">
        {can('integrations.apikey.manage') && <div className="card"><div className="card-head"><h2>API keys</h2><span className="muted">A key acts as you, limited to its scopes.</span><span style={{ flex: 1 }} /><button className="primary" onClick={() => setK({ name: '', scopes: [], allowed_ips: [], expires_at: null })}>+ New key</button></div>
          <DataTable rows={keys.data?.results} cols={[{ key: 'name', title: 'Name' }, { key: 'prefix', title: 'Prefix', render: (x) => <span className="mono">{x.prefix}…</span> }, { key: 'owner_name', title: 'Owner' }, { key: 'scopes', title: 'Scopes', render: (x) => x.scopes.length },
            { key: 'last_used_at', title: 'Last used', render: (x) => x.last_used_at ? new Date(x.last_used_at).toLocaleString() : 'never' }, { key: 'active', title: 'Status', render: (x) => x.active ? <span className="badge ok">Active</span> : <span className="badge bad">Revoked</span> },
            { key: 'a', title: '', render: (x) => x.active && <button className="sm danger" onClick={async () => { if (window.confirm('Revoke this key?') && await run(() => patch(`/api-keys/${x.id}/`, { active: false }), 'Revoked')) keys.reload() }}>Revoke</button> }]} /></div>}
        {can('integrations.webhook.manage') && <div className="card"><div className="card-head"><h2>Webhooks</h2><span className="muted">HTTPS POST with HMAC-SHA256 signature on business events.</span><span style={{ flex: 1 }} /><button className="primary" onClick={() => setH({ name: '', url: 'https://', events: [], active: true })}>+ New webhook</button></div>
          <DataTable rows={hooks.data?.results} onRow={setH} cols={[{ key: 'name', title: 'Name' }, { key: 'url', title: 'URL', render: (x) => <span className="mono">{x.url}</span> }, { key: 'events', title: 'Events', render: (x) => x.events.join(', ') }, { key: 'active', title: '', render: (x) => x.active ? <span className="badge ok">Active</span> : <span className="badge">Paused</span> }]} />
          <div className="card-head" style={{ borderTop: '1px solid var(--border)' }}><h3>Recent deliveries</h3></div>
          <DataTable rows={deliveries.data?.results?.slice(0, 20)} cols={[{ key: 'created_at', title: 'Time', render: (x) => new Date(x.created_at).toLocaleString() }, { key: 'event', title: 'Event' }, { key: 'status', title: 'Status', render: (x) => <StateBadge state={x.status} /> }, { key: 'attempts', title: 'Attempts', num: true }, { key: 'last_status_code', title: 'HTTP' }, { key: 'last_error', title: 'Error' }]} /></div>}
      </div>
      {k && <Modal wide title="New API key" onClose={() => setK(null)} footer={<><button onClick={() => setK(null)}>Cancel</button><button className="primary" disabled={busy || !k.name || !k.scopes.length} onClick={saveKey}>Create key</button></>}>
        <div className="grid form"><Field label="Name (which app uses it)"><input value={k.name} onChange={(e) => setK({ ...k, name: e.target.value })} /></Field>
          <Field label="Expires"><input type="date" value={k.expires_at?.slice(0, 10) || ''} onChange={(e) => setK({ ...k, expires_at: e.target.value ? `${e.target.value}T23:59:59Z` : null })} /></Field>
          <Field label="Allowed IPs" hint="Comma-separated; empty = any"><input value={k.allowed_ips.join(', ')} onChange={(e) => setK({ ...k, allowed_ips: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} /></Field></div>
        <Field label="Scopes (least privilege)"><div className="grid two">{perms.data?.map((p) => <label key={p.code} className="check"><input type="checkbox" checked={k.scopes.includes(p.code)} onChange={(e) => setK({ ...k, scopes: e.target.checked ? [...k.scopes, p.code] : k.scopes.filter((c: string) => c !== p.code) })} />{p.description}</label>)}</div></Field>
      </Modal>}
      {h && <Modal title={h.id ? h.name : 'New webhook'} onClose={() => setH(null)} footer={<><button onClick={() => setH(null)}>Cancel</button><button className="primary" disabled={busy} onClick={saveHook}>Save</button></>}>
        <Field label="Name"><input value={h.name} onChange={(e) => setH({ ...h, name: e.target.value })} /></Field>
        <Field label="Endpoint URL (HTTPS)"><input value={h.url} onChange={(e) => setH({ ...h, url: e.target.value })} /></Field>
        <Field label="Events">{events.data?.map((ev) => <label key={ev} className="check"><input type="checkbox" checked={h.events.includes(ev)} onChange={(e) => setH({ ...h, events: e.target.checked ? [...h.events, ev] : h.events.filter((x: string) => x !== ev) })} /><span className="mono">{ev}</span></label>)}</Field>
        <label className="check"><input type="checkbox" checked={h.active} onChange={(e) => setH({ ...h, active: e.target.checked })} />Active</label>
      </Modal>}
      {secret && <Modal title={secret.title} onClose={() => setSecret(null)} footer={<button className="primary" onClick={() => setSecret(null)}>I have stored it</button>}>
        <div className="alert warn">{secret.note}</div><input className="mono" readOnly value={secret.value} onFocus={(e) => e.target.select()} />
      </Modal>}
    </>
  )
}

// ------------------------------------------------------------------ audit
export function Audit() {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const { data } = useFetch<any>('/audit-log/', { search, page, page_size: 100 })
  const [verify, setVerify] = useState<any>(null)
  const [row, setRow] = useState<any>(null)
  return (
    <>
      <PageHead title="Audit Trail" sub="Append-only and hash-chained: every record includes the hash of the previous one, so any alteration is detectable."
        actions={<button onClick={async () => setVerify(await get('/audit-log/verify/'))}>Verify integrity</button>} />
      {verify && <div className={`alert ${verify.intact ? 'ok' : 'bad'}`} style={{ marginBottom: 12 }}>{verify.intact ? `✓ Chain intact — ${verify.entries} records verified.` : `✗ Chain broken at record #${verify.first_broken_id}. Investigate immediately.`}</div>}
      <div className="card">
        <div className="toolbar"><input placeholder="Search user, action, object…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} /><span style={{ flex: 1 }} /><button disabled={!data?.previous} onClick={() => setPage(page - 1)}>←</button><span>Page {page}</span><button disabled={!data?.next} onClick={() => setPage(page + 1)}>→</button></div>
        <DataTable rows={data?.results} onRow={setRow} cols={[{ key: 'at', title: 'Time', render: (x) => new Date(x.at).toLocaleString() }, { key: 'actor_label', title: 'User' }, { key: 'action', title: 'Action', render: (x) => <span className="badge">{x.action}</span> },
          { key: 'object_type', title: 'Object', render: (x) => <span className="mono">{x.object_type}</span> }, { key: 'object_repr', title: 'Record' }, { key: 'ip', title: 'IP' }]} />
      </div>
      {row && <Modal wide title={`${row.action} · ${row.object_repr}`} onClose={() => setRow(null)}>
        <dl className="kv"><dt>When</dt><dd>{new Date(row.at).toLocaleString()}</dd><dt>Who</dt><dd>{row.actor_label}</dd><dt>IP</dt><dd>{row.ip}</dd><dt>Request</dt><dd className="mono">{row.request_id}</dd><dt>Hash</dt><dd className="mono" style={{ wordBreak: 'break-all' }}>{row.hash}</dd></dl>
        <pre className="mono" style={{ background: 'var(--surface-2)', padding: 12, borderRadius: 8, overflow: 'auto', maxHeight: 400 }}>{JSON.stringify(row.changes, null, 2)}</pre>
      </Modal>}
    </>
  )
}
