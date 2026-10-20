import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AccountSelect, CostCenterSelect, CurrencySelect, JournalSelect, PartnerSelect } from '../components/pickers'
import { DataTable, Field, Modal, PageHead, StateBadge, useAction } from '../components/ui'
import { del, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, label, money, num, today } from '../lib/format'
import { useFetch, useOptions } from '../lib/hooks'

export default function JournalEntries() {
  const [sp, setSp] = useSearchParams()
  const [q, setQ] = useState('')
  const state = sp.get('state') || ''
  const [page, setPage] = useState(1)
  const { data, reload } = useFetch<any>('/journal-entries/', { state, search: q, page, page_size: 50 })
  const [open, setOpen] = useState<any>(null)
  const { can, company } = useAuth()
  return (
    <>
      <PageHead title="Journal Entries" sub="Every posting in the general ledger. Posted entries are permanent; corrections are made by reversal."
        actions={can('ledger.entry.create') && <button className="primary" onClick={() => setOpen({})}>+ New journal entry</button>} />
      <div className="card">
        <div className="toolbar">
          <input placeholder="Search number, reference, narration…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1) }} />
          <select value={state} onChange={(e) => { setSp(e.target.value ? { state: e.target.value } : {}); setPage(1) }}>
            <option value="">All states</option><option value="draft">Draft</option><option value="to_approve">Waiting approval</option><option value="approved">Approved</option><option value="posted">Posted</option>
          </select>
          <span className="muted">{data?.count ?? 0} entries</span>
        </div>
        <DataTable rows={data?.results} onRow={(r) => setOpen(r)} cols={[
          { key: 'date', title: 'Date', render: (r) => fmtDate(r.date) },
          { key: 'number', title: 'Number', render: (r) => <span className="mono">{r.number || '—'}</span> },
          { key: 'journal_code', title: 'Journal' },
          { key: 'reference', title: 'Reference' },
          { key: 'narration', title: 'Narration', render: (r) => <span title={r.narration}>{r.narration?.slice(0, 60)}</span> },
          { key: 'source_type', title: 'Source', render: (r) => r.source_type ? <span className="badge">{label(r.source_type)}</span> : <span className="muted">Manual</span> },
          { key: 'total', title: `Amount (${company?.base_currency})`, num: true, render: (r) => money(r.state === 'posted' ? r.total_debit : r.lines.reduce((s: number, l: any) => s + num(l.debit), 0)) },
          { key: 'state', title: 'State', render: (r) => <>{<StateBadge state={r.state} />} {r.reversed_by && <span className="badge warn">Reversed</span>} {r.reversal_of && <span className="badge">Reversal</span>}</> },
        ]} />
        {data && data.count > 50 && <div className="toolbar" style={{ borderTop: '1px solid var(--border)', borderBottom: 0 }}>
          <button disabled={!data.previous} onClick={() => setPage(page - 1)}>← Prev</button><span>Page {page}</span><button disabled={!data.next} onClick={() => setPage(page + 1)}>Next →</button></div>}
      </div>
      {open && <EntryEditor entry={open.id ? open : null} onClose={() => setOpen(null)} onChanged={() => { reload() }} />}
    </>
  )
}

type Line = { account: number | null; partner: number | null; description: string; debit: string; credit: string; cost_center: number | null }
const blank = (): Line => ({ account: null, partner: null, description: '', debit: '', credit: '', cost_center: null })

