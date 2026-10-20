import QRCode from 'qrcode'
import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'

type Step = 'password' | 'mfa' | 'setup'

export default function Login() {
  const { finishLogin } = useAuth()
  const [step, setStep] = useState<Step>('password')
  const [username, setU] = useState('')
  const [password, setP] = useState('')
  const [code, setCode] = useState('')
  const [token, setToken] = useState('')
  const [secret, setSecret] = useState<{ secret: string; otpauth_uri: string } | null>(null)
  const [qr, setQr] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { if (secret) QRCode.toDataURL(secret.otpauth_uri, { width: 180, margin: 1 }).then(setQr) }, [secret])

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(''); setBusy(true)
    try {
      if (step === 'password') {
        const r = await api('/auth/login/', { method: 'POST', body: { username, password }, auth: false })
        if (r.mfa_required) { setToken(r.mfa_token); setStep('mfa') }
        else if (r.mfa_setup_required) {
          setToken(r.mfa_token)
          setSecret(await api('/auth/mfa/setup/', { method: 'POST', body: { mfa_token: r.mfa_token }, auth: false }))
          setStep('setup')
        } else await finishLogin(r)
      } else {
        const path = step === 'mfa' ? '/auth/mfa/verify/' : '/auth/mfa/confirm/'
        const r = await api(path, { method: 'POST', body: { mfa_token: token, code }, auth: false })
        await finishLogin(r)
      }
    } catch (e: any) { setErr(e.message); if (e.data?.error === 'invalid_token') { setStep('password'); setCode('') } }
    finally { setBusy(false) }
  }

  return (
    <div className="login-wrap">
      <div className="login-art">
        <div className="brand" style={{ padding: 0 }}><div className="brand-mark">L</div>Ledgerline ERP</div>
        <div>
          <h1>Accounting your auditors will trust.</h1>
          <p>Double-entry general ledger, receivables, payables, inventory costing and purchasing — with controls built into the database itself.</p>
          <ul>
            <li>Immutable, gap-free journal numbering</li>
            <li>Period close & lock, approval limits by level</li>
            <li>Tamper-evident audit trail</li>
          </ul>
        </div>
        <div style={{ fontSize: 12, color: '#8ea0c0' }}>Protected system. All activity is logged.</div>
      </div>
      <div className="login-panel">
        <form className="login-box" onSubmit={submit}>
          <div>
            <h1>{step === 'password' ? 'Sign in' : step === 'mfa' ? 'Two-factor authentication' : 'Set up two-factor authentication'}</h1>
            <div className="muted" style={{ marginTop: 6 }}>
              {step === 'password' && 'Use your company credentials.'}
              {step === 'mfa' && 'Enter the 6-digit code from your authenticator app.'}
              {step === 'setup' && 'Your role requires MFA. Scan this code with Google Authenticator, Microsoft Authenticator or 1Password, then enter the 6-digit code.'}
            </div>
          </div>
          {err && <div className="alert bad">{err}</div>}
          {step === 'password' && <>
            <label className="field">Username or email<input autoFocus autoComplete="username" value={username} onChange={(e) => setU(e.target.value)} required /></label>
            <label className="field">Password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setP(e.target.value)} required /></label>
          </>}
          {step === 'setup' && secret && <div style={{ textAlign: 'center' }}>
            {qr && <img src={qr} alt="MFA QR code" width={180} height={180} />}
            <div className="mono muted" style={{ marginTop: 8, wordBreak: 'break-all' }}>{secret.secret}</div>
          </div>}
          {step !== 'password' && <label className="field">Authentication code
            <input autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="\d{6}" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} required style={{ letterSpacing: '.4em', fontSize: 20, textAlign: 'center' }} />
          </label>}
          <button className="primary" disabled={busy} style={{ justifyContent: 'center', padding: 10 }}>{busy ? 'Please wait…' : step === 'password' ? 'Sign in' : 'Verify'}</button>
          {step !== 'password' && <button type="button" className="ghost" onClick={() => { setStep('password'); setCode(''); setErr('') }}>← Back</button>}
        </form>
      </div>
    </div>
  )
}
