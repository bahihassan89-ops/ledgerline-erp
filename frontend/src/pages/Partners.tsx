import { useState } from 'react'
import { CompanySelect, CurrencySelect, SimpleSelect, WhtSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, StateBadge, useAction } from '../components/ui'
import { patch, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, money } from '../lib/format'
import { invalidateOptions, useFetch } from '../lib/hooks'

const BANK_CHANGE_REASONS: [string, string][] = [
  ['vendor_requested', 'Vendor/customer requested the change'],
  ['bank_migration', 'Bank migration / account consolidation'],
  ['correction', 'Correcting a data-entry error'],
  ['new_vendor_setup', 'Initial setup for a new vendor/customer'],
  ['other', 'Other (explain below)'],
]

export default function Partners({ kind }: { kind: 'customer' | 'vendor' }) {
  const { can } = useAuth()
  const [search, setSearch] = useState('')
  const { data, error, reload } = useFetch<any>('/partners/', { [`is_${kind}`]: true, search, page_size: 200 })
  const [edit, setEdit] = useState<any>(null)
  const [stmt, setStmt] = useState<any>(null)
  return (
    <>
      <PageHead title={kind === 'customer' ? 'Customers' : 'Vendors'} actions={can('partners.partner.manage') && <button className="primary" onClick={() => setEdit({ [`is_${kind}`]: true })}>+ New {kind}</button>} />
      {error && <div className="alert bad">{error}</div>}
      <div className="card">
        <div className="toolbar"><input placeholder="Search code, name, email, tax no…" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
        <DataTable rows={data?.results} onRow={setEdit} cols={[
          { key: 'code', title: 'Code', render: (r) => <span className="mono">{r.code}</span> },
          { key: 'name', title: 'Name', render: (r) => <>{r.name} {r.on_hold && <span className="badge bad">On hold</span>} {!r.active && <span className="badge">Inactive</span>}</> },
          { key: 'tax_number', title: 'Tax no.' }, { key: 'email', title: 'Email' }, { key: 'country', title: 'Country' },
          ...(kind === 'customer' ? [{ key: 'credit_limit', title: 'Credit limit', num: true, render: (r: any) => r.credit_check ? money(r.credit_limit) : <span className="muted">no check</span> }] : []),
          { key: 's', title: '', render: (r) => <button className="sm ghost" onClick={(e) => { e.stopPropagation(); setStmt(r) }}>Statement</button> },
        ]} />
      </div>
      {edit && <PartnerForm initial={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); invalidateOptions(); reload() }} />}
      {stmt && <Statement partner={stmt} onClose={() => setStmt(null)} />}
    </>
  )
}

