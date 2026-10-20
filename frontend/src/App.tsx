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

function Routed() {
  const { me, loading, can } = useAuth()
  if (loading) return <div className="empty">Loading…</div>
  if (!me) return <Login />
  if (!me.companies.length) return <div className="empty">Your account is not assigned to any company. Contact your administrator.</div>
  const home = can('ledger.report.view') ? <Dashboard /> : can('iam.user.view') ? <Navigate to="/users" /> : <Navigate to="/invoices" />
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
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Layout>
  )
}

export default function App() {
  return <BrowserRouter><ToastProvider><AuthProvider><Routed /></AuthProvider></ToastProvider></BrowserRouter>
}
