"""Optional modules a company can be provisioned with. `module_code` on a ViewSet (checked by
ErpPermission.has_permission) gates the whole endpoint behind CompanyModule.enabled -- a trading
company provisioned without "manufacturing" simply gets PermissionDenied from those endpoints."""
from .models import CompanyType

MODULE_REGISTRY = {
    "ledger": "General ledger, chart of accounts, journals",
    "billing": "Customer invoices, vendor bills, payments",
    "partners": "Customers and vendors",
    "inventory": "Products, stock, warehouses",
    "sales": "Quotations and sales orders",
    "purchasing": "Purchase orders",
    "manufacturing": "Bills of materials, work centers, manufacturing orders",
    "assets": "Fixed-asset register and depreciation",
    "banking": "Bank statements and reconciliation",
    "einvoice": "Egyptian Tax Authority e-invoicing",
    "planning": "Budgets and recurring entries",
}

CORE_MODULES = ["ledger", "billing", "partners", "inventory", "banking", "einvoice", "planning"]

DEFAULT_MODULES_BY_TYPE = {
    CompanyType.MANUFACTURING: CORE_MODULES + ["sales", "purchasing", "manufacturing", "assets"],
    CompanyType.TRADING: CORE_MODULES + ["sales", "purchasing", "assets"],
    CompanyType.SERVICES: CORE_MODULES + ["sales", "assets"],
    CompanyType.AGRICULTURE: CORE_MODULES + ["sales", "purchasing", "manufacturing", "assets"],
    CompanyType.OTHER: CORE_MODULES + ["sales", "purchasing", "assets"],
}
