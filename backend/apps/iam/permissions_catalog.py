"""Every permission in the system. Roles are bundles of these codes."""

CATALOG = {
    # Administration
    "core.company.view": "View company settings",
    "core.company.manage": "Edit company settings, lock date",
    "core.currency.manage": "Manage currencies and exchange rates",
    "core.costcenter.manage": "Manage cost centers",
    "core.branch.manage": "Manage branches",
    "iam.user.view": "View users",
    "iam.user.manage": "Create/disable users, assign roles",
    "iam.role.manage": "Create and edit roles",
    "iam.team.manage": "Create/edit teams (Inventory, Procurement, ...) and assign their members/lead",
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
    "core.costcenter.manage", "core.branch.manage", "assets.asset.manage", "inventory.landed_cost.manage",
    "planning.recurring.manage",
    "manufacturing.bom.manage", "einvoice.document.cancel",
]
CONTROLLER = SENIOR + [
    "ledger.account.manage", "ledger.journal.manage", "ledger.tax.manage", "ledger.period.manage",
    "ledger.period.close", "ledger.period.post_closed", "partners.credit_limit.manage",
    "core.currency.manage", "approvals.rule.manage", "audit.log.view", "assets.depreciation.run",
    "assets.asset.dispose", "ledger.revaluation.run", "ledger.import.run", "planning.budget.manage",
    "einvoice.settings.manage",
    # The Controller/CFO is typically the person who actually set up the company (see
    # apps.core.services._grant_submitter_membership) -- they need to be able to hire/assign roles
    # and stand up department teams themselves, not just the System Administrator. Narrower than
    # core.company.manage (CFO-only) since it doesn't touch company settings, just people/roles/teams.
    "iam.user.manage", "iam.role.manage", "iam.team.manage",
]
CFO = CONTROLLER + ["ledger.period.lock", "ledger.year.close", "core.company.manage"]
AUDITOR = VIEW_ALL + ["audit.log.view"]
ADMIN = ["iam.user.view", "iam.user.manage", "iam.role.manage", "iam.team.manage", "core.company.view",
         "audit.log.view", "integrations.apikey.manage", "integrations.webhook.manage"]

# Inventory department ladder: day-to-day stock handling vs. full product/valuation ownership.
INVENTORY_OFFICER = ["inventory.product.view", "inventory.stock.view", "inventory.stock.move"]
INVENTORY_MANAGER = sorted(set(INVENTORY_OFFICER + [
    "inventory.product.manage", "inventory.stock.adjust", "inventory.valuation.view",
    "inventory.landed_cost.manage", "approvals.request.decide",
]))

# Procurement department ladder: raising/placing orders vs. confirming them and managing vendors.
PROCUREMENT_OFFICER = ["purchasing.order.view", "purchasing.order.create", "partners.partner.view",
                       "inventory.product.view"]
PROCUREMENT_MANAGER = sorted(set(PROCUREMENT_OFFICER + [
    "purchasing.order.confirm", "purchasing.order.cancel", "partners.partner.manage",
    "approvals.request.decide",
]))

# name, level, permissions, approval_limit (None = unlimited)
DEFAULT_ROLES = [
    ("Accounting Clerk", 1, CLERK, "0"),
    ("Accountant", 2, ACCOUNTANT, "10000"),
    ("Senior Accountant", 3, SENIOR, "50000"),
    ("Financial Controller", 4, CONTROLLER, "250000"),
    ("CFO / Finance Director", 5, CFO, None),
    ("Auditor (read-only)", 0, AUDITOR, "0"),
    ("System Administrator", 0, ADMIN, "0"),
    ("Inventory Officer", 1, INVENTORY_OFFICER, "0"),
    ("Inventory Manager", 3, INVENTORY_MANAGER, "25000"),
    ("Procurement Officer", 1, PROCUREMENT_OFFICER, "0"),
    ("Procurement Manager", 3, PROCUREMENT_MANAGER, "50000"),
]

# Company-independent permissions: cross-company/central authority that doesn't belong to any
# single company's Role/Membership (CEO, central Controller, central Accounting Manager).
GLOBAL_CATALOG = {
    "company.create": "Start a new company (draft) for provisioning",
    "company.submit": "Submit a draft company for CEO approval",
    "company.activate": "Give final CEO approval to activate a pending company",
    "company.reject": "Reject a company pending approval",
    "company.module.manage": "Enable/disable modules on a company",
    "membership.grant_any_company": "Grant/edit team membership and roles in any company",
    "presence.view_team": "View the team activity / presence portal",
    "report.intercompany": "View the intercompany reconciliation report across companies",
    "report.consolidated": "View the consolidated / group financial report across companies",
    "ops.backup.manage": "Trigger an on-demand backup and view backup status",
}

GLOBAL_CONTROLLER = ["company.create", "company.submit", "company.module.manage",
                      "membership.grant_any_company", "presence.view_team",
                      "report.intercompany", "report.consolidated", "ops.backup.manage"]
GLOBAL_ACCOUNTING_MANAGER = ["company.create", "company.submit", "membership.grant_any_company",
                              "presence.view_team", "report.intercompany", "report.consolidated"]
GLOBAL_CEO = list(GLOBAL_CATALOG)

# name, level, permissions
GLOBAL_DEFAULT_ROLES = [
    ("Central Financial Controller", 4, GLOBAL_CONTROLLER),
    ("Central Accounting Manager", 3, GLOBAL_ACCOUNTING_MANAGER),
    ("CEO", 5, GLOBAL_CEO),
]
