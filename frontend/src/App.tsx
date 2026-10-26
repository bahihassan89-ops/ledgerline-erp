import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import { ToastProvider } from './components/ui'
import { AuthProvider, useAuth } from './lib/auth'
import Accounts from './pages/Accounts'
import { Approvals, Audit, Integrations, Periods, Settings, Users } from './pages/Admin'
import Dashboard from './pages/Dashboard'
import Documents from './pages/Documents'
import { Products, Stock } from './pages/Inventory'
import JournalEntries from './pages/JournalEntries'
import Login from './pages/Login'
import Orders from './pages/Orders'
import Partners from './pages/Partners'
import Payments from './pages/Payments'
import Reports from './pages/Reports'
import Assets from './pages/Assets'
import Banking from './pages/Banking'
import Eta from './pages/Eta'
import { LandedCosts } from './pages/Inventory'
import Manufacturing from './pages/Manufacturing'
import { Cheques } from './pages/Payments'
import { CloseChecklist, Imports, Revaluation } from './pages/PeriodEnd'
import { Budgets, Recurring } from './pages/Planning'
import Print from './pages/Print'

function Routed() {
  const { me, loading, can } = useAuth()
  if (loading) return <div className="empty">Loading…</div>
  if (!me) return <Login />
  if (!me.companies.length) return <div className="empty">Your account is not assigned to any company. Contact your administrator.</div>
  if (window.location.pathname.startsWith('/print/')) return <Routes><Route path="/print/:kind/:id" element={<Print />} /></Routes>
  // Route the user to the first module they actually have access to, rather than assuming
  // everyone can view invoices — e.g. an inventory- or sales-only role has neither.
  const HOME_CANDIDATES: [string, string][] = [
    ['ledger.report.view', '__dashboard__'], ['iam.user.view', '/users'],
    ['sales.order.view', '/sales-orders'], ['billing.invoice.view', '/invoices'],
    ['purchasing.order.view', '/purchase-orders'], ['billing.bill.view', '/bills'],
    ['partners.partner.view', '/customers'], ['inventory.product.view', '/products'],
    ['inventory.stock.view', '/stock'], ['ledger.account.view', '/accounts'],
    ['assets.asset.view', '/assets'], ['manufacturing.order.view', '/manufacturing'],
    ['banking.statement.view', '/banking'], ['einvoice.document.view', '/eta'],
  ]
  const homeTo = HOME_CANDIDATES.find(([perm]) => can(perm))?.[1]
  const home = homeTo === '__dashboard__' ? <Dashboard /> : homeTo ? <Navigate to={homeTo} />
    : <div className="empty">Your account has no module access yet. Contact your administrator.</div>
  return (
    <Layout>
      <Routes>
        <Route path="/" element={home} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/journal-entries" element={<JournalEntries />} />
        <Route path="/periods" element={<Periods />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/sales-orders" element={<Orders kind="sales" />} />
        <Route path="/invoices" element={<Documents kind="invoices" />} />
        <Route path="/receipts" element={<Payments direction="inbound" />} />
        <Route path="/customers" element={<Partners kind="customer" />} />
        <Route path="/purchase-orders" element={<Orders kind="purchase" />} />
        <Route path="/bills" element={<Documents kind="bills" />} />
        <Route path="/vendor-payments" element={<Payments direction="outbound" />} />
        <Route path="/vendors" element={<Partners kind="vendor" />} />
        <Route path="/products" element={<Products />} />
        <Route path="/stock" element={<Stock />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/users" element={<Users />} />
        <Route path="/integrations" element={<Integrations />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/assets" element={<Assets />} />
        <Route path="/banking" element={<Banking />} />
        <Route path="/cheques" element={<Cheques />} />
        <Route path="/eta" element={<Eta />} />
        <Route path="/manufacturing" element={<Manufacturing />} />
        <Route path="/landed-costs" element={<LandedCosts />} />
        <Route path="/budgets" element={<Budgets />} />
        <Route path="/recurring" element={<Recurring />} />
        <Route path="/close" element={<CloseChecklist />} />
        <Route path="/revaluation" element={<Revaluation />} />
        <Route path="/imports" element={<Imports />} />
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Layout>
  )
}

export default function App() {
  return <BrowserRouter><ToastProvider><AuthProvider><Routed /></AuthProvider></ToastProvider></BrowserRouter>
}
