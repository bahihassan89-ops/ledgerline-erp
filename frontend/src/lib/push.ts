// Web Push subscribe flow: fetch the server's VAPID public key, subscribe this browser via the
// service worker's PushManager, then register the subscription with the backend
// (PushSubscriptionViewSet @ /api/push-subscriptions/). Best-effort everywhere -- a user who
// never enables push still gets in-app notifications via the bell, so failures here are silent.
import { get, post } from './api'

function urlBase64ToUint8Array(base64url: string): Uint8Array {
  const padding = '='.repeat((4 - (base64url.length % 4)) % 4)
  const base64 = (base64url + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(base64)
  const out = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

export function pushSupported() {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

export async function getPushSubscriptionState(): Promise<'unsupported' | 'denied' | 'subscribed' | 'available'> {
  if (!pushSupported()) return 'unsupported'
  if (Notification.permission === 'denied') return 'denied'
  const reg = await navigator.serviceWorker.getRegistration()
  const sub = await reg?.pushManager.getSubscription()
  return sub ? 'subscribed' : 'available'
}

export async function enablePush(): Promise<boolean> {
  if (!pushSupported()) return false
  const permission = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission()
  if (permission !== 'granted') return false

  const reg = await navigator.serviceWorker.ready
  let sub = await reg.pushManager.getSubscription()
  if (!sub) {
    const { key } = await get<{ key: string }>('/push/public-key/')
    if (!key) return false
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) })
  }
  const raw = sub.toJSON() as { keys?: { p256dh?: string; auth?: string } }
  if (!raw.keys?.p256dh || !raw.keys?.auth) return false
  await post('/push-subscriptions/', { endpoint: sub.endpoint, p256dh: raw.keys.p256dh, auth: raw.keys.auth })
  return true
}

export async function disablePush(): Promise<void> {
  const reg = await navigator.serviceWorker.getRegistration()
  const sub = await reg?.pushManager.getSubscription()
  if (sub) { await sub.unsubscribe() }
}
