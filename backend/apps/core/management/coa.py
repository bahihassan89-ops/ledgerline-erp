"""Standard 4-level chart of accounts (IFRS presentation).
Level 1 = class, 2 = group, 3 = sub-group, 4 = postable ledger account.
Tuple: (code, name, type, flags) — type "group" for non-postable nodes."""

C = "control"

CHART = [
    ("1", "Assets", "group", "asset", [
        ("11", "Current Assets", "group", [
            ("1101", "Cash and Cash Equivalents", "group", [
                ("110101", "Main Cash Box", "cash"),
                ("110102", "Petty Cash", "cash"),
                ("110110", "Bank — Main Operating Account", "bank"),
                ("110111", "Bank — Foreign Currency Account", "bank"),
            ]),
            ("1102", "Trade and Other Receivables", "group", [
                ("110201", "Accounts Receivable — Trade", "receivable", C),
                ("110202", "Allowance for Expected Credit Losses", "current_asset"),
                ("110203", "Employee Advances", "current_asset"),
                ("110204", "Other Receivables", "current_asset"),
            ]),
            ("1103", "Inventories", "group", [
                ("110301", "Inventory — Finished Goods / Merchandise", "inventory", C),
                ("110302", "Inventory — Raw Materials", "inventory", C),
                ("110303", "Goods in Transit", "inventory"),
            ]),
            ("1104", "Prepayments and Tax Assets", "group", [
                ("110401", "Prepaid Expenses", "prepayment"),
                ("110402", "Advances to Suppliers", "prepayment"),
                ("110403", "Input VAT Recoverable", "tax_input"),
            ]),
        ]),
        ("12", "Non-Current Assets", "group", [
            ("1201", "Property, Plant and Equipment", "group", [
                ("120101", "Land", "fixed_asset"),
                ("120102", "Buildings", "fixed_asset"),
                ("120103", "Machinery and Equipment", "fixed_asset"),
                ("120104", "Motor Vehicles", "fixed_asset"),
                ("120105", "Furniture and Fixtures", "fixed_asset"),
                ("120106", "Computers and IT Equipment", "fixed_asset"),
            ]),
            ("1202", "Accumulated Depreciation", "group", [
                ("120202", "Accumulated Depreciation — Buildings", "accumulated_depreciation"),
                ("120203", "Accumulated Depreciation — Machinery", "accumulated_depreciation"),
                ("120204", "Accumulated Depreciation — Vehicles", "accumulated_depreciation"),
                ("120205", "Accumulated Depreciation — Furniture", "accumulated_depreciation"),
                ("120206", "Accumulated Depreciation — IT Equipment", "accumulated_depreciation"),
            ]),
            ("1203", "Intangible Assets", "group", [
                ("120301", "Software and Licences", "non_current_asset"),
                ("120302", "Accumulated Amortisation", "accumulated_depreciation"),
            ]),
        ]),
    ]),
    ("2", "Liabilities", "group", "liability", [
        ("21", "Current Liabilities", "group", [
            ("2101", "Trade Payables", "group", [
                ("210101", "Accounts Payable — Trade", "payable", C),
                ("210102", "Goods Received Not Invoiced (GRNI)", "grni", C),
            ]),
            ("2102", "Accruals", "group", [
                ("210201", "Accrued Salaries and Wages", "current_liability"),
                ("210202", "Accrued Expenses — Other", "current_liability"),
            ]),
            ("2103", "Tax Liabilities", "group", [
                ("210301", "Output VAT Payable", "tax_output"),
                ("210302", "VAT Settlement Account", "current_liability"),
                ("210303", "Zakat / Income Tax Payable", "current_liability"),
            ]),
            ("2104", "Other Current Liabilities", "group", [
                ("210401", "Customer Advances / Deferred Revenue", "current_liability"),
                ("210402", "Short-Term Borrowings", "current_liability"),
            ]),
        ]),
        ("22", "Non-Current Liabilities", "group", [
            ("2201", "Long-Term Liabilities", "group", [
                ("220101", "Long-Term Loans", "non_current_liability"),
                ("220102", "End-of-Service Benefits Provision", "non_current_liability"),
            ]),
        ]),
    ]),
    ("3", "Equity", "group", "equity", [
        ("31", "Capital and Reserves", "group", [
            ("3101", "Share Capital", "group", [
                ("310101", "Paid-up Capital", "equity"),
            ]),
            ("3102", "Reserves", "group", [
                ("310201", "Statutory Reserve", "equity"),
            ]),
        ]),
        ("32", "Retained Earnings", "group", [
            ("3201", "Accumulated Results", "group", [
                ("320101", "Retained Earnings", "retained_earnings"),
                ("320102", "Opening Balance Equity", "equity"),
            ]),
        ]),
    ]),
    ("4", "Revenue", "group", "income", [
        ("41", "Operating Revenue", "group", [
            ("4101", "Sales", "group", [
                ("410101", "Sales — Goods", "revenue"),
                ("410102", "Sales — Services", "revenue"),
                ("410103", "Sales Returns and Allowances", "revenue"),
            ]),
        ]),
        ("42", "Other Income", "group", [
            ("4201", "Non-Operating Income", "group", [
                ("420101", "Realised Foreign Exchange Gain", "other_income"),
                ("420102", "Miscellaneous Income", "other_income"),
            ]),
        ]),
    ]),
    ("5", "Expenses", "group", "expense", [
        ("51", "Cost of Sales", "group", [
            ("5101", "Cost of Goods Sold", "group", [
                ("510101", "Cost of Goods Sold", "cost_of_sales"),
                ("510102", "Purchase Price Variance", "cost_of_sales"),
                ("510103", "Inventory Adjustments and Shrinkage", "cost_of_sales"),
            ]),
        ]),
        ("52", "Operating Expenses", "group", [
            ("5201", "Employee Costs", "group", [
                ("520101", "Salaries and Wages", "expense"),
                ("520102", "Employee Benefits and Allowances", "expense"),
                ("520103", "End-of-Service Benefits Expense", "expense"),
            ]),
            ("5202", "Premises Costs", "group", [
                ("520201", "Rent", "expense"),
                ("520202", "Utilities", "expense"),
                ("520203", "Repairs and Maintenance", "expense"),
            ]),
            ("5203", "General and Administrative", "group", [
                ("520301", "Office Supplies", "expense"),
                ("520302", "Telecommunications and Internet", "expense"),
                ("520303", "Professional and Legal Fees", "expense"),
                ("520304", "Insurance", "expense"),
                ("520305", "Travel and Accommodation", "expense"),
                ("520306", "Bank Charges", "expense"),
                ("520307", "Software Subscriptions", "expense"),
            ]),
            ("5204", "Selling and Marketing", "group", [
                ("520401", "Advertising and Marketing", "expense"),
                ("520402", "Delivery and Freight Out", "expense"),
            ]),
            ("5205", "Depreciation and Amortisation", "group", [
                ("520501", "Depreciation Expense", "depreciation"),
                ("520502", "Amortisation Expense", "depreciation"),
            ]),
        ]),
        ("53", "Other Expenses", "group", [
            ("5301", "Finance and Other Costs", "group", [
                ("530101", "Realised Foreign Exchange Loss", "other_expense"),
                ("530102", "Interest Expense", "other_expense"),
                ("530103", "Zakat / Income Tax Expense", "other_expense"),
            ]),
        ]),
    ]),
]
