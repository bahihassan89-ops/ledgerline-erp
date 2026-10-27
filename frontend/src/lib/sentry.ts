// Error tracking (Sentry). Baked in at build time via VITE_SENTRY_DSN -- unset by default, so
// this is a no-op until a deployment opts in. Only runs in production builds: dev/HMR errors are
// already visible in the terminal/devtools console and shouldn't be shipped to a third party.
// No PII is attached (no user email, no request bodies) -- this app handles customer/vendor data.
import * as Sentry from '@sentry/react'

const dsn = import.meta.env.VITE_SENTRY_DSN as string | undefined

export function initSentry() {
  if (!dsn || !import.meta.env.PROD) return
  // No integrations beyond the defaults -- in particular no session replay and no browser
  // tracing, so no DOM/network content is captured, just error reports and basic breadcrumbs.
  Sentry.init({
    dsn,
    environment: import.meta.env.MODE,
    tracesSampleRate: 0,
  })
}

export const SentryErrorBoundary = Sentry.ErrorBoundary
