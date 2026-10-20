import { useCallback, useEffect, useRef, useState } from 'react'
import { get, list } from './api'

export function useFetch<T = any>(path: string | null, query?: Record<string, any>, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(!!path)
  const q = JSON.stringify(query || {})
  const seq = useRef(0)
  const load = useCallback(async () => {
    if (!path) return
    const n = ++seq.current
    setLoading(true)
    try {
      const d = await get<T>(path, JSON.parse(q))
      if (n === seq.current) { setData(d); setError(null) }
    } catch (e: any) { if (n === seq.current) setError(e.message) } finally { if (n === seq.current) setLoading(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, q, ...deps])
  useEffect(() => { load() }, [load])
  return { data, error, loading, reload: load, setData }
}

const cache = new Map<string, Promise<any[]>>()
export function useOptions<T = any>(path: string, query?: Record<string, any>) {
  const key = path + JSON.stringify(query || {})
  const [items, setItems] = useState<T[]>([])
  useEffect(() => {
    if (!cache.has(key)) cache.set(key, list(path, query).catch(() => { cache.delete(key); return [] }))
    cache.get(key)!.then(setItems)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return items
}
export const invalidateOptions = () => cache.clear()
