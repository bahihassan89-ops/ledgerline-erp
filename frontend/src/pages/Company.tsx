import { useState } from 'react'
import { DataTable, Field, Modal, PageHead, StateBadge, useAction } from '../components/ui'
import { get, post } from '../lib/api'
import { fmtDate, label } from '../lib/format'
import { useFetch } from '../lib/hooks'

const COMPANY_TYPES: [string, string][] = [
  ['manufacturing', 'Manufacturing'], ['trading', 'Trading'], ['services', 'Services'],
  ['agriculture', 'Agriculture'], ['other', 'Other'],
]
const STEPS: [string, string][] = [['basic', 'Basic info'], ['modules', 'Modules'], ['review', 'Review & submit']]

function blankForm() {
  return { name: '', name_ar: '', code: '', company_type: 'trading', country: '', currency: '', vat: '', fy_start_month: 1, year: new Date().getFullYear() }
}

// ------------------------------------------------------------------ provisioning wizard
export function CompanyProvisioning() {
  const { data, error, reload } = useFetch<any>('/company-provisioning/', { page_size: 500 })
  const rows = Array.isArray(data) ? data : data?.results
  const registry = useFetch<any[]>('/company-provisioning/module-registry/')
  const [wiz, setWiz] = useState<any>(null) // { company, step, form, modules }
  const [view, setView] = useState<any>(null)
  const { run, busy } = useAction()

  const startNew = () => setWiz({ company: null, step: 'basic', form: blankForm(), modules: null })
  const openRow = (c: any) => {
    if (c.state === 'pending_approval') { setView(c); return }
    setWiz({ company: c, step: 'modules', form: null, modules: null })
    get(`/company-provisioning/${c.id}/modules/`).then((m) => setWiz((w: any) => ({ ...w, modules: m })))
  }

  const createDraft = async () => {
    const body = { ...wiz.form, vat: wiz.form.vat === '' ? null : wiz.form.vat, currency: wiz.form.currency || null }
    const r = await run(() => post('/company-provisioning/', body), 'Draft company created')
    if (r) { reload(); const m = await get(`/company-provisioning/${r.id}/modules/`); setWiz({ company: r, step: 'modules', form: null, modules: m }) }
  }
  const saveModules = async () => {
    const codes = wiz.modules.filter((m: any) => m.enabled).map((m: any) => m.module_code)
    const r = await run(() => post(`/company-provisioning/${wiz.company.id}/modules/`, { module_codes: codes }), 'Modules saved')
    if (r) setWiz({ ...wiz, modules: r, step: 'review' })
  }
  const submit = async () => {
    const r = await run(() => post(`/company-provisioning/${wiz.company.id}/submit/`), 'Submitted for CEO approval')
    if (r) { setWiz(null); reload() }
  }
  const toggleModule = (code: string) => setWiz({ ...wiz, modules: wiz.modules.map((m: any) => m.module_code === code ? { ...m, enabled: !m.enabled } : m) })

  return (
    <>
      <PageHead title="Company Provisioning" sub="Configure a new company draft — chart of accounts, modules and team — then submit it for CEO approval before it goes live."
        actions={<button className="primary" onClick={startNew}>+ New company</button>} />
      {error && <div className="alert bad">{error}</div>}
      <div className="card"><DataTable rows={rows} onRow={openRow} empty="No companies in progress." cols={[
        { key: 'name', title: 'Name' }, { key: 'code', title: 'Code', render: (r) => <span className="mono">{r.code}</span> },
        { key: 'company_type', title: 'Type', render: (r) => label(r.company_type) }, { key: 'country', title: 'Country' },
        { key: 'state', title: 'State', render: (r) => <StateBadge state={r.state} /> },
        { key: 'created_at', title: 'Created', render: (r) => fmtDate(r.created_at.slice(0, 10)) },
      ]} /></div>

      {wiz && (
        <Modal wide title={wiz.company ? wiz.company.name : 'New company'} onClose={() => setWiz(null)}
          footer={<>
            <button onClick={() => setWiz(null)}>Cancel</button>
            {wiz.step === 'basic' && <button className="primary" disabled={busy || !wiz.form.name || !wiz.form.code} onClick={createDraft}>Create draft</button>}
            {wiz.step === 'modules' && wiz.modules && <button className="primary" disabled={busy} onClick={saveModules}>Save & continue</button>}
            {wiz.step === 'review' && <button className="primary" disabled={busy} onClick={submit}>Submit for CEO approval</button>}
          </>}>
          <div className="tabs" style={{ marginBottom: 12 }}>{STEPS.map(([k, l]) => <button key={k} className={wiz.step === k ? 'on' : ''} disabled>{l}</button>)}</div>

          {wiz.step === 'basic' && <div className="grid form">
            <Field label="Trading name"><input value={wiz.form.name} onChange={(e) => setWiz({ ...wiz, form: { ...wiz.form, name: e.target.value } })} /></Field>
            <Field label="الاسم التجاري بالعربية"><input value={wiz.form.name_ar} onChange={(e) => setWiz({ ...wiz, form: { ...wiz.form, name_ar: e.target.value } })} /></Field>
            <Field label="Code" hint="Short, unique, used in record numbering"><input value={wiz.form.code} onChange={(e) => setWiz({ ...wiz, form: { ...wiz.form, code: e.target.value } })} /></Field>
            <Field label="Company type"><select value={wiz.form.company_type} onChange={(e) => setWiz({ ...wiz, form: { ...wiz.form, company_type: e.target.value } })}>{COMPANY_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Country (ISO 2-letter)"><input value={wiz.form.country} onChange={(e) => setWiz({ ...wiz, form: { ...wiz.form, country: e.target.value.toUpperCase() } })} /></Field>
            <Field label="Base currency" hint="Defaults to EGP for Egypt, else USD"><input value={wiz.form.currency} onChange={(e) => setWiz({ ...wiz, form: { ...wiz.form, currency: e.target.value.toUpperCase() } })} /></Field>
            <Field label="VAT rate %" hint="Defaults to 14% for Egypt, else 15%"><input className="num" value={wiz.form.vat} onChange={(e) => setWiz({ ...wiz, form: { ...wiz.form, vat: e.target.value } })} /></Field>
            <Field label="Fiscal year start month"><select value={wiz.form.fy_start_month} onChange={(e) => setWiz({ ...wiz, form: { ...wiz.form, fy_start_month: Number(e.target.value) } })}>{Array.from({ length: 12 }, (_, i) => i + 1).map((m) => <option key={m} value={m}>{m}</option>)}</select></Field>
            <Field label="First fiscal year"><input className="num" value={wiz.form.year} onChange={(e) => setWiz({ ...wiz, form: { ...wiz.form, year: Number(e.target.value) } })} /></Field>
          </div>}

          {wiz.step === 'modules' && (!wiz.modules ? <div className="empty">Loading…</div> : <div className="grid two">
            {wiz.modules.map((m: any) => <label key={m.module_code} className="check"><input type="checkbox" checked={m.enabled} onChange={() => toggleModule(m.module_code)} />{m.description || label(m.module_code)} <span className="mono muted" style={{ fontSize: 11 }}>{m.module_code}</span></label>)}
            {!registry.data && <div className="muted">Loading module catalog…</div>}
          </div>)}

          {wiz.step === 'review' && <div className="stack">
            <div className="alert warn">Once submitted, this company waits for CEO approval before it becomes active and usable.</div>
            <dl className="kv">
              <dt>Name</dt><dd>{wiz.company.name}</dd>
              <dt>Code</dt><dd className="mono">{wiz.company.code}</dd>
              <dt>Type</dt><dd>{label(wiz.company.company_type)}</dd>
              <dt>Enabled modules</dt><dd>{(wiz.modules || []).filter((m: any) => m.enabled).map((m: any) => m.description || label(m.module_code)).join(', ')}</dd>
            </dl>
          </div>}
        </Modal>
      )}

      {view && <Modal title={view.name} onClose={() => setView(null)}>
        <dl className="kv">
          <dt>Code</dt><dd className="mono">{view.code}</dd>
          <dt>Type</dt><dd>{label(view.company_type)}</dd>
          <dt>State</dt><dd><StateBadge state={view.state} /></dd>
          <dt>Submitted</dt><dd>{view.submitted_at ? new Date(view.submitted_at).toLocaleString() : '—'}</dd>
        </dl>
        <div className="muted">Waiting for CEO approval — see the Company Approvals queue.</div>
      </Modal>}
    </>
  )
}

// ------------------------------------------------------------------ CEO approval queue
export function CompanyApprovals() {
  const { data, error, reload } = useFetch<any>('/company-approvals/', { page_size: 500 })
  const rows = Array.isArray(data) ? data : data?.results
  const [cur, setCur] = useState<any>(null)
  const { run, busy } = useAction()

  const approve = async () => { if (await run(() => post(`/company-approvals/${cur.id}/approve/`), `${cur.name} activated`)) { setCur(null); reload() } }
  const reject = async () => {
    const reason = window.prompt('Reason for rejection (sent back to the preparer):')
    if (!reason) return
    if (await run(() => post(`/company-approvals/${cur.id}/reject/`, { reason }), `${cur.name} rejected`)) { setCur(null); reload() }
  }

  return (
    <>
      <PageHead title="Company Approvals" sub="Companies fully configured and submitted by a Controller, waiting for final CEO sign-off before they go live." />
      {error && <div className="alert bad">{error}</div>}
      <div className="card"><DataTable rows={rows} onRow={setCur} empty="Nothing waiting for approval." cols={[
        { key: 'name', title: 'Name' }, { key: 'code', title: 'Code', render: (r) => <span className="mono">{r.code}</span> },
        { key: 'company_type', title: 'Type', render: (r) => label(r.company_type) }, { key: 'country', title: 'Country' },
        { key: 'submitted_at', title: 'Submitted', render: (r) => r.submitted_at ? new Date(r.submitted_at).toLocaleString() : '—' },
      ]} /></div>
      {cur && <Modal title={cur.name} onClose={() => setCur(null)}
        footer={<><button className="danger" disabled={busy} onClick={reject}>Reject</button><button className="primary" disabled={busy} onClick={approve}>Approve & activate</button></>}>
        <dl className="kv">
          <dt>Code</dt><dd className="mono">{cur.code}</dd>
          <dt>Legal name</dt><dd>{cur.legal_name}</dd>
          <dt>Type</dt><dd>{label(cur.company_type)}</dd>
          <dt>Country</dt><dd>{cur.country}</dd>
          <dt>Base currency</dt><dd>{cur.base_currency}</dd>
          <dt>Submitted</dt><dd>{cur.submitted_at ? new Date(cur.submitted_at).toLocaleString() : '—'}</dd>
        </dl>
      </Modal>}
    </>
  )
}
