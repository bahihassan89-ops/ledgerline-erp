"""Standard 4-level chart of accounts (IFRS / Egyptian Accounting Standards presentation), bilingual.
Level 1 = class, 2 = group, 3 = sub-group, 4 = postable ledger account.
Node: (code, name_en, name_ar, type, children-or-flags...). Root nodes also carry the account class.
"""

C = "control"

CHART = [
    ("1", "Assets", "الأصول", "group", "asset", [
        ("11", "Current Assets", "الأصول المتداولة", "group", [
            ("1101", "Cash and Cash Equivalents", "النقدية وما في حكمها", "group", [
                ("110101", "Main Cash Box", "الخزينة الرئيسية", "cash"),
                ("110102", "Petty Cash", "العهدة النقدية (المصروفات النثرية)", "cash"),
                ("110110", "Bank — Main Operating Account", "البنك — الحساب الجاري الرئيسي", "bank"),
                ("110111", "Bank — Foreign Currency Account", "البنك — حساب العملات الأجنبية", "bank"),
            ]),
            ("1102", "Trade and Other Receivables", "العملاء والمدينون", "group", [
                ("110201", "Accounts Receivable — Trade", "العملاء", "receivable", C),
                ("110202", "Allowance for Expected Credit Losses", "مخصص خسائر ائتمانية متوقعة", "current_asset"),
                ("110203", "Employee Advances", "سلف العاملين", "current_asset"),
                ("110204", "Other Receivables", "مدينون متنوعون", "current_asset"),
                ("110205", "Notes Receivable — Cheques Under Collection", "أوراق القبض — شيكات تحت التحصيل",
                 "notes_receivable", C),
                ("110206", "Taxes Withheld by Customers", "ضرائب خصم من المنبع لدى العملاء (خصم وإضافة)",
                 "wht_receivable"),
            ]),
            ("1103", "Inventories", "المخزون", "group", [
                ("110301", "Inventory — Finished Goods / Merchandise", "مخزون البضاعة التامة / بضاعة بغرض البيع",
                 "inventory", C),
                ("110302", "Inventory — Raw Materials", "مخزون الخامات", "inventory", C),
                ("110303", "Goods in Transit", "بضاعة بالطريق", "inventory"),
                ("110304", "Work in Progress", "إنتاج تحت التشغيل", "wip", C),
            ]),
            ("1104", "Prepayments and Tax Assets", "مصروفات مدفوعة مقدماً وأرصدة ضريبية", "group", [
                ("110401", "Prepaid Expenses", "مصروفات مدفوعة مقدماً", "prepayment"),
                ("110402", "Advances to Suppliers", "دفعات مقدمة للموردين", "prepayment"),
                ("110403", "Input VAT Recoverable", "ضريبة القيمة المضافة على المشتريات (مدخلات)", "tax_input"),
                ("110404", "Advance Income Tax Payments", "دفعات مقدمة من ضريبة الدخل", "current_asset"),
            ]),
        ]),
        ("12", "Non-Current Assets", "الأصول غير المتداولة", "group", [
            ("1201", "Property, Plant and Equipment", "الأصول الثابتة", "group", [
                ("120101", "Land", "أراضي", "fixed_asset"),
                ("120102", "Buildings", "مباني وإنشاءات", "fixed_asset"),
                ("120103", "Machinery and Equipment", "آلات ومعدات", "fixed_asset"),
                ("120104", "Motor Vehicles", "وسائل نقل وانتقال", "fixed_asset"),
                ("120105", "Furniture and Fixtures", "أثاث وتجهيزات", "fixed_asset"),
                ("120106", "Computers and IT Equipment", "أجهزة حاسب آلي ونظم معلومات", "fixed_asset"),
                ("120107", "Projects Under Construction", "مشروعات تحت التنفيذ", "fixed_asset"),
            ]),
            ("1202", "Accumulated Depreciation", "مجمع الإهلاك", "group", [
                ("120202", "Accumulated Depreciation — Buildings", "مجمع إهلاك المباني", "accumulated_depreciation"),
                ("120203", "Accumulated Depreciation — Machinery", "مجمع إهلاك الآلات والمعدات",
                 "accumulated_depreciation"),
                ("120204", "Accumulated Depreciation — Vehicles", "مجمع إهلاك وسائل النقل", "accumulated_depreciation"),
                ("120205", "Accumulated Depreciation — Furniture", "مجمع إهلاك الأثاث", "accumulated_depreciation"),
                ("120206", "Accumulated Depreciation — IT Equipment", "مجمع إهلاك الحاسب الآلي",
                 "accumulated_depreciation"),
            ]),
            ("1203", "Intangible Assets", "الأصول غير الملموسة", "group", [
                ("120301", "Software and Licences", "برامج وتراخيص", "non_current_asset"),
                ("120302", "Accumulated Amortisation", "مجمع الاستهلاك", "accumulated_depreciation"),
            ]),
        ]),
    ]),
    ("2", "Liabilities", "الالتزامات", "group", "liability", [
        ("21", "Current Liabilities", "الالتزامات المتداولة", "group", [
            ("2101", "Trade Payables", "الموردون والدائنون", "group", [
                ("210101", "Accounts Payable — Trade", "الموردون", "payable", C),
                ("210102", "Goods Received Not Invoiced (GRNI)", "بضاعة مستلمة لم ترد فواتيرها", "grni", C),
                ("210103", "Notes Payable — Cheques Issued", "أوراق الدفع — شيكات صادرة", "notes_payable", C),
                ("210104", "Landed Costs Clearing", "حساب وسيط تكاليف الاستيراد", "current_liability"),
            ]),
            ("2102", "Accruals", "مصروفات مستحقة", "group", [
                ("210201", "Accrued Salaries and Wages", "أجور ومرتبات مستحقة", "current_liability"),
                ("210202", "Accrued Expenses — Other", "مصروفات مستحقة أخرى", "current_liability"),
                ("210203", "Social Insurance Payable", "هيئة التأمينات الاجتماعية", "current_liability"),
            ]),
            ("2103", "Tax Liabilities", "الالتزامات الضريبية", "group", [
                ("210301", "Output VAT Payable", "ضريبة القيمة المضافة على المبيعات (مخرجات)", "tax_output"),
                ("210302", "VAT Settlement Account", "حساب تسوية ضريبة القيمة المضافة", "current_liability"),
                ("210303", "Corporate Income Tax Payable", "ضريبة الدخل المستحقة", "current_liability"),
                ("210304", "Withholding Tax Payable (Discount & Addition)", "ضرائب الخصم والإضافة المستحقة",
                 "wht_payable"),
                ("210305", "Payroll Income Tax Payable", "ضريبة كسب العمل المستحقة", "current_liability"),
                ("210306", "Stamp Tax Payable", "ضريبة الدمغة المستحقة", "current_liability"),
            ]),
            ("2104", "Other Current Liabilities", "التزامات متداولة أخرى", "group", [
                ("210401", "Customer Advances / Deferred Revenue", "دفعات مقدمة من العملاء / إيرادات مؤجلة",
                 "current_liability"),
                ("210402", "Short-Term Borrowings", "قروض قصيرة الأجل", "current_liability"),
            ]),
        ]),
        ("22", "Non-Current Liabilities", "الالتزامات غير المتداولة", "group", [
            ("2201", "Long-Term Liabilities", "التزامات طويلة الأجل", "group", [
                ("220101", "Long-Term Loans", "قروض طويلة الأجل", "non_current_liability"),
                ("220102", "End-of-Service Benefits Provision", "مخصص مكافأة نهاية الخدمة", "non_current_liability"),
                ("220103", "Deferred Tax Liability", "التزامات ضريبية مؤجلة", "non_current_liability"),
            ]),
        ]),
    ]),
    ("3", "Equity", "حقوق الملكية", "group", "equity", [
        ("31", "Capital and Reserves", "رأس المال والاحتياطيات", "group", [
            ("3101", "Share Capital", "رأس المال", "group", [
                ("310101", "Paid-up Capital", "رأس المال المدفوع", "equity"),
            ]),
            ("3102", "Reserves", "الاحتياطيات", "group", [
                ("310201", "Legal Reserve", "الاحتياطي القانوني", "equity"),
                ("310202", "General Reserve", "احتياطي عام", "equity"),
            ]),
        ]),
        ("32", "Retained Earnings", "الأرباح المرحلة", "group", [
            ("3201", "Accumulated Results", "نتائج الأعمال المتراكمة", "group", [
                ("320101", "Retained Earnings", "أرباح (خسائر) مرحلة", "retained_earnings"),
                ("320102", "Opening Balance Equity", "حساب وسيط الأرصدة الافتتاحية", "equity"),
            ]),
        ]),
    ]),
    ("4", "Revenue", "الإيرادات", "group", "income", [
        ("41", "Operating Revenue", "إيرادات النشاط", "group", [
            ("4101", "Sales", "المبيعات", "group", [
                ("410101", "Sales — Goods", "مبيعات بضائع", "revenue"),
                ("410102", "Sales — Services", "إيرادات خدمات", "revenue"),
                ("410103", "Sales Returns and Allowances", "مردودات ومسموحات المبيعات", "revenue"),
                ("410104", "Export Sales", "مبيعات تصدير", "revenue"),
            ]),
        ]),
        ("42", "Other Income", "إيرادات أخرى", "group", [
            ("4201", "Non-Operating Income", "إيرادات غير تشغيلية", "group", [
                ("420101", "Realised Foreign Exchange Gain", "أرباح فروق عملة محققة", "other_income"),
                ("420102", "Miscellaneous Income", "إيرادات متنوعة", "other_income"),
                ("420103", "Gain on Disposal of Fixed Assets", "أرباح رأسمالية (بيع أصول ثابتة)", "other_income"),
                ("420104", "Unrealised Foreign Exchange Gain", "أرباح فروق عملة غير محققة (إعادة تقييم)",
                 "other_income"),
                ("420105", "Interest Income", "فوائد دائنة", "other_income"),
            ]),
        ]),
    ]),
    ("5", "Expenses", "المصروفات", "group", "expense", [
        ("51", "Cost of Sales", "تكلفة المبيعات", "group", [
            ("5101", "Cost of Goods Sold", "تكلفة البضاعة المباعة", "group", [
                ("510101", "Cost of Goods Sold", "تكلفة البضاعة المباعة", "cost_of_sales"),
                ("510102", "Purchase Price Variance", "فروق أسعار المشتريات", "cost_of_sales"),
                ("510103", "Inventory Adjustments and Shrinkage", "فروق جرد وتسويات المخزون", "cost_of_sales"),
            ]),
            ("5102", "Production Costs", "تكاليف الإنتاج", "group", [
                ("510201", "Direct Labour — Actual", "الأجور المباشرة الفعلية", "cost_of_sales"),
                ("510202", "Manufacturing Overhead — Actual", "التكاليف الصناعية غير المباشرة الفعلية",
                 "cost_of_sales"),
                ("510203", "Labour Absorbed into Production", "أجور محملة على الإنتاج", "cost_of_sales"),
                ("510204", "Overhead Absorbed into Production", "تكاليف غير مباشرة محملة على الإنتاج",
                 "cost_of_sales"),
                ("510205", "Production Scrap and Variances", "تالف وفروق الإنتاج", "cost_of_sales"),
            ]),
        ]),
        ("52", "Operating Expenses", "المصروفات التشغيلية", "group", [
            ("5201", "Employee Costs", "تكاليف العاملين", "group", [
                ("520101", "Salaries and Wages", "الأجور والمرتبات", "expense"),
                ("520102", "Employee Benefits and Allowances", "مزايا وبدلات العاملين", "expense"),
                ("520103", "Social Insurance — Employer Share", "حصة صاحب العمل في التأمينات الاجتماعية", "expense"),
                ("520104", "End-of-Service Benefits Expense", "مصروف مكافأة نهاية الخدمة", "expense"),
            ]),
            ("5202", "Premises Costs", "مصروفات المقرات", "group", [
                ("520201", "Rent", "الإيجارات", "expense"),
                ("520202", "Utilities", "كهرباء ومياه وغاز", "expense"),
                ("520203", "Repairs and Maintenance", "صيانة وإصلاحات", "expense"),
            ]),
            ("5203", "General and Administrative", "مصروفات عمومية وإدارية", "group", [
                ("520301", "Office Supplies", "أدوات مكتبية ومطبوعات", "expense"),
                ("520302", "Telecommunications and Internet", "اتصالات وإنترنت", "expense"),
                ("520303", "Professional and Legal Fees", "أتعاب مهنية وقانونية", "expense"),
                ("520304", "Insurance", "تأمين", "expense"),
                ("520305", "Travel and Accommodation", "سفر وانتقالات", "expense"),
                ("520306", "Bank Charges", "مصروفات وعمولات بنكية", "expense"),
                ("520307", "Software Subscriptions", "اشتراكات برامج", "expense"),
                ("520308", "Stamp Tax and Government Fees", "ضريبة دمغة ورسوم حكومية", "expense"),
            ]),
            ("5204", "Selling and Marketing", "مصروفات بيعية وتسويقية", "group", [
                ("520401", "Advertising and Marketing", "دعاية وإعلان", "expense"),
                ("520402", "Delivery and Freight Out", "مصروفات نقل وشحن للعملاء", "expense"),
            ]),
            ("5205", "Depreciation and Amortisation", "الإهلاكات والاستهلاكات", "group", [
                ("520501", "Depreciation Expense", "مصروف الإهلاك", "depreciation"),
                ("520502", "Amortisation Expense", "مصروف استهلاك الأصول غير الملموسة", "depreciation"),
            ]),
        ]),
        ("53", "Other Expenses", "مصروفات أخرى", "group", [
            ("5301", "Finance and Other Costs", "مصروفات تمويلية وأخرى", "group", [
                ("530101", "Realised Foreign Exchange Loss", "خسائر فروق عملة محققة", "other_expense"),
                ("530102", "Interest Expense", "فوائد مدينة", "other_expense"),
                ("530103", "Corporate Income Tax Expense", "ضريبة الدخل", "other_expense"),
                ("530104", "Loss on Disposal of Fixed Assets", "خسائر رأسمالية (بيع أصول ثابتة)", "other_expense"),
                ("530105", "Unrealised Foreign Exchange Loss", "خسائر فروق عملة غير محققة (إعادة تقييم)",
                 "other_expense"),
            ]),
        ]),
    ]),
]

# AccountingSettings field -> account code
DEFAULT_ACCOUNTS = {
    "receivable_account": "110201", "payable_account": "210101", "grni_account": "210102",
    "price_variance_account": "510102", "inventory_adjustment_account": "510103",
    "fx_gain_account": "420101", "fx_loss_account": "530101", "retained_earnings_account": "320101",
    "opening_equity_account": "320102", "cheques_receivable_account": "110205",
    "cheques_payable_account": "210103", "unrealized_fx_gain_account": "420104",
    "unrealized_fx_loss_account": "530105", "asset_disposal_gain_account": "420103",
    "asset_disposal_loss_account": "530104", "landed_cost_clearing_account": "210104",
    "wip_account": "110304", "labour_absorbed_account": "510203", "overhead_absorbed_account": "510204",
    "production_scrap_account": "510205",
}
