import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'
import { initSentry, SentryErrorBoundary } from './lib/sentry'

initSentry()

// Catches render-time crashes anywhere in the tree so one broken page can't white-screen the
// whole app -- the user can reload instead of losing the session. Reported to Sentry when configured.
function Crashed() {
  return (
    <div className="empty">
      Something went wrong. <button className="sm ghost" onClick={() => window.location.reload()}>Reload</button>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SentryErrorBoundary fallback={<Crashed />}>
      <App />
    </SentryErrorBoundary>
  </StrictMode>,
)

// Service worker: app-shell caching for resilience + Web Push display. Production-build only --
// in dev it would cache Vite's module graph and fight HMR.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}) })
}
