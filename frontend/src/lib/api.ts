// API client. The access token lives only in memory; the refresh token in sessionStorage
// (cleared when the tab closes). Every request carries the selected company.

let accessToken: string | null = null
let refreshing: Promise<boolean> | null = null
const REFRESH_KEY = 'erp.refresh'
const COMPANY_KEY = 'erp.company'

export class ApiError extends Error {
  status: number
  data: any
  constructor(status: number, data: any) {
    super(extractMessage(data) || `Request failed (${status})`)
    this.status = status
    this.data = data
  }
}

export function extractMessage(data: any): string {
  if (!data) return ''
  if (typeof data === 'string') return data
  const parts: string[] = []
  if (data.detail && typeof data.detail === 'string') parts.push(data.detail)
  const errs = data.details?.errors
  if (Array.isArray(errs)) parts.push(...errs)
  if (!parts.length) {
    const walk = (v: any, prefix = '') => {
      if (Array.isArray(v)) v.forEach((x) => walk(x, prefix))
      else if (v && typeof v === 'object') Object.entries(v).forEach(([k, x]) => walk(x, k === 'non_field_errors' || k === 'detail' ? prefix : k))
      else if (v != null) parts.push(prefix ? `${prefix}: ${v}` : String(v))
    }
    walk(data.detail ?? data)
  }
  return parts.join('\n')
}

export const session = {
  setTokens(access: string, refresh: string) {
    accessToken = access
    sessionStorage.setItem(REFRESH_KEY, refresh)
  },
  clear() {
    accessToken = null
    sessionStorage.removeItem(REFRESH_KEY)
  },
  hasRefresh: () => !!sessionStorage.getItem(REFRESH_KEY),
  company: () => localStorage.getItem(COMPANY_KEY),
  setCompany: (id: number) => localStorage.setItem(COMPANY_KEY, String(id)),
}

async function refresh(): Promise<boolean> {
  const token = sessionStorage.getItem(REFRESH_KEY)
  if (!token) return false
  const r = await fetch('/api/auth/refresh/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh: token }),
  })
  if (!r.ok) { session.clear(); return false }
  const d = await r.json()
  session.setTokens(d.access, d.refresh)
  return true
}

let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn }

export async function api<T = any>(path: string, opts: { method?: string; body?: any; query?: Record<string, any>; auth?: boolean } = {}): Promise<T> {
  const url = new URL(path.startsWith('/api') ? path : `/api${path}`, window.location.origin)
  Object.entries(opts.query || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v)) })
  const doFetch = () => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (accessToken && opts.auth !== false) headers.Authorization = `Bearer ${accessToken}`
    const company = session.company()
    if (company) headers['X-Company'] = company
    return fetch(url.toString(), { method: opts.method || 'GET', headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined })
  }
  if (!accessToken && opts.auth !== false && session.hasRefresh()) {
    refreshing = refreshing || refresh().finally(() => { refreshing = null })
    await refreshing
  }
  let r = await doFetch()
  if (r.status === 401 && opts.auth !== false && session.hasRefresh()) {
    refreshing = refreshing || refresh().finally(() => { refreshing = null })
    if (await refreshing) r = await doFetch()
  }
  if (r.status === 401 && opts.auth !== false) onUnauthorized()
  const text = await r.text()
  const data = text ? JSON.parse(text) : null
  if (!r.ok) throw new ApiError(r.status, data)
  return data as T
}

export const get = <T = any>(p: string, query?: Record<string, any>) => api<T>(p, { query })
export const post = <T = any>(p: string, body?: any) => api<T>(p, { method: 'POST', body: body ?? {} })
export const patch = <T = any>(p: string, body: any) => api<T>(p, { method: 'PATCH', body })
export const put = <T = any>(p: string, body: any) => api<T>(p, { method: 'PUT', body })
export const del = (p: string) => api(p, { method: 'DELETE' })

export async function list<T = any>(p: string, query?: Record<string, any>): Promise<T[]> {
  const d = await get<any>(p, { page_size: 500, ...query })
  return Array.isArray(d) ? d : d.results
}

/** Multipart upload (attachments, statement files). */
export async function upload<T = any>(path: string, form: FormData): Promise<T> {
  const doFetch = () => fetch(`/api${path}`, {
    method: 'POST', body: form,
    headers: { ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}), ...(session.company() ? { 'X-Company': session.company()! } : {}) },
  })
  let r = await doFetch()
  if (r.status === 401 && session.hasRefresh() && await refresh()) r = await doFetch()
  const text = await r.text()
  const data = text ? JSON.parse(text) : null
  if (!r.ok) throw new ApiError(r.status, data)
  return data
}

/** Authenticated file download (the server never exposes files publicly). */
export async function downloadAuthed(path: string, filename: string) {
  const r = await fetch(`/api${path}`, { headers: { ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}), ...(session.company() ? { 'X-Company': session.company()! } : {}) } })
  if (!r.ok) throw new ApiError(r.status, null)
  const a = document.createElement('a')
  a.href = URL.createObjectURL(await r.blob())
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}