function PartnerForm({ initial, onClose, onSaved }: any) {
  const { can } = useAuth()
  const [f, setF] = useState<any>({ code: '', name: '', name_ar: '', is_customer: false, is_vendor: false, tax_number: '', email: '', phone: '', address: '', country: 'EG', currency: null, payment_term: null, credit_limit: '0', credit_check: false, on_hold: false, notes: '', active: true, withholding_tax: null, eta_receiver_type: 'B', national_id: '', governate: '', region_city: '', street: '', building_number: '', postal_code: '', is_intercompany: false, linked_company: null, ...initial })
  const { run, busy } = useAction()
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const ro = !can('partners.partner.manage')
  const credit = can('partners.credit_limit.manage')
  const save = async () => { const { id, ...b } = f; const r = await run(() => (id ? patch(`/partners/${id}/`, b) : post('/partners/', b)), 'Saved'); if (r) onSaved() }
  return (
    <Modal title={f.id ? f.name : 'New partner'} onClose={onClose} footer={!ro && <><button onClick={onClose}>Cancel</button><button className="primary" disabled={busy} onClick={save}>Save</button></>}>
      <div className="grid form">
        <Field label="Code"><input className="mono" value={f.code} onChange={(e) => set('code', e.target.value)} disabled={ro} /></Field>
        <Field label="Tax / VAT number"><input value={f.tax_number} onChange={(e) => set('tax_number', e.target.value)} disabled={ro} /></Field>
      </div>
      <Field label="Name"><input value={f.name} onChange={(e) => set('name', e.target.value)} disabled={ro} /></Field>
      <Field label="الاسم بالعربية"><input dir="rtl" value={f.name_ar} onChange={(e) => set('name_ar', e.target.value)} disabled={ro} /></Field>
      <div className="row"><label className="check"><input type="checkbox" checked={f.is_customer} onChange={(e) => set('is_customer', e.target.checked)} disabled={ro} />Customer</label>
        <label className="check"><input type="checkbox" checked={f.is_vendor} onChange={(e) => set('is_vendor', e.target.checked)} disabled={ro} />Vendor</label>
        <label className="check"><input type="checkbox" checked={f.active} onChange={(e) => set('active', e.target.checked)} disabled={ro} />Active</label></div>
      <div className="grid form">
        <Field label="Email"><input value={f.email} onChange={(e) => set('email', e.target.value)} disabled={ro} /></Field>
        <Field label="Phone"><input value={f.phone} onChange={(e) => set('phone', e.target.value)} disabled={ro} /></Field>
        <Field label="Country (ISO)"><input maxLength={2} value={f.country} onChange={(e) => set('country', e.target.value.toUpperCase())} disabled={ro} /></Field>
        <Field label="Default currency"><CurrencySelect allowEmpty value={f.currency} onChange={(v) => set('currency', v)} disabled={ro} /></Field>
        <Field label="Payment terms"><SimpleSelect path="/payment-terms/" labelOf={(t) => t.name} value={f.payment_term} onChange={(v) => set('payment_term', v)} disabled={ro} /></Field>
      </div>
      <Field label="Address"><textarea rows={2} value={f.address} onChange={(e) => set('address', e.target.value)} disabled={ro} /></Field>
      <div className="card card-body stack" style={{ gap: 10 }}>
        <h3>Egyptian tax & e-invoicing</h3>
        <div className="grid form">
          <Field label={f.is_vendor && !f.is_customer ? 'Withholding we deduct (خصم وإضافة)' : 'Withholding deducted by this customer'}><WhtSelect scope={f.is_vendor && !f.is_customer ? 'purchase' : 'sale'} value={f.withholding_tax} onChange={(v) => set('withholding_tax', v)} disabled={ro} /></Field>
          <Field label="ETA receiver type"><select value={f.eta_receiver_type} onChange={(e) => set('eta_receiver_type', e.target.value)} disabled={ro}><option value="B">B — Business (tax reg. no.)</option><option value="P">P — Individual (national ID)</option><option value="F">F — Foreign</option></select></Field>
          {f.eta_receiver_type === 'P' && <Field label="National ID (14 digits)"><input value={f.national_id} onChange={(e) => set('national_id', e.target.value)} disabled={ro} /></Field>}
          <Field label="Governorate"><input value={f.governate} onChange={(e) => set('governate', e.target.value)} disabled={ro} /></Field>
          <Field label="City / region"><input value={f.region_city} onChange={(e) => set('region_city', e.target.value)} disabled={ro} /></Field>
          <Field label="Street"><input value={f.street} onChange={(e) => set('street', e.target.value)} disabled={ro} /></Field>
          <Field label="Building no."><input value={f.building_number} onChange={(e) => set('building_number', e.target.value)} disabled={ro} /></Field>
          <Field label="Postal code"><input value={f.postal_code} onChange={(e) => set('postal_code', e.target.value)} disabled={ro} /></Field>
        </div>
      </div>
      <div className="card card-body stack" style={{ gap: 10 }}>
        <h3>Credit control {!credit && <span className="muted" style={{ fontWeight: 400 }}>(requires credit-limit permission)</span>}</h3>
        <div className="grid form">
          <Field label="Credit limit (base currency)"><input className="num" value={f.credit_limit} onChange={(e) => set('credit_limit', e.target.value)} disabled={!credit} /></Field>
        </div>
        <label className="check"><input type="checkbox" checked={f.credit_check} onChange={(e) => set('credit_check', e.target.checked)} disabled={!credit} />Block sales orders above the credit limit</label>
        <label className="check"><input type="checkbox" checked={f.on_hold} onChange={(e) => set('on_hold', e.target.checked)} disabled={!credit} />On hold (no new orders)</label>
      </div>
      <div className="card card-body stack" style={{ gap: 10 }}>
        <h3>Intercompany</h3>
        <label className="check"><input type="checkbox" checked={f.is_intercompany} onChange={(e) => set('is_intercompany', e.target.checked)} disabled={ro} />This partner is another company in our group</label>
        {f.is_intercompany && <Field label="Linked company" hint="Transactions against this partner post as ordinary sales/purchases -- see the Intercompany reconciliation report for matching the two sides; nothing is eliminated automatically.">
          <CompanySelect value={f.linked_company} onChange={(v) => set('linked_company', v)} disabled={ro} />
        </Field>}
      </div>
      {f.id && <BankChangePanel partner={f} />}
    </Modal>
  )
}

