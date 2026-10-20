import QRCode from 'qrcode'
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { get } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fmtDate, money, num } from '../lib/format'

const TITLES: Record<string, [string, string]> = {
  out_invoice: ['Tax Invoice', 'فاتورة ضريبية'], out_refund: ['Credit Note', 'إشعار دائن'],
  in_invoice: ['Vendor Bill', 'فاتورة مورد'], in_refund: ['Debit Note', 'إشعار مدين'],
  sales: ['Sales Order', 'أمر بيع'], purchase: ['Purchase Order', 'أمر شراء'],
}
const PATHS: Record<string, string> = { invoices: '/invoices/', bills: '/bills/', sales: '/sales-orders/', purchase: '/purchase-orders/' }

/** A4 print layout; use the browser's "Save as PDF". */
export default function Print() {
  const { kind = 'invoices', id } = useParams()
  const { company } = useAuth()
  const [doc, setDoc] = useState<any>(null)
  const [co, setCo] = useState<any>(null)
  const [partner, setPartner] = useState<any>(null)
  const [qr, setQr] = useState('')
  useEffect(() => {
    get(`${PATHS[kind]}${id}/`).then(async (d) => {
      setDoc(d)
      setCo(await get('/company/current/'))
      setPartner(await get(`/partners/${d.partner}/`))
      if (d.eta?.url) setQr(await QRCode.toDataURL(d.eta.url, { width: 120, margin: 0 }))
    })
  }, [kind, id])
  useEffect(() => { if (doc && co && partner) setTimeout(() => window.print(), 400) }, [doc, co, partner])
  if (!doc || !co || !partner) return <div className="empty">Preparing document…</div>
  const [en, ar] = doc.move_type && doc.move_type === 'out_invoice' && doc.origin_invoice ? ['Debit Note', 'إشعار مدين'] : TITLES[doc.move_type || kind]
  const cur = doc.currency
  return (
    <div className="print-page">
      <style>{`
        @page { size: A4; margin: 14mm; }
        body { background: #fff; }
        .print-page { max-width: 820px; margin: 0 auto; padding: 24px; color: #111; font-size: 12.5px; }
        .ph { display: flex; justify-content: space-between; gap: 24px; border-bottom: 2px solid #111; padding-bottom: 12px; }
        .ph h1 { font-size: 22px; margin: 0; }
        .ar { direction: rtl; font-family: "Segoe UI", Tahoma, Arial, sans-serif; }
        .two { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin: 16px 0; }
        .box { border: 1px solid #bbb; border-radius: 6px; padding: 10px 12px; }
        .box h4 { margin: 0 0 6px; font-size: 11px; text-transform: uppercase; color: #555; display: flex; justify-content: space-between; }
        table.p { width: 100%; border-collapse: collapse; margin-top: 8px; }
        table.p th { background: #f0f0f0; text-align: left; font-size: 11px; padding: 6px; border: 1px solid #ccc; }
        table.p td { padding: 6px; border: 1px solid #ddd; vertical-align: top; }
        .n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
        .tot { width: 340px; margin-left: auto; margin-top: 10px; }
        .tot td { padding: 4px 6px; } .tot tr.g td { font-weight: 700; border-top: 2px solid #111; font-size: 14px; }
        .foot { margin-top: 28px; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px; font-size: 11px; }
        .sig { border-top: 1px solid #999; padding-top: 4px; margin-top: 40px; }
        @media print { .noprint { display: none; } .print-page { padding: 0; } }
      `}</style>
      <div className="noprint" style={{ marginBottom: 12 }}><button onClick={() => window.print()}>Print / Save as PDF</button></div>
      <div className="ph">
        <div><h1>{co.legal_name || co.name}</h1>{co.name_ar && <div className="ar" style={{ fontSize: 16, fontWeight: 600 }}>{co.name_ar}</div>}
          <div>{co.address}</div><div>Tax reg. no. / رقم التسجيل الضريبي: <b>{co.tax_number}</b></div>
          {co.registration_number && <div>Commercial reg. / السجل التجاري: {co.registration_number}</div>}{co.tax_office && <div>Tax office / المأمورية: {co.tax_office}</div>}</div>
        <div style={{ textAlign: 'right' }}><h1>{en}</h1><div className="ar" style={{ fontSize: 18, fontWeight: 700 }}>{ar}</div>
          <div style={{ marginTop: 6 }}>No. / رقم: <b>{doc.number || `DRAFT-${doc.id}`}</b></div><div>Date / التاريخ: {fmtDate(doc.date)}</div>
          {doc.due_date && <div>Due / الاستحقاق: {fmtDate(doc.due_date)}</div>}{doc.reference && <div>Reference / المرجع: {doc.reference}</div>}{doc.origin_number && <div>Ref. invoice: {doc.origin_number}</div>}</div>
      </div>
      <div className="two">
        <div className="box"><h4><span>{kind === 'invoices' || kind === 'sales' ? 'Bill to' : 'Vendor'}</span><span className="ar">{kind === 'invoices' || kind === 'sales' ? 'العميل' : 'المورد'}</span></h4>
          <b>{partner.name}</b>{partner.name_ar && <div className="ar">{partner.name_ar}</div>}
          <div>{[partner.street, partner.building_number, partner.region_city, partner.governate].filter(Boolean).join(', ') || partner.address}</div>
          {partner.tax_number && <div>Tax reg. no.: {partner.tax_number}</div>}{partner.national_id && <div>National ID: {partner.national_id}</div>}</div>
        <div className="box" style={{ display: 'flex', gap: 12, justifyContent: 'space-between' }}>
          <div><h4>Currency / العملة</h4>{cur}{doc.rate && num(doc.rate) !== 1 && <div>Rate: {num(doc.rate)}</div>}
            {doc.eta?.uuid && <><h4 style={{ marginTop: 8 }}>ETA e-invoice</h4><div style={{ fontSize: 10, wordBreak: 'break-all' }}>UUID: {doc.eta.uuid}</div><div>Status: {doc.eta.state}</div></>}</div>
          {qr && <img src={qr} width={110} height={110} alt="ETA QR" />}
        </div>
      </div>
      <table className="p"><thead><tr><th>#</th><th>Description / البيان</th><th className="n">Qty / الكمية</th><th className="n">Unit price / السعر</th><th className="n">Disc %</th><th className="n">VAT</th><th className="n">Net / الصافي</th></tr></thead>
        <tbody>{doc.lines.map((l: any, i: number) => <tr key={i}><td>{i + 1}</td><td>{l.description}{l.product_sku && <div style={{ color: '#666', fontSize: 11 }}>{l.product_sku}</div>}</td>
          <td className="n">{num(l.quantity)}</td><td className="n">{money(l.unit_price)}</td><td className="n">{num(l.discount_pct) || ''}</td><td className="n">{money(l.tax_amount)}</td><td className="n">{money(l.subtotal)}</td></tr>)}</tbody></table>
      <table className="tot"><tbody>
        <tr><td>Subtotal / الإجمالي قبل الضريبة</td><td className="n">{money(doc.amount_untaxed)}</td></tr>
        <tr><td>VAT / ضريبة القيمة المضافة</td><td className="n">{money(doc.amount_tax)}</td></tr>
        {num(doc.amount_wht) > 0 && <tr><td>Withholding tax / ضريبة الخصم والإضافة</td><td className="n">({money(doc.amount_wht)})</td></tr>}
        <tr className="g"><td>Total / الإجمالي</td><td className="n">{money(doc.amount_total, cur)}</td></tr>
        {doc.state === 'posted' && doc.amount_residual !== undefined && num(doc.amount_residual) !== num(doc.amount_total) && <tr><td>Amount due / المستحق</td><td className="n">{money(doc.amount_residual, cur)}</td></tr>}
      </tbody></table>
      {doc.notes && <p style={{ marginTop: 12 }}>{doc.notes}</p>}
      <div className="foot"><div className="sig">Prepared by / إعداد</div><div className="sig">Approved by / اعتماد</div><div className="sig">Received by / استلام</div></div>
      <p style={{ marginTop: 16, fontSize: 10, color: '#777' }}>{company?.name} · generated {new Date().toLocaleString()}</p>
    </div>
  )
}
