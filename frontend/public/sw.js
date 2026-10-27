// Ledgerline service worker: app-shell caching for resilience against flaky connections, plus
// Web Push display. Bump CACHE on each meaningful change so old caches are cleared on activate.
const CACHE = 'ledgerline-v1'
const CORE_ASSETS = ['/', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(CORE_ASSETS)).then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  )
})

// Never cache the API (always fresh/authoritative). For navigations, try the network first and
// fall back to the cached shell if offline. For other same-origin GETs (hashed build assets),
// cache-first since a given build's filenames are immutable.
self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/')) return

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((res) => { caches.open(CACHE).then((c) => c.put(request, res.clone())); return res })
        .catch(() => caches.match(request).then((cached) => cached || caches.match('/')))
    )
    return
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then((res) => {
      if (res.ok) caches.open(CACHE).then((c) => c.put(request, res.clone()))
      return res
    }))
  )
})

// ---------------- Web Push ----------------
self.addEventListener('push', (event) => {
  let data = {}
  try { data = event.data ? event.data.json() : {} } catch { data = { title: 'Ledgerline', body: event.data ? event.data.text() : '' } }
  const title = data.title || 'Ledgerline'
  const options = { body: data.body || '', icon: '/icon-192.png', badge: '/icon-192.png', data: { url: data.url || '/' } }
  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = event.notification.data?.url || '/'
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      const existing = list.find((c) => c.url.startsWith(self.location.origin))
      if (existing) { existing.focus(); if ('navigate' in existing) existing.navigate(url); return undefined }
      return self.clients.openWindow(url)
    })
  )
})