/** Bank details are read-only here by design (see PartnerSerializer) -- the only way to change them
 * is this maker-checker + independent-callback-verification workflow (item #34): request (maker) ->
 * apps.approvals generic decide() (checker, see the Approvals inbox) -> verify (independent phone
 * callback, cannot be the requester) -> apply (copies the proposed values onto the partner, cannot
 * be the requester's own unverified request). */
function BankChangePanel({ partner }: { partner: any }) {
  const { can } = useAuth()
  const { data, reload } = useFetch<any>('/partner-bank-change-requests/', { partner: partner.id, page_size: 50 })
  const [showNew, setShowNew] = useState(false)
  const [verifyReq, setVerifyReq] = useState<any>(null)
  const { run, busy } = useAction()
  const apply = async (req: any) => {
    if (!window.confirm(`Apply the verified bank details to ${partner.name}? This overwrites the bank details currently on file.`)) return
    const r = await run(() => post(`/partner-bank-change-requests/${req.id}/apply/`, {}), 'Bank details updated')
    if (r) reload()
  }
  return (
    <div className="card card-body stack" style={{ gap: 10 }}>
      <h3>Bank details <span className="muted" style={{ fontWeight: 400 }}>(maker-checker + independent callback verification required to change)</span></h3>
      <div className="grid form">
        <Field label="Bank name"><input value={partner.bank_name || ''} disabled /></Field>
        <Field label="Bank country"><input value={partner.bank_country || ''} disabled /></Field>
        <Field label="IBAN"><input className="mono" value={partner.iban || ''} disabled /></Field>
        <Field label="Account number"><input className="mono" value={partner.bank_account_number || ''} disabled /></Field>
        <Field label="SWIFT/BIC"><input className="mono" value={partner.swift_bic || ''} disabled /></Field>
      </div>
      {can('partners.partner.manage') && <button type="button" className="sm" disabled={busy} onClick={() => setShowNew(true)}>Request bank change</button>}
      {data?.results?.length > 0 && <DataTable rows={data.results} empty="No bank-change requests yet." cols={[
        { key: 'created_at', title: 'Requested', render: (r: any) => fmtDate(r.created_at) },
        { key: 'reason_code', title: 'Reason', render: (r: any) => BANK_CHANGE_REASONS.find(([k]) => k === r.reason_code)?.[1] || r.reason_code },
        { key: 'state', title: 'Status', render: (r: any) => <StateBadge state={r.state} /> },
        { key: 'verification', title: 'Verified', render: (r: any) => r.verification ? `Yes, ${fmtDate(r.verification.verified_at)}` : '—' },
        { key: 'a', title: '', render: (r: any) => <span className="row" style={{ gap: 6 }}>
          {r.state === 'approved' && !r.verification && can('partners.bank_change.verify') &&
            <button type="button" className="sm" onClick={() => setVerifyReq(r)}>Verify</button>}
          {r.state === 'approved' && r.verification && can('partners.partner.manage') &&
            <button type="button" className="sm primary" disabled={busy} onClick={() => apply(r)}>Apply</button>}
        </span> },
      ]} />}
      {showNew && <NewBankChangeForm partner={partner} onClose={() => setShowNew(false)} onSaved={() => { setShowNew(false); reload() }} />}
      {verifyReq && <VerifyBankChangeForm req={verifyReq} onClose={() => setVerifyReq(null)} onSaved={() => { setVerifyReq(null); reload() }} />}
    </div>
  )
}

