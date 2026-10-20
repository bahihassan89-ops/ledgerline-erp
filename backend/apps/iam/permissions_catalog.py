"""Every permission in the system. Roles are bundles of these codes."""

CATALOG = {
    # Administration
    "core.company.view": "View company settings",
    "core.company.manage": "Edit company settings, lock date",
    "core.currency.manage": "Manage currencies and exchange rates",
    "core.costcenter.manage": "Manage cost centers",
    "iam.user.view": "View users",
    "iam.user.manage": "Create/disable users, assign roles",
    "iam.role.manage": "Create and edit roles",
    "audit.log.view": "View the audit trail",
    "integrations.apikey.manage": "Manage integration API keys",
    "integrations.webhook.manage": "Manage webhooks",
    # General ledger
    "ledger.account.view": "View chart of accounts",
    "ledger.account.manage": "Create/edit accounts in the chart of accounts",
    "ledger.journal.manage": "Create/edit journals",
    "ledger.tax.manage": "Manage tax codes",
    "ledger.entry.view": "View journal entries",
    "ledger.entry.create": "Create draft journal entries",
    "ledger.entry.post": "Post journal entries",
    "ledger.entry.reverse": "Reverse posted entries",
    "ledger.period.manage": "Create fiscal years and periods",
    "ledger.period.close": "Close / reopen fiscal periods",
    "ledger.period.lock": "Permanently lock fiscal periods",
    "ledger.period.post_closed": "Post into a closed (not locked) period",
    "ledger.year.close": "Run year-end closing",
    "ledger.report.view": "View financial reports",
    # Partners
    "partners.partner.view": "View customers and vendors",
    "partners.partner.manage": "Create/edit customers and vendors",
    "partners.credit_limit.manage": "Change customer credit limits",
    # Inventory
    "inventory.product.view": "View products",
    "inventory.product.manage": "Create/edit products, categories, warehouses",
    "inventory.stock.view": "View stock levels and moves",
    "inventory.stock.move": "Validate receipts and deliveries",
    "inventory.stock.adjust": "Post inventory adjustments",
    "inventory.valuation.view": "View inventory valuation",
    # Sales
    "sales.order.view": "View quotations and sales orders",
    "sales.order.create": "Create quotations and sales orders",
    "sales.order.confirm": "Confirm sales orders",
    "sales.order.cancel": "Cancel sales orders",
    # Purchasing
    "purchasing.order.view": "View purchase orders",
    "purchasing.order.create": "Create purchase orders",
    "purchasing.order.confirm": "Confirm purchase orders",
    "purchasing.order.cancel": "Cancel purchase orders",
    # AR / AP
    "billing.invoice.view": "View customer invoices and credit notes",
    "billing.invoice.create": "Create draft customer invoices",
    "billing.invoice.post": "Post customer invoices",
    "billing.invoice.cancel": "Cancel / credit customer invoices",
    "billing.bill.view": "View vendor bills",
    "billing.bill.create": "Create draft vendor bills",
    "billing.bill.post": "Post vendor bills",
    "billing.bill.cancel": "Cancel / debit vendor bills",
    "billing.payment.view": "View payments",
    "billing.payment.create": "Create draft payments",
    "billing.payment.post": "Post payments and allocations",
    # Cheques & banking
    "billing.cheque.manage": "Deposit, clear and bounce cheques",
    "banking.statement.view": "View bank statements and reconciliations",
    "banking.statement.manage": "Import statements and reconcile bank accounts",
    # Fixed assets
    "assets.asset.view": "View the fixed-asset register",
    "assets.asset.manage": "Register and confirm fixed assets",
    "assets.depreciation.run": "Post depreciation",
    "assets.asset.dispose": "Dispose of fixed assets",
    # E-invoicing (ETA)
    "einvoice.document.view": "View e-invoice submissions",
    "einvoice.document.submit": "Submit, refresh and resubmit e-invoices",
    "einvoice.document.cancel": "Request cancellation of e-invoices on ETA",
    "einvoice.settings.manage": "Configure the ETA connection",
    # Planning
    "planning.budget.view": "View budgets and budget-vs-actual",
    "planning.budget.manage": "Create and approve budgets",
    "planning.recurring.manage": "Manage recurring journal entries",
    # Period-end & data
    "ledger.revaluation.run": "Run foreign-currency revaluation",
    "ledger.import.run": "Import opening balances and journal spreadsheets",
    "inventory.landed_cost.manage": "Create and post landed costs",
    # Production
    "manufacturing.order.view": "View manufacturing orders and BOMs",
    "manufacturing.order.manage": "Create and run manufacturing orders",
    "manufacturing.bom.manage": "Maintain bills of materials and work centers",
    # Approvals
    "approvals.request.view": "View approval requests",
    "approvals.request.decide": "Approve or reject requests (within limit)",
    "approvals.rule.manage": "Configure approval rules",
}

VIEW_ALL = [c for c in CATALOG if c.endswith(".view")] + ["ledger.report.view", "inventory.valuation.view"]

CLERK = VIEW_ALL + [
    "ledger.entry.create", "billing.invoice.create", "billing.bill.create", "billing.payment.create",
    "sales.order.create", "purchasing.order.create",
]
ACCOUNTANT = CLERK + [
    "ledger.entry.post", "billing.invoice.post", "billing.bill.post", "billing.payment.post",
    "sales.order.confirm", "purchasing.order.confirm", "inventory.stock.move", "approvals.request.decide",
    "billing.cheque.manage", "banking.statement.manage", "einvoice.document.submit", "manufacturing.order.manage",
]
SENIOR = ACCOUNTANT + [
    "ledger.entry.reverse", "partners.partner.manage", "inventory.product.manage", "inventory.stock.adjust",
    "billing.invoice.cancel", "billing.bill.cancel", "sales.order.cancel", "purchasing.order.cancel",
    "core.costcenter.manage", "assets.asset.manage", "inventory.landed_cost.manage", "planning.recurring.manage",
    "manufacturing.bom.manage", "einvoice.document.cancel",
]
CONTROLLER = SENIOR + [
    "ledger.account.manage", "ledger.journal.manage", "ledger.tax.manage", "ledger.period.manage",
    "ledger.period.close", "ledger.period.post_closed", "partners.credit_limit.manage",
    "core.currency.manage", "approvals.rule.manage", "audit.log.view", "assets.depreciation.run",
    "assets.asset.dispose", "ledger.revaluation.run", "ledger.import.run", "planning.budget.manage",
    "einvoice.settings.manage",
]
CFO = CONTROLLER + ["ledger.period.lock", "ledger.year.close", "core.company.manage"]
AUDITOR = VIEW_ALL + ["audit.log.view"]
ADMIN = ["iam.user.view", "iam.user.manage", "iam.role.manage", "core.company.view", "audit.log.view",
         "integrations.apikey.manage", "integrations.webhook.manage"]

# name, level, permissions, approval_limit (None = unlimited)
DEFAULT_ROLES = [
    ("Accounting Clerk", 1, CLERK, "0"),
    ("Accountant", 2, ACCOUNTANT, "10000"),
    ("Senior Accountant", 3, SENIOR, "50000"),
    ("Financial Controller", 4, CONTROLLER, "250000"),
    ("CFO / Finance Director", 5, CFO, None),
    ("Auditor (read-only)", 0, AUDITOR, "0"),
    ("System Administrator", 0, ADMIN, "0"),
]
