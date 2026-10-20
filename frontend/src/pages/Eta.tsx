import { useState } from 'react'
import { DataTable, Field, Modal, PageHead, Tabs, useAction } from '../components/ui'
import { get, patch, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, money } from '../lib/format'
import { useFetch } from '../lib/hooks'

const TONE: Record<string, string> = { valid: 'ok', submitted: 'info', pending: 'warn', invalid: 'bad', rejected: 'bad', failed: 'bad', cancel_requested: 'warn', cancelled: '' }

export default function Eta() {
  const { can } = useAuth()
  const [tab, setTab] = useState('docs')
  const [state, setState] = useState('')
  const docs = useFetch<any>('/eta-documents/', { state, page_size: 200 })
  const received = useFetch<any[]>(tab === 'received' ? '/eta-documents/received/' : null)
  const [view, setView] = useState<any>(null)
  const { run, busy } = useAction()
  return (
    <>
      <PageHead title="E-Invoicing — منظومة الفاتورة الإلكترونية" sub="Customer invoices, credit and debit notes are signed and submitted to the Egyptian Tax Authority when posted."
        actions={can('einvoice.document.submit') && <button className="primary" disabled={busy} onClick={async () => { const r = await run(() => post('/eta-documents/submit-pending/')); if (r) { alert(`Submitted ${r.submitted}, refreshed ${r.refreshed}`); docs.reload() } }}>Submit pending & refresh</button>} />
      <Tabs tabs={[['docs', 'Submissions'], ['received', 'Received from vendors'], ...(can('einvoice.settings.manage') ? [['settings', 'Connection settings'] as [string, string]] : [])]} value={tab} onChange={setTab} />
      {tab === 'docs' && <div className="card">
        <div className="toolbar"><select value={state} onChange={(e) => setState(e.target.value)}><option value="">All states</option>{Object.keys(TONE).map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}</select></div>
        <DataTable rows={docs.data?.results} onRow={setView} empty="No e-invoices yet. Enable ETA in settings, then post customer invoices." cols={[
          { key: 'invoice_number', title: 'Document', render: (d) => <span className="mono">{d.invoice_number}</span> }, { key: 'document_type', title: 'Type', render: (d) => ({ I: 'Invoice', C: 'Credit note', D: 'Debit note' } as any)[d.document_type] },
          { key: 'partner', title: 'Receiver' }, { key: 'date', title: 'Date', render: (d) => fmtDate(d.date) }, { key: 'total', title: 'Total', num: true, render: (d) => money(d.total) },
          { key: 'state', title: 'ETA status', render: (d) => <span className={`badge ${TONE[d.state]}`}>{d.state.replace('_', ' ')}</span> },
          { key: 'uuid', title: 'UUID', render: (d) => d.url ? <a href={d.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="mono">{d.uuid.slice(0, 12)}…</a> : '—' }]} />
      </div>}
      {tab === 'received' && <div className="card">
        <div className="toolbar"><span className="muted">Vendor e-invoices issued to your tax number. Unmatched ones may be missing bills — and input VAT is only deductible on valid e-invoices.</span><span style={{ flex: 1 }} />
          {can('einvoice.document.submit') && <button onClick={async () => { await run(() => post('/eta-documents/received/'), 'Fetched'); received.reload() }}>Fetch from ETA</button>}</div>
        <DataTable rows={received.data} empty="Nothing fetched yet." cols={[{ key: 'date', title: 'Date', render: (r) => fmtDate(r.date?.slice(0, 10)) }, { key: 'issuer_name', title: 'Vendor' }, { key: 'issuer_id', title: 'Tax no.' }, { key: 'internal_id', title: 'Vendor doc no.' },
          { key: 'total', title: 'Total', num: true, render: (r) => money(r.total) }, { key: 'status', title: 'Status' }, { key: 'bill', title: 'Our bill', render: (r) => r.bill ? <span className="badge ok">{r.bill}</span> : <span className="badge warn">not recorded</span> }]} />
      </div>}
      {tab === 'settings' && <EtaSettings />}
      {view && <EtaDoc doc={view} onClose={() => { setView(null); docs.reload() }} />}
    </>
  )
}

function EtaDoc({ doc, onClose }: any) {
  const { data } = useFetch<any>(`/eta-documents/${doc.id}/payload/`)
  const [show, setShow] = useState<'json' | 'canonical'>('json')
  return (
    <Modal wide title={`${doc.invoice_number} — ETA ${doc.state}`} onClose={onClose}>
      {doc.errors?.length > 0 && <div className="alert bad"><pre className="mono" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{JSON.stringify(doc.errors, null, 1)}</pre></div>}
      <dl className="kv"><dt>UUID</dt><dd className="mono">{doc.uuid || '—'}</dd><dt>Submission</dt><dd className="mono">{doc.submission_uuid || '—'}</dd><dt>Attempts</dt><dd>{doc.attempts}</dd><dt>Submitted</dt><dd>{doc.submitted_at ? new Date(doc.submitted_at).toLocaleString() : '—'}</dd></dl>
      <div className="row"><button className={`sm ${show === 'json' ? 'primary' : ''}`} onClick={() => setShow('json')}>Document JSON</button><button className={`sm ${show === 'canonical' ? 'primary' : ''}`} onClick={() => setShow('canonical')}>Canonical (signed) string</button></div>
      {data && <pre className="mono" style={{ background: 'var(--surface-2)', padding: 12, borderRadius: 8, maxHeight: 420, overflow: 'auto', whiteSpace: show === 'canonical' ? 'pre-wrap' : 'pre', wordBreak: 'break-all' }}>{show === 'json' ? JSON.stringify(data.document, null, 2) : data.canonical}</pre>}
    </Modal>
  )
}

function EtaSettings() {
  const { data, setData } = useFetch<any>('/eta-settings/')
  const [secret, setSecret] = useState('')
  const [signerToken, setSignerToken] = useState('')
  const { run, busy } = useAction()
  if (!data) return null
  const set = (k: string, v: any) => setData({ ...data, [k]: v })
  const save = async () => {
    const { has_secret, ...b } = data; void has_secret
    const r = await run(() => patch('/eta-settings/', { ...b, ...(secret ? { client_secret: secret } : {}), ...(signerToken ? { signer_token: signerToken } : {}) }), 'Settings saved')
    if (r) { setData(r); setSecret(''); setSignerToken('') }
  }
  const test = async () => { await run(async () => { await post('/eta-documents/submit-pending/'); return get('/eta-documents/', { state: 'failed' }) }, 'Connection attempted — check document statuses') }
  return (
    <div className="card card-body stack">
      <div className="alert info">Register the ERP on the ETA portal (Taxpayer profile → ERP systems) to obtain the client ID and secret. Start in <b>pre-production</b>; move to production only after ETA confirms your test documents.</div>
      <label className="check"><input type="checkbox" checked={data.enabled} onChange={(e) => set('enabled', e.target.checked)} /><b>Enable e-invoicing for this company</b> (posting a customer invoice will validate and queue it)</label>
      <div className="grid form">
        <Field label="Environment"><select value={data.environment} onChange={(e) => set('environment', e.target.value)}><option value="preprod">Pre-production (testing)</option><option value="prod">Production</option></select></Field>
        <Field label="Client ID"><input value={data.client_id} onChange={(e) => set('client_id', e.target.value)} /></Field>
        <Field label="Client secret" hint={data.has_secret ? 'Stored (encrypted). Type to replace.' : 'Not set'}><input type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} /></Field>
        <Field label="Taxpayer activity code" hint="As registered with ETA"><input value={data.taxpayer_activity_code} onChange={(e) => set('taxpayer_activity_code', e.target.value)} /></Field>
        <Field label="Branch ID" hint="0 = head office"><input value={data.branch_id} onChange={(e) => set('branch_id', e.target.value)} /></Field>
      </div>
      <h3>Issuer address (as registered)</h3>
      <div className="grid form">{[['governate', 'Governorate'], ['region_city', 'City / region'], ['street', 'Street'], ['building_number', 'Building no.'], ['postal_code', 'Postal code'], ['floor', 'Floor'], ['room', 'Room'], ['landmark', 'Landmark']].map(([k, l]) =>
        <Field key={k} label={l}><input value={data[k]} onChange={(e) => set(k, e.target.value)} /></Field>)}</div>
      <h3>Signing</h3>
      <div className="grid form">
        <Field label="Signing method"><select value={data.signer_mode} onChange={(e) => set('signer_mode', e.target.value)}><option value="service">Signing agent with USB token / eSeal</option><option value="unsigned_preprod">Unsigned v0.9 (pre-production only)</option></select></Field>
        {data.signer_mode === 'service' && <><Field label="Signing agent URL" hint="HTTPS endpoint on the PC where the token is plugged in"><input value={data.signer_url} onChange={(e) => set('signer_url', e.target.value)} /></Field>
          <Field label="Signing agent token"><input type="password" autoComplete="new-password" value={signerToken} onChange={(e) => setSignerToken(e.target.value)} /></Field></>}
      </div>
      <h3>Rules (confirm with your tax advisor)</h3>
      <div className="grid form">
        <Field label="Individuals: national ID required from (EGP)"><input className="num" value={data.person_id_threshold} onChange={(e) => set('person_id_threshold', e.target.value)} /></Field>
        <Field label="Warn when not accepted after (days)"><input className="num" value={data.max_submission_days} onChange={(e) => set('max_submission_days', e.target.value)} /></Field>
      </div>
      <label className="check"><input type="checkbox" checked={data.auto_submit} onChange={(e) => set('auto_submit', e.target.checked)} />Submit automatically right after posting (otherwise the background worker submits)</label>
      <div className="row"><button className="primary" disabled={busy} onClick={save}>Save</button><button onClick={test}>Submit pending now</button></div>
    </div>
  )
}