function NewBankChangeForm({ partner, onClose, onSaved }: any) {
  const [f, setF] = useState<any>({ bank_name: '', bank_account_number: '', iban: '', swift_bic: '',
    bank_country: partner.bank_country || partner.country || '', reason_code: '', reason: '' })
  const { run, busy } = useAction()
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const save = async () => {
    const r = await run(() => post('/partner-bank-change-requests/', { partner: partner.id, ...f }),
      'Bank-change request submitted for approval')
    if (r) onSaved()
  }
  return (
    <Modal title={`Request bank change — ${partner.name}`} onClose={onClose} footer={<>
      <button onClick={onClose}>Cancel</button>
      <button className="primary" disabled={busy || !f.reason_code} onClick={save}>Submit for approval</button>
    </>}>
      <div className="alert info">This goes to maker-checker approval (see the Approvals inbox), then needs an
        independent phone-callback verification -- neither the approver nor the verifier can be the person who
        submitted this request -- before the partner's bank details on file actually change.</div>
      <div className="grid form">
        <Field label="Bank name"><input value={f.bank_name} onChange={(e) => set('bank_name', e.target.value)} /></Field>
        <Field label="Bank country (ISO)"><input maxLength={2} value={f.bank_country} onChange={(e) => set('bank_country', e.target.value.toUpperCase())} /></Field>
        <Field label="IBAN"><input className="mono" value={f.iban} onChange={(e) => set('iban', e.target.value)} /></Field>
        <Field label="Account number"><input className="mono" value={f.bank_account_number} onChange={(e) => set('bank_account_number', e.target.value)} /></Field>
        <Field label="SWIFT/BIC"><input className="mono" value={f.swift_bic} onChange={(e) => set('swift_bic', e.target.value)} /></Field>
      </div>
      <Field label="Reason">
        <select value={f.reason_code} onChange={(e) => set('reason_code', e.target.value)}>
          <option value="">Choose a reason…</option>
          {BANK_CHANGE_REASONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </Field>
      {f.reason_code === 'other' && <Field label="Explain"><textarea rows={2} value={f.reason} onChange={(e) => set('reason', e.target.value)} /></Field>}
    </Modal>
  )
}

function VerifyBankChangeForm({ req, onClose, onSaved }: any) {
  const [phone, setPhone] = useState('')
  const [notes, setNotes] = useState('')
  const { run, busy } = useAction()
  const save = async () => {
    const r = await run(() => post(`/partner-bank-change-requests/${req.id}/verify/`, { phone_called: phone, notes }), 'Verified')
    if (r) onSaved()
  }
  return (
    <Modal title="Verify bank-detail change" onClose={onClose} footer={<>
      <button onClick={onClose}>Cancel</button>
      <button className="primary" disabled={busy || !phone.trim()} onClick={save}>Confirm verified</button>
    </>}>
      <div className="alert warn">Call the partner back on a number already on file or from a known prior contact
        -- never a number given in this request -- and confirm the new bank details before verifying.</div>
      <Field label="Phone number called"><input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+20 1xx xxx xxxx" /></Field>
      <Field label="Notes"><textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
    </Modal>
  )
}

function Statement({ partner, onClose }: any) {
  const { data } = useFetch<any>(`/partners/${partner.id}/statement/`)
  return (
    <Modal wide title={`Statement — ${partner.name}`} onClose={onClose}>
      {data && <>
        <div className="row"><span>Opening balance <b>{money(data.opening_balance)}</b></span><span style={{ flex: 1 }} /><span>Closing balance <b>{money(data.closing_balance)}</b></span></div>
        <DataTable rows={data.rows} cols={[{ key: 'date', title: 'Date', render: (r) => fmtDate(r.date) }, { key: 'number', title: 'Entry' }, { key: 'description', title: 'Description' }, { key: 'due_date', title: 'Due', render: (r) => fmtDate(r.due_date) },
          { key: 'debit', title: 'Debit', num: true, render: (r) => money(r.debit) }, { key: 'credit', title: 'Credit', num: true, render: (r) => money(r.credit) }, { key: 'balance', title: 'Balance', num: true, render: (r) => money(r.balance) }]} />
        <div className="muted">{data.note}</div>
      </>}
    </Modal>
  )
}
