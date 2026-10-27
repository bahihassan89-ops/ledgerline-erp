import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)

// Service worker: app-shell caching for resilience + Web Push display. Production-build only --
// in dev it would cache Vite's module graph and fight HMR.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}) })
}
