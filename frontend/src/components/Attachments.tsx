import { useRef } from 'react'
import { del, downloadAuthed, upload } from '../lib/api'
import { useFetch } from '../lib/hooks'
import { useAction } from './ui'

/** Supporting documents for any record (invoice scan, contract, customs declaration...). */
export default function Attachments({ type, id, canEdit = true }: { type: string; id: number; canEdit?: boolean }) {
  const { data, reload } = useFetch<any[]>(`/attachments/`, { object_type: type, object_id: id })
  const file = useRef<HTMLInputElement>(null)
  const { run } = useAction()
  const onPick = async (f: File | undefined) => {
    if (!f) return
    const form = new FormData()
    form.append('object_type', type); form.append('object_id', String(id)); form.append('file', f)
    if (await run(() => upload('/attachments/', form), 'Attached')) reload()
    if (file.current) file.current.value = ''
  }
  return (
    <div className="card">
      <div className="card-head"><h3>Attachments</h3><span className="muted">PDF, images, Excel, Word, CSV · max 10 MB</span><span style={{ flex: 1 }} />
        {canEdit && <><input ref={file} type="file" style={{ display: 'none' }} accept=".pdf,.png,.jpg,.jpeg,.xlsx,.xls,.docx,.csv,.txt" onChange={(e) => onPick(e.target.files?.[0])} />
          <button className="sm" onClick={() => file.current?.click()}>+ Attach file</button></>}
      </div>
      {!data?.length ? <div className="empty" style={{ padding: 16 }}>No attachments.</div> :
        <table className="data"><tbody>{data.map((a) => (
          <tr key={a.id}><td>📎 {a.original_name}</td><td className="muted">{(a.size / 1024).toFixed(0)} KB · {a.uploaded_by_name} · {new Date(a.uploaded_at).toLocaleDateString()}</td>
            <td className="mono muted" title="SHA-256 fingerprint">{a.sha256.slice(0, 12)}…</td>
            <td className="right"><button className="sm ghost" onClick={() => run(() => downloadAuthed(`/attachments/${a.id}/download/`, a.original_name))}>Download</button>
              {canEdit && <button className="sm ghost" onClick={async () => { if (window.confirm('Remove this attachment?') && await run(() => del(`/attachments/${a.id}/`), 'Removed') !== undefined) reload() }}>Remove</button>}</td></tr>))}
        </tbody></table>}
    </div>
  )
}
