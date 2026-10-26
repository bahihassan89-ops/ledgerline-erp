import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, get, post, session, setUnauthorizedHandler } from './api'

export type Company = {
  id: number; name: string; code: string; base_currency: string; level: number
  approval_limit: string | null; permissions: string[]; roles: { id: number; name: string; level: number }[]
}
export type Me = { id: number; username: string; email: string; full_name: string; mfa_enabled: boolean; is_superuser: boolean; must_change_password: boolean; trial?: boolean; companies: Company[]; global_permissions: string[] }

type Ctx = {
  me: Me | null; company: Company | null; loading: boolean
  can: (perm: string | string[]) => boolean
  canGlobal: (perm: string | string[]) => boolean
  reload: () => Promise<void>; logout: () => Promise<void>; switchCompany: (id: number) => void
  finishLogin: (tokens: { access: string; refresh: string }) => Promise<void>
}
const AuthCtx = createContext<Ctx>(null as any)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(() => session.hasRefresh())
  const [companyId, setCompanyId] = useState<number | null>(() => Number(session.company()) || null)

  const reload = useCallback(async () => {
    try {
      const m = await get<Me>('/auth/me/')
      setMe(m)
      const valid = m.companies.find((c) => c.id === companyId) || m.companies[0]
      if (valid) { session.setCompany(valid.id); setCompanyId(valid.id) }
    } catch { setMe(null) } finally { setLoading(false) }
  }, [companyId])

  const logout = useCallback(async () => {
    try { await post('/auth/logout/', { refresh: sessionStorage.getItem('erp.refresh') }) } catch { /* ignore */ }
    session.clear(); setMe(null)
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(() => { session.clear(); setMe(null) })
    // One-shot session restore on mount: reload() is async and only touches state after
    // awaiting the network call, so this isn't a synchronous cascading setState.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (session.hasRefresh()) reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // auto-logout after 30 minutes of inactivity
  useEffect(() => {
    if (!me) return
    let t: number
    const reset = () => { clearTimeout(t); t = window.setTimeout(() => logout(), 30 * 60 * 1000) }
    const evs = ['mousemove', 'keydown', 'click']
    evs.forEach((e) => window.addEventListener(e, reset))
    reset()
    return () => { clearTimeout(t); evs.forEach((e) => window.removeEventListener(e, reset)) }
  }, [me, logout])

  const company = me?.companies.find((c) => c.id === companyId) || me?.companies[0] || null
  const can = (perm: string | string[]) => {
    if (!company) return false
    const ps = Array.isArray(perm) ? perm : [perm]
    return ps.some((p) => company.permissions.includes(p))
  }
  const canGlobal = (perm: string | string[]) => {
    if (!me) return false
    if (me.is_superuser) return true
    const ps = Array.isArray(perm) ? perm : [perm]
    return ps.some((p) => me.global_permissions.includes(p))
  }
  const finishLogin = async (t: { access: string; refresh: string }) => { session.setTokens(t.access, t.refresh); await reload() }
  const switchCompany = (id: number) => { session.setCompany(id); setCompanyId(id); window.location.assign('/') }
  return <AuthCtx.Provider value={{ me, company, loading, can, canGlobal, reload, logout, switchCompany, finishLogin }}>{children}</AuthCtx.Provider>
}

export const useAuth = () => useContext(AuthCtx)
export { api }