export function EntryEditor({ entry, onClose, onChanged }: { entry: any; onClose: () => void; onChanged: () => void }) {
  const { can, company } = useAuth()
  const [e, setE] = useState<any>(entry)
  const editable = !e || (e.state === 'draft' && !e.source_type)
  const [h, setH] = useState<any>(() => ({ journal: e?.journal ?? null, date: e?.date ?? today(), reference: e?.reference ?? '', narration: e?.narration ?? '', currency: e?.currency ?? company?.base_currency, rate: e && e.currency !== company?.base_currency ? e.rate : '' }))
  const [lines, setLines] = useState<Line[]>(() => e ? e.lines.map((l: any) => ({ account: l.account, partner: l.partner, description: l.description, cost_center: l.cost_center,
    debit: num(l.debit) ? String(num(e.currency === company?.base_currency ? l.debit : l.amount_currency)) : '', credit: num(l.credit) ? String(Math.abs(num(e.currency === company?.base_currency ? l.credit : l.amount_currency))) : '' })) : [blank(), blank()])
  const accounts = useOptions<any>('/accounts/')
  const accById = useMemo(() => Object.fromEntries(accounts.map((a) => [a.id, a])), [accounts])
  const { run, busy } = useAction()
  const totD = lines.reduce((s, l) => s + num(l.debit), 0)
  const totC = lines.reduce((s, l) => s + num(l.credit), 0)
  const diff = Math.round((totD - totC) * 10000) / 10000
  const setLine = (i: number, k: keyof Line, v: any) => setLines((ls) => ls.map((l, j) => j === i ? { ...l, [k]: v, ...(k === 'debit' && v ? { credit: '' } : {}), ...(k === 'credit' && v ? { debit: '' } : {}) } : l))
  const foreign = h.currency && h.currency !== company?.base_currency

  const body = () => ({ ...h, rate: foreign && h.rate ? h.rate : undefined, lines: lines.filter((l) => l.account && (num(l.debit) || num(l.credit))).map((l) => ({ ...l, debit: l.debit || '0', credit: l.credit || '0' })) })
  const save = async () => {
    const r = await run(() => e ? put(`/journal-entries/${e.id}/`, body()) : post('/journal-entries/', body()), 'Draft saved')
    if (r) { setE(r); onChanged() }
    return r
  }
  const doPost = async () => {
    const saved = editable ? await save() : e
    if (!saved) return
    const r = await run(() => post(`/journal-entries/${saved.id}/post/`), 'Posted')
    if (r) { setE(r); onChanged() }
  }
  const reverse = async () => {
    const reason = window.prompt('Reason for reversal (recorded in the audit trail):')
    if (!reason) return
    const r = await run(() => post(`/journal-entries/${e.id}/reverse/`, { reason }), 'Reversal posted')
    if (r) { onChanged(); setE(r) }
  }
  const remove = async () => { if (window.confirm('Delete this draft?') && await run(() => del(`/journal-entries/${e.id}/`), 'Draft deleted') !== undefined) { onChanged(); onClose() } }

  return (
    <Modal wide title={<>{e?.number || (e ? `Draft entry #${e.id}` : 'New journal entry')} {e && <StateBadge state={e.state} />}</>} onClose={onClose}
      footer={<>
        {e && editable && can('ledger.entry.create') && <button className="danger" onClick={remove}>Delete draft</button>}
        <span style={{ flex: 1 }} />
        {e?.state === 'posted' && !e.reversed_by && !e.reversal_of && !e.source_type && can('ledger.entry.reverse') && <button onClick={reverse}>Reverse entry</button>}
        {editable && can('ledger.entry.create') && <button disabled={busy} onClick={save}>Save draft</button>}
        {(editable || e?.state === 'approved') && can('ledger.entry.post') && <button className="primary" disabled={busy || (editable && (diff !== 0 || totD === 0))} onClick={doPost}>Post entry</button>}
      </>}>
      {e?.state === 'to_approve' && <div className="alert warn">This entry exceeds an approval threshold and is waiting in the Approvals inbox.</div>}
      {e?.source_type && <div className="alert info">Generated automatically by a {label(e.source_type)} — change the source document instead.</div>}
      {e?.reversal_of_number && <div className="alert info">This is the reversal of {e.reversal_of_number}.</div>}
      {e?.reversed_by && <div className="alert warn">Reversed by {e.reversed_by.number}.</div>}
      <div className="grid form">
        <Field label="Journal"><JournalSelect types={['general', 'bank', 'cash']} value={h.journal} onChange={(v) => setH({ ...h, journal: v })} disabled={!editable} /></Field>
        <Field label="Date"><input type="date" value={h.date} onChange={(ev) => setH({ ...h, date: ev.target.value })} disabled={!editable} /></Field>
        <Field label="Currency"><CurrencySelect value={h.currency} onChange={(v) => setH({ ...h, currency: v })} disabled={!editable} /></Field>
        {foreign && <Field label={`Rate (1 ${h.currency} = ? ${company?.base_currency})`} hint="Leave empty to use the rate table"><input className="num" value={h.rate} onChange={(ev) => setH({ ...h, rate: ev.target.value })} disabled={!editable} /></Field>}
        <Field label="Reference"><input value={h.reference} onChange={(ev) => setH({ ...h, reference: ev.target.value })} disabled={!editable} /></Field>
      </div>
      <Field label="Narration (explain the entry for auditors)"><textarea rows={2} value={h.narration} onChange={(ev) => setH({ ...h, narration: ev.target.value })} disabled={!editable} /></Field>
      <div className="card">
        <div className="table-wrap">
          <table className="data lines">
            <thead><tr><th style={{ width: '30%' }}>Account</th><th>Partner</th><th>Description</th><th>Cost center</th><th className="num">Debit</th><th className="num">Credit</th><th /></tr></thead>
            <tbody>{lines.map((l, i) => {
              const a = l.account ? accById[l.account] : null
              return (
                <tr key={i}>
                  <td><AccountSelect value={l.account} onChange={(v) => setLine(i, 'account', v)} disabled={!editable} /></td>
                  <td>{(a?.reconcilable || l.partner) ? <PartnerSelect value={l.partner} onChange={(v) => setLine(i, 'partner', v)} disabled={!editable} allowEmpty /> : <span className="muted">—</span>}</td>
                  <td><input value={l.description} onChange={(ev) => setLine(i, 'description', ev.target.value)} disabled={!editable} /></td>
                  <td><CostCenterSelect value={l.cost_center} onChange={(v) => setLine(i, 'cost_center', v)} disabled={!editable} /></td>
                  <td className="num"><input className="num" inputMode="decimal" value={l.debit} onChange={(ev) => setLine(i, 'debit', ev.target.value.replace(/[^\d.]/g, ''))} disabled={!editable} /></td>
                  <td className="num"><input className="num" inputMode="decimal" value={l.credit} onChange={(ev) => setLine(i, 'credit', ev.target.value.replace(/[^\d.]/g, ''))} disabled={!editable} /></td>
                  <td>{editable && lines.length > 2 && <button className="sm ghost" onClick={() => setLines(lines.filter((_, j) => j !== i))} aria-label="Remove line">✕</button>}</td>
                </tr>)
            })}</tbody>
          </table>
        </div>
        <div className="balance-bar">
          {editable && <button className="sm" style={{ marginRight: 'auto' }} onClick={() => setLines([...lines, blank()])}>+ Add line</button>}
          <span>Total debit <b>{money(totD)}</b></span><span>Total credit <b>{money(totC)}</b></span>
          <span className={diff === 0 && totD ? 'ok' : 'bad'}>{diff === 0 && totD ? '✓ Balanced' : `Out of balance ${money(diff)}`}</span>
        </div>
      </div>
      {e?.state === 'posted' && <div className="kv"><dt>Posted by</dt><dd>{e.posted_by_name} · {new Date(e.posted_at).toLocaleString()}</dd><dt>Prepared by</dt><dd>{e.created_by_name}</dd>
        {foreign && <><dt>Base amount</dt><dd>{money(e.total_debit, company?.base_currency)} at rate {num(e.rate)}</dd></>}</div>}
    </Modal>
  )
}

export function EntryById({ id, onClose }: { id: number; onClose: () => void }) {
  const { data } = useFetch<any>(`/journal-entries/${id}/`)
  return data ? <EntryEditor entry={data} onClose={onClose} onChanged={() => {}} /> : null
}
