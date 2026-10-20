# Ledgerline ERP — Accounting Handbook (Egypt edition)

**Audience:** the accounting team, internal audit, and the company's external tax advisor.
**Purpose:** describe every accounting rule, automatic journal entry, control and report in the
system, so the team can verify it before go-live and use it as the reference afterwards.

Every rule below is implemented in code and covered by automated tests (68 at the time of writing).
Where the rule is also enforced by the database itself, this is stated. Items whose legal value
should be confirmed by your tax advisor are marked **⚖ verify** and collected in §21.

---

## Contents

1. Core principles and how they are enforced
2. Chart of accounts (bilingual, 4 levels)
3. Journals and document numbering
4. Fiscal years, periods and locking
5. Multi-currency and exchange differences (EAS 13)
6. Sales cycle and accounts receivable
7. Purchase cycle and accounts payable
8. Inventory and costing (EAS 2)
9. Production / manufacturing
10. Fixed assets (EAS 10) and tax depreciation (Law 91/2005)
11. Value Added Tax — Law 67/2016
12. Withholding tax — discount & addition system (خصم وإضافة)
13. ETA e-invoicing (منظومة الفاتورة الإلكترونية)
14. Banking, bank reconciliation and cheques
15. Month-end and year-end close
16. Budgets, recurring entries and accruals
17. Reports catalogue
18. Roles, managerial levels, approvals and segregation of duties
19. Audit trail, attachments and record retention
20. Go-live: migrating from the previous system
21. Parameters to confirm with the tax advisor
22. Known limitations and roadmap
- Appendix A — Journal entry cookbook (every automatic entry)
- Appendix B — Month-end procedure (step by step)

---

## 1. Core principles and how they are enforced

| Principle | What it means | Enforcement |
|---|---|---|
| **Double entry** | Every entry has equal debits and credits in base currency. | Posting engine rejects unbalanced entries; a PostgreSQL *deferred constraint trigger* re-checks at COMMIT, so not even direct SQL can leave an unbalanced posted entry. |
| **Immutability** | Posted entries are never edited or deleted; errors are corrected by a reversing entry. | DB triggers block UPDATE/DELETE of posted entries and their lines. The application role has no right to drop or disable triggers. |
| **Gapless numbering** | Posted entries are numbered consecutively per journal per fiscal year (`GEN/2026/00001`). | Number assigned at posting time under a row lock, inside the posting transaction. Drafts carry no number. |
| **Period control** | Closed months receive no postings. | Periods are *open* → *closed* (only users with "post into closed period") → *locked* (nobody). Plus a company *lock date*. |
| **Accrual basis** | Revenue on invoicing, expense on billing, not on cash movement. | Invoices/bills post to AR/AP; payments clear AR/AP. |
| **Sub-ledger = control account** | Customer balances = AR account; vendor balances = AP; stock valuation = inventory accounts; open manufacturing orders = WIP; asset register = fixed-asset accounts. | Control accounts accept postings only from their sub-ledger (no manual entries). Each pair has a reconciliation report and a close-checklist item. |
| **Segregation of duties** | The preparer is not the approver; administrators hold no accounting rights. | Approval engine refuses self-approval and double approval; permission model per company. |
| **Evidence** | Who did what, when, from where; supporting documents kept. | Append-only, SHA-256 hash-chained audit log (DB-protected); attachments of posted records cannot be deleted. |
| **Money precision** | No floating-point money. | `Decimal` everywhere, 4-decimal storage, rounding to the currency's decimals (EGP 2). |

## 2. Chart of accounts (bilingual, 4 levels)

The chart is a tree. Only **leaf** accounts are postable; group accounts total their children.

```
Level 1  Class        1   Assets                                  الأصول
Level 2  Group        11  Current Assets                          الأصول المتداولة
Level 3  Sub-group    1102 Trade and Other Receivables            العملاء والمدينون
Level 4  Ledger       110205 Notes Receivable — Cheques Under Collection   أوراق القبض — شيكات تحت التحصيل
```

* Five classes: **1 Assets, 2 Liabilities, 3 Equity, 4 Revenue, 5 Expenses**. Children inherit
  the class; a child's code must start with its parent's code.
* Every account has an English **and Arabic** name; reports, prints and the tree show both.
* The **account type** drives behaviour:

| Type | Class | Role in the system |
|---|---|---|
| cash, bank | Asset | Payment journals, bank reconciliation, cash-flow "cash" |
| receivable *(control)* | Asset | Customer balances (AR), requires a partner on every line |
| notes_receivable *(control)* | Asset | Cheques received and not yet cleared (أوراق القبض) |
| wht_receivable | Asset | Withholding tax deducted by customers — credit against income tax |
| inventory *(control)* | Asset | Stock at cost — only stock moves post here |
| wip *(control)* | Asset | Work in progress — only manufacturing orders post here |
| tax_input | Asset | Recoverable input VAT |
| prepayment, current_asset | Asset | Prepaid expenses, advances, other receivables |
| fixed_asset, non_current_asset | Asset | Asset cost (register reconciles to these) |
| accumulated_depreciation | Asset (contra) | Credit balance |
| payable *(control)* | Liability | Vendor balances (AP) |
| notes_payable *(control)* | Liability | Cheques issued and not yet cleared (أوراق الدفع) |
| grni *(control)* | Liability | Goods received not invoiced |
| tax_output | Liability | Output VAT |
| wht_payable | Liability | Withholding tax deducted from vendors, due to the ETA |
| current_liability, non_current_liability | Liability | Accruals, loans, provisions |
| equity, retained_earnings | Equity | Capital, reserves, results |
| revenue, other_income | Revenue | Sales; FX gains; disposal gains |
| cost_of_sales | Expense | COGS, price variance, production costs & absorption |
| expense, depreciation, other_expense | Expense | Operating costs, depreciation, FX losses, interest, income tax |

* The standard chart has **137 accounts**, aligned to EAS/IFRS presentation, including the Egyptian
  tax accounts (output/input VAT, withholding payable/receivable, payroll tax, stamp tax, social
  insurance, legal reserve). It is fully editable.
* **Cash-flow classification** can be overridden per account (operating / investing / financing / cash / excluded).
* Accounts with postings cannot change class, cannot become groups, and cannot be deleted (only deactivated).
* `python manage.py sync_chart --code <company>` adds new standard accounts to an existing company without touching existing ones.

## 3. Journals and document numbering

| Code | Type | Used for | Manual entries |
|---|---|---|---|
| SAL | sales | Customer invoices, credit and debit notes | No |
| PUR | purchase | Vendor bills and debit notes | No |
| BNK / BNK2 | bank | Receipts, payments, cheque clearing, statement items | Yes |
| CSH | cash | Cash box | Yes |
| STK | inventory | Receipts, deliveries, adjustments, returns, landed costs, production | No |
| GEN | general | Accruals, corrections, FX differences, revaluation, disposals | Yes |
| DEP | general | Monthly depreciation | No |
| OPN | general | Opening balances (go-live) | Yes |
| CLS | general | Year-end closing | No |

Business documents have their own gapless sequences: `INV`, `CN` (credit note), `BILL`, `DN`
(vendor debit note), `RCPT`, `PAY`, `SO`, `PO`, `MO`, `LC` (landed cost), `WH/IN`, `WH/OUT`.

## 4. Fiscal years, periods and locking

* A fiscal year has 12 monthly periods (start month configurable).
* **Open** → anyone with posting rights may post. **Closed** → only users holding *post into closed
  period* (Financial Controller and above). **Locked** → nobody, permanently; requires all earlier
  periods closed and no drafts dated in the period.
* **Company lock date**: no posting on or before it, regardless of period state. Moving it backwards requires the lock permission.
* **Year-end close** (CFO): all periods closed, no drafts; posts the closing entry transferring every
  revenue and expense balance to **Retained Earnings**; the P&L report still shows the year's result
  (closing entries are excluded from it).

## 5. Multi-currency and exchange differences (EAS 13)

* Base (functional) currency: **EGP** for Egyptian companies. Every journal line keeps the base amount
  *and* the transaction-currency amount and rate. Rates are maintained per date (typically CBE rates).
* Conversion rounding differences are placed on the side with more lines so a single bank or control line keeps its exact converted amount.
* **Realised FX**: when a foreign-currency document is settled at a different rate, the difference is
  posted automatically to *Realised FX gain/loss* (also on cheque clearing).
* **Unrealised FX — month-end revaluation**: open foreign-currency invoices/bills and foreign-currency
  bank/cash accounts (accounts with a currency set) are restated at the closing rate. The difference
  goes to *Unrealised FX gain/loss* and is **reversed automatically on the next day**, so settlement
  continues to be measured against the original document rate. Running it twice for the same date is
  blocked. The AR/AP sub-ledger reconciliation excludes these entries by design.

## 6. Sales cycle and accounts receivable

```
Quotation → Sales Order (confirm: credit check, approval) → Delivery → Customer Invoice → Receipt
```

* **Credit control**: if enabled for the customer, confirmation is blocked when *open receivables +
  confirmed-not-invoiced orders + this order* exceed the credit limit. "On hold" blocks new orders.
  Changing credit settings needs a dedicated permission.
* **Delivery** issues stock at cost (Dr COGS / Cr Inventory). **Invoicing** is based on delivered quantities (services: mark as delivered).
* **Invoice** posts AR (net of any withholding the customer will deduct), revenue and output VAT; see Appendix A.
* **Credit note** (إشعار دائن) from a posted invoice; automatically applied against the original.
  Option **return to stock**: goods come back at their original delivery cost (Dr Inventory / Cr COGS).
* **Debit note** (إشعار مدين) for additional charges, referencing the original invoice (ETA type D).
* **Receipts**: bank transfer, cash or **cheque** (§14). Partial and multiple allocations; oldest-first auto-allocation; allocations can be removed (FX entries are reversed).
* **Aging** (as-of any date): current, 1–30, 31–60, 61–90, 90+ days past due; unapplied receipts shown separately. Customer statements with running balance.

## 7. Purchase cycle and accounts payable

```
RFQ → Purchase Order (approval by amount & level) → Goods Receipt → Vendor Bill (3-way match) → Payment
```

* **Goods receipt**: Dr Inventory / Cr GRNI at PO net price × receipt-date rate.
* **Three-way match** on bills linked to POs: billed qty ≤ received − already billed; unit price ≤ PO
  price × (1 + tolerance %). Above tolerance only a level-4+ user may post.
* **GRNI clearing**: the bill debits GRNI at the *receipt* value; the difference to the bill price goes
  to **Purchase Price Variance**. The last bill clears the exact remainder.
* **Duplicate protection**: the same vendor bill number cannot be entered twice for a vendor (database constraint).
* **Withholding** at bill level (§12): the vendor is paid net; WHT payable is recorded.
* **Vendor debit note** with **return to stock**: goods leave at current cost against GRNI, the price difference goes to PPV, and PO received/billed quantities are reduced.
* **Landed costs** (§8.4) for customs, freight and clearance on imports.
* Services and non-stock items post straight to the expense account on the bill line.

## 8. Inventory and costing (EAS 2)

### 8.1 Perpetual inventory
Every movement posts to the GL at cost immediately, so *Stock valuation report = Inventory accounts* at all times (checked by the close checklist).

### 8.2 Costing methods (per product category)
* **FIFO** — each receipt creates a cost layer; issues consume the oldest layers first.
* **Weighted average (AVCO)** — `new average = (qty × avg + in qty × in cost) / (qty + in qty)`; the last unit out takes the exact remaining value (no rounding residue).
* LIFO is not available (not permitted under EAS 2). The costing method cannot change while the category has stock.

### 8.3 Movements
Receipts, deliveries, customer and vendor returns, count adjustments (gain/loss to *Inventory
adjustments and shrinkage*), opening stock (against Opening Balance Equity), warehouse transfers
(no GL effect), production consumption and output. **Negative stock is blocked** (database CHECK constraint).

### 8.4 Landed costs (تكاليف الاستيراد)
1. Post the customs/clearing/freight bills with the line account **Landed Costs Clearing** (210104), VAT as applicable.
2. Create a landed-cost document, choose the receipts, allocate **by value or by quantity**.
3. Posting adds the share of goods **still on hand** to Inventory (raising FIFO layers / the average cost) and the share **already sold** to COGS; credit Landed Costs Clearing.
* Limitation: for AVCO products the on-hand share is approximated from the product's quantity on hand (see §22).

## 9. Production / manufacturing

**Model:** actual absorption costing.

* **Work centers** carry a direct-labour rate and an overhead absorption rate per hour (budgeted overhead ÷ budgeted hours).
* **Bill of materials (BOM)**: components with quantity per output and expected loss %; operations with minutes per output and work center. Planned cost = components at current average cost + minutes × rates.
* **Manufacturing order (MO)**: confirm → issue components → record actual minutes → produce (good qty + scrap) → finish.

| Step | Entry |
|---|---|
| Issue components | Dr Work in Progress / Cr Raw-material inventory (FIFO/average cost) |
| Record operations | Dr WIP / Cr *Labour absorbed into production* and *Overhead absorbed into production* |
| Produce | Dr Finished-goods inventory / Cr WIP (actual unit cost) |
| Scrap | Dr *Production scrap and variances* / Cr WIP |

* While an MO is open, partial output is valued at the average WIP per remaining unit; **finishing** clears all remaining WIP.
* Actual factory wages and overhead are booked as usual to *Direct labour — actual* and *Manufacturing overhead — actual*. The net of the actual and absorbed accounts is the **under/over-absorption** for the period, shown in cost of sales.
* Reports: MO cost sheet (planned vs actual: material, labour, overhead, variance, unit cost) and WIP report reconciled to the WIP account.

## 10. Fixed assets (EAS 10) and tax depreciation (Law 91/2005)

### 10.1 Register
Code, Arabic/English name, category, serial number, location, **custodian (العهدة)**, cost center,
acquisition and in-service dates, cost, salvage value, useful life, method. Assets are created
manually or **from a posted vendor-bill line** booked to a fixed-asset account; the checklist lists
capital purchases not yet registered.

### 10.2 Accounting depreciation (monthly)
* **Straight line** (القسط الثابت): (cost − salvage − prior depreciation) ÷ remaining months; last month takes the remainder.
* **Declining balance** (القسط المتناقص): NBV × annual rate ÷ 12; the final month brings NBV to salvage value.
* Depreciation starts in the in-service month. One entry per month: Dr Depreciation expense (by cost center) / Cr Accumulated depreciation.
* Go-live assets: enter the accumulated depreciation before go-live and the first month to depreciate in this system.
* **Disposal / sale**: depreciation is first posted up to the disposal date, then Dr Accumulated depreciation, Dr Proceeds (bank/other receivable), Cr Cost, and the difference to *Gain/Loss on disposal of fixed assets*.
* The register (cost, accumulated, NBV) reconciles to the GL at any date.

### 10.3 Default categories

| Category | Accounting life | Tax treatment (art. 25) ⚖ verify |
|---|---|---|
| Land | not depreciated | not depreciable |
| Buildings | 50 years SL | 5% of cost per year |
| Machinery & equipment | 10 years SL | 25% of the depreciation basis |
| Motor vehicles | 5 years SL | 25% of the depreciation basis |
| Furniture & fixtures | 10 years SL | 25% of the depreciation basis |
| Computers & IT | 3 years SL | 50% of the depreciation basis |
| Software & licences | 3 years SL | 50% of the depreciation basis |

Purchased intangibles such as goodwill: 10% per year under art. 25 (create a category if needed).

### 10.4 Tax depreciation report
For the tax return reconciliation, per category and year: accounting depreciation, tax
depreciation, difference, and closing tax basis. Pool categories use basis = opening basis +
additions − disposal proceeds (art. 26). **Not applied:** the optional 30% first-year deduction
for production machinery (art. 27) — apply manually if elected. Deferred tax is not computed automatically.

## 11. Value Added Tax — Law 67/2016

* Standard rate **14%**; **5%** for machinery and equipment; **zero-rated** exports; **exempt** supplies. Each VAT code carries its ETA type/sub-type code (e.g. T1/V009 general, T1/V001 export, T1/V003 exempt).
* VAT is computed per line, on the price after discount; tax-inclusive prices are supported.
* Output VAT → *Output VAT payable* (210301). Input VAT → *Input VAT recoverable* (110403).
* Codes are validated: sales documents only accept sales VAT codes, purchases only purchase codes.
* **VAT return working paper** (monthly): taxable value and VAT by code for sales and purchases, net payable or credit carried forward. The settlement entry (offset input against output, pay the balance) is a manual journal via the *VAT Settlement Account*.
* Input VAT is deductible only with a valid tax invoice; vendor e-invoices received on ETA are matched to bills (§13.6). ⚖ verify filing deadline and form.

## 12. Withholding tax — discount & addition system (خصم وإضافة)

Under Income Tax Law 91/2005 (art. 59) and the ministerial decision in force, payers withhold tax
from payments to resident suppliers and remit it quarterly with **Form 41**.

| Code (purchases) | Rate | ETA code | Applies to |
|---|---|---|---|
| WHT1-CON-P | 1% | T4/W001 | Contracting |
| WHT1-SUP-P | 1% | T4/W002 | Supplies and purchases |
| WHT3-SRV-P | 3% | T4/W004 | Services |
| WHT5-COM-P | 5% | T4/W011 | Commissions and brokerage |

Matching sales-side codes (…-S) record tax **our customers** withhold from us. ⚖ verify rates and
codes against the decision in force (sources reviewed cite Ministerial Decree 405/2018: 1% / 3% / 5%).

* **Base**: net amount after discount, **before VAT**. **Threshold**: applies when the document's
  untaxed amount reaches **EGP 300** (`min_base`, configurable per code). ⚖ verify
* Each vendor/customer can carry a default withholding code; it is applied automatically to bills/invoices generated from orders and to new lines.
* **Vendor bill**: Dr Expense/GRNI, Dr Input VAT, Cr Accounts payable (*net of WHT*), Cr **WHT payable**. The vendor is paid the net amount.
* **Customer invoice** where the customer withholds: Dr Accounts receivable (net), Dr **Taxes withheld by customers**, Cr Revenue, Cr Output VAT. Collect the customer's withholding certificate; the balance is credited against corporate income tax.
* **Reports**: *WHT Form 41* (vendor, tax registration no., type/ETA code, rate, base, amount, documents) for any period — typically a quarter — reconciled to the WHT payable account movement; *WHT by customers* for certificates to collect.
* Remittance: a manual payment from the bank debiting WHT payable.

## 13. ETA e-invoicing (منظومة الفاتورة الإلكترونية)

### 13.1 Scope
Customer **invoices (I)**, **credit notes (C)** and **debit notes (D)** are e-invoiced to the
Egyptian Tax Authority (ETA) when posted. Opening items imported at go-live are not. B2C
**e-receipts** (POS) are not covered (§22).

### 13.2 One-time setup
1. On the ETA portal, register the ERP under the taxpayer profile and obtain the **client ID and secret** (pre-production first).
2. Integrations → ETA: environment, client ID/secret (stored encrypted, never displayed again), **taxpayer activity code**, branch ID, issuer address as registered.
3. Company: 9-digit **tax registration number**, Arabic legal name.
4. Products: register item codes on the ETA portal (**EGS** internal codes like `EG-<RIN>-<code>`, or **GS1** barcodes) and enter them with the ETA unit (EA, KGM, LTR…).
5. Customers: receiver type **B** (business — 9-digit tax number and full address), **P** (individual — 14-digit national ID required from the configured threshold, default EGP 25,000 ⚖ verify), or **F** (foreign).
6. Signing (below), then enable e-invoicing.

### 13.3 Validation before posting
Posting a customer document is **blocked** with a list of every problem if ETA would reject it:
missing/invalid tax numbers, addresses, item codes, VAT codes without ETA codes, or a credit/debit
note whose original invoice was not accepted by ETA. The books never contain an invoice that cannot be e-invoiced.

### 13.4 Document, serialization and signing
* The document follows ETA schema **v1.0**: issuer, receiver, document type, `dateTimeIssued` (UTC), activity code, internal ID (our invoice number), PO/SO references, lines (`salesTotal`, `discount`, `netTotal`, `taxableItems` T1 VAT and T4 withholding, `total`), totals (`totalSalesAmount`, `totalDiscountAmount`, `netAmount`, `taxTotals`, `totalAmount`), and for C/D the `references` to the original UUID. Amounts in EGP; foreign-currency invoices include `currencySold`, `amountSold` and `currencyExchangeRate`.
* `totalAmount` = net + VAT − withholding, identical to the invoice total in the books.
* **Canonical serialization** (what is hashed and signed): property names upper-cased and quoted, values quoted, document order, array names repeated before each element, `signatures` excluded.
* **Signing**: ETA requires a CAdES-BES signature with an approved eSeal certificate, normally on a USB token, which cannot be used from a server. The system therefore calls a **signing agent** on a trusted PC with the token: `POST {document, canonical}` → `{signature}` (base64 CAdES-BES). The agent can be ETA's own signing tool or an equivalent wrapped as a small HTTPS service; it receives both the full document and our canonical string. In pre-production only, *unsigned v0.9* mode can be used for testing.

### 13.5 Lifecycle
`pending` → submitted (`submitted`) → ETA validation → **valid** / **invalid** (validation steps and
errors shown) / **rejected** (at submission). Valid documents get a **UUID**, a long ID and a
**public URL** (also printed as a QR code on the invoice). Invalid/rejected/failed documents can be
corrected (master data) and **resubmitted**; amount errors are corrected with a credit note.
**Cancellation** of a valid document is requested from ETA with a reason (buyer approval may be
required; the status moves to cancelled when ETA confirms). A background worker (`eta_sync`)
submits pending documents and refreshes statuses; submission can also be immediate on posting.
⚖ verify submission deadline (sources describe same-day/real-time reporting with a 7-day maximum being phased out), cancellation and rejection windows.

### 13.6 Received documents
The worker can pull e-invoices that vendors issued to the company's tax number and **match** them
to posted vendor bills (vendor tax number + vendor invoice number). Unmatched documents appear on
the close checklist: they are either missing bills or invoices the company should dispute — and
input VAT on non-e-invoiced purchases is at risk.

## 14. Banking, bank reconciliation and cheques

### 14.1 Bank reconciliation (التسوية البنكية)
1. **Import** the bank statement CSV (date, description, reference, and either a signed amount or debit/credit columns; DD/MM/YYYY or ISO dates). Opening + movements must equal the closing balance, or the import is refused.
2. **Auto-match**: each statement line is matched to an unmatched posted GL line on that bank account with the same amount within ±10 days, preferring a reference match.
3. **Manual match** for the rest; **create entry** for items known only from the statement (bank charges, interest, direct debits).
4. **Mark reconciled** when every line is matched.
5. **Reconciliation statement**: balance per bank + deposits in transit − outstanding payments/unpresented cheques = adjusted bank balance; balance per books ± statement items not yet booked = adjusted book balance; difference must be zero. The first imported statement's opening balance must equal the book balance on its start date (shown).

Posted GL lines are never modified by matching; the match is stored on the statement line.

### 14.2 Cheques (أوراق القبض / أوراق الدفع)
| Event | Customer cheque received | Cheque issued to vendor |
|---|---|---|
| Receipt / issue (posting the payment) | Dr Notes receivable / Cr AR (invoice settled) | Dr AP / Cr Notes payable |
| Deposit for collection | status only | — |
| Cleared by the bank | Dr Bank / Cr Notes receivable (± realised FX) | Dr Notes payable / Cr Bank |
| Bounced / stopped | Allocations removed (invoices reopen) and the payment reversed: Dr AR / Cr Notes receivable | Reversed: Dr Notes payable / Cr AP |

The **cheque portfolio** lists cheques by due date and status with past-due flags; overdue
uncleared cheques appear on the close checklist.

## 15. Month-end and year-end close

The **Close Checklist** runs every control for the selected period. Blocking (✗) items must be
resolved; warnings (!) should be reviewed:

| Check | Blocks |
|---|---|
| Unposted / waiting-approval journal entries in the period | ✗ |
| Unposted invoices and bills dated in the period | ! |
| Recurring entries due | ! |
| Depreciation posted up to period end | ✗ |
| Capital purchases registered as assets | ! |
| Bank statements imported up to period end and fully matched | ✗ / ! |
| Cheques past due and not cleared | ! |
| AR and AP sub-ledgers agree with the GL | ✗ |
| Stock valuation agrees with the GL | ✗ |
| WIP agrees with open manufacturing orders | ✗ |
| Goods received > 60 days ago and not billed (accrue or chase) | ! |
| Foreign-currency balances revalued at period end | ! |
| All customer invoices of the period valid on ETA | ✗ |
| Vendor e-invoices on ETA recorded as bills | ! |
| Opening Balance Equity cleared after go-live | ! |
| Trial balance balances | ✗ |

The step-by-step procedure is in Appendix B.

## 16. Budgets, recurring entries and accruals

* **Budgets**: per fiscal year, per account (and optionally cost center), per month; draft → approved (frozen). **Budget vs actual** for any range: budget, actual, variance, %, favourable/unfavourable (income above budget and expenses below budget are favourable).
* **Recurring entries**: templates (monthly/quarterly/yearly) for rent, amortisation of prepayments, standing accruals; generated daily by the scheduler (`run_recurring`) or on demand, as drafts or auto-posted (only if the template's owner holds posting rights).
* **Auto-reversing accruals**: any manual entry can carry an *auto-reverse date*; posting it posts the reversal on that date automatically.

## 17. Reports catalogue

| Report | What it proves / shows |
|---|---|
| Trial balance (by level) | Opening, period debits/credits, closing per account and group; balanced flag |
| Balance sheet (with prior-year column) | Assets = liabilities + equity, including the unclosed current-year result |
| Income statement (with prior-year column) | Revenue, cost of sales, gross profit, net result (closing entries excluded) |
| **Cash flow statement** (indirect, EAS 4) | Operating / investing / financing; reconciles to the actual change in cash; go-live opening entries are treated as opening cash |
| General ledger | Running balance per account (drill-down from every statement) |
| AR / AP aging | As-of any date, by bucket, with unapplied payments |
| Sub-ledger reconciliation | AR and AP open items = control accounts |
| Inventory valuation | Quantity, average cost and value per product = inventory accounts |
| **VAT return** working paper | By VAT code: taxable value and VAT, output/input/net |
| **WHT Form 41** / **WHT by customers** | Withheld amounts by partner and type, reconciled to the GL |
| Fixed-asset register / tax depreciation | NBV reconciled to GL; tax vs accounting depreciation |
| Bank reconciliation statement | Bank vs books with outstanding items |
| Cheque portfolio | Cheques by due date and status |
| Budget vs actual | Variances by account |
| MO cost sheet / WIP | Planned vs actual production cost; WIP = GL |
| Customer/vendor statement | Running balance per partner |
| Audit trail + chain verification | Every change; tamper detection |

All tables export to CSV (protected against spreadsheet formula injection) and print; invoices,
credit/debit notes, bills and orders print as bilingual A4 documents (Save as PDF).

## 18. Roles, managerial levels, approvals and segregation of duties

Permissions (75) are fine-grained codes; roles bundle them with a **managerial level (0–5)** and an
**approval limit**. Roles are assigned **per company**.

| Level | Role | Main rights | Default approval limit, EGP |
|---|---|---|---|
| 1 | Accounting Clerk | Drafts of entries, invoices, bills, orders, payments; view reports | 0 |
| 2 | Accountant | Post entries, invoices, bills, payments; confirm orders; stock moves; cheques; bank reconciliation; ETA submission; production orders | 100,000 |
| 3 | Senior Accountant | + reversals, credit/debit notes, master data, stock adjustments, assets, landed costs, recurring entries, BOMs, ETA cancellation | 500,000 |
| 4 | Financial Controller | + chart of accounts, journals, taxes, periods (close), post into closed period, depreciation run, disposals, FX revaluation, imports, budgets, ETA settings, credit limits, audit log | 2,500,000 |
| 5 | CFO / Finance Director | + lock periods, year-end close, company settings | unlimited |
| 0 | Auditor | Read-only everything + audit trail | — |
| 0 | System Administrator | Users, roles, integrations — **no accounting rights** | — |

* **MFA** (authenticator app) is mandatory from level 4 (configurable; recommended for all finance users).
* **Approval rules** by document type and amount route documents through one or more levels in order (e.g. PO ≥ EGP 500,000 → Controller; ≥ EGP 2,500,000 → Controller then CFO). An approver must hold the level, have a limit ≥ the amount, not be the preparer/submitter, and not have approved an earlier step.
* **Anti-escalation**: nobody can grant permissions or assign roles above their own; nobody can change their own roles.

## 19. Audit trail, attachments and record retention

* Every master-data change (field-level before/after), business event (post, reverse, cancel,
  allocate, approve, cheque events, ETA submissions, period changes, imports) and login event is
  recorded with user, IP, time and request ID, **SHA-256 hash-chained**, and protected against
  modification at database level. *Verify integrity* recomputes the chain.
* **Attachments** (PDF, images, Excel, Word, CSV; ≤10 MB; content checked against the extension;
  SHA-256 fingerprint) on invoices, bills, payments, entries, orders, assets, statements and landed
  costs. Downloaded only through the authenticated API. Attachments of posted records **cannot be deleted**.
* Retain books, documents and e-invoices for the period required by law ⚖ verify (Unified Tax Procedures Law 206/2020 and the Commercial Law). Back up the database with point-in-time recovery and keep the audit log in write-once storage.

## 20. Go-live: migrating from the previous system

Choose the go-live date (normally the last day before the first period in this system). Then:

1. **Set up**: `setup_company --country EG`, company data, users and roles, ETA settings (pre-production), customers/vendors (with tax data and withholding codes), products (with ETA codes), warehouses.
2. **Opening trial balance** (Imports → 1): every account except receivables, payables, inventory and WIP. The difference parks in *Opening Balance Equity*.
3. **Open items** (Imports → 2a/2b): each unpaid customer invoice and vendor bill with its original number and due date (negative = credit note), so aging and allocation work from day one. Not e-invoiced.
4. **Opening stock** (Stock → Opening stock): quantity and unit cost per product and warehouse.
5. **Fixed assets**: register each asset with cost, accumulated depreciation before go-live and the first month to depreciate here (the cost/accumulated balances come in with step 2).
6. **Check**: *Opening Balance Equity must be zero*, the trial balance must equal the old system's closing trial balance, AR/AP aging must match the old system's, stock valuation must match.
7. **Bank**: import the first statement from the go-live date; its opening balance must equal the book balance.
8. Switch ETA to production after the ETA confirms your pre-production test documents.

Templates for every CSV are downloadable from the Imports page. A spreadsheet journal import
(e.g. monthly payroll summary from the payroll system) creates a draft entry for review.

## 21. Parameters to confirm with the tax advisor

| Parameter | Default in the system | Where to change |
|---|---|---|
| VAT standard / reduced rates | 14% / 5% (machinery & equipment) | Accounting Setup → Taxes |
| ETA VAT sub-codes | V009 general, V010 other rates, V001 export, V003 exempt | Taxes |
| Withholding rates | 1% contracting & supplies, 3% services, 5% commissions | Taxes |
| Withholding ETA codes | W001, W002, W004, W011 | Taxes |
| Withholding threshold | EGP 300 per document | Taxes → *Withholding from amount* |
| Individuals' national-ID threshold on e-invoices | EGP 25,000 | ETA settings |
| E-invoice submission warning | 7 days | ETA settings |
| Tax depreciation rates | 5% buildings, 10% intangibles, 50% computers/software, 25% other | Asset categories |
| Art. 27 accelerated 30% first-year deduction | Not applied | Manual adjustment |
| VAT return filing deadline / form | Monthly working paper | — |
| Form 41 deadline | Quarterly (end of month following the quarter) | — |
| Record retention period | — | Policy |
| Corporate income tax rate | Not automated (accounts provided) | Manual entry |

## 22. Known limitations and roadmap

* **B2C e-receipts** (POS e-receipt system) — not included; required if the company sells to consumers through points of sale.
* **Table tax** (ضريبة الجدول, T2/T3) and other ETA tax types (stamp tax T5–T6, entertainment, etc.) — not calculated; only VAT (T1) and withholding (T4).
* **Payroll** (salary tax, social insurance Law 148/2019) — not included; post payroll summaries via the journal import.
* **Deferred tax** — not computed; use the tax-depreciation report as the input.
* **Standard costing, landed costs on AVCO** (approximation), lots/serial numbers, expiry dates, multi-level BOM explosion, production planning (MRP) — not included.
* **Consolidation** across companies, inter-company eliminations — not included.
* **Arabic user interface** (RTL translation of screens) — the data is bilingual and printed documents are bilingual; screen labels are in English.
* **Email sending** of invoices and statements — print to PDF and send from email for now.

---

## Appendix A — Journal entry cookbook

Amounts: N = net (after discount), V = VAT, W = withholding, T = N + V − W.

| Transaction | Debit | Credit |
|---|---|---|
| Customer invoice | AR (T) · Taxes withheld by customers (W) | Revenue (N) · Output VAT (V) |
| Customer credit note | Revenue (N) · Output VAT (V) | AR (T) · Taxes withheld by customers (W) |
| Credit note with return to stock (additional) | Inventory (original cost) | COGS |
| Delivery | COGS | Inventory |
| Customer receipt (transfer) | Bank | AR |
| Customer cheque received / cleared | Notes receivable → then Bank | AR → then Notes receivable |
| Goods receipt | Inventory | GRNI |
| Vendor bill (stock, from PO) | GRNI (receipt value) · Input VAT · PPV (if bill > receipt) | AP (T) · WHT payable (W) · PPV (if bill < receipt) |
| Vendor bill (expense / asset) | Expense or Fixed asset · Input VAT | AP (T) · WHT payable (W) |
| Vendor debit note with return | AP · (stock leaves) GRNI | Expense/GRNI · Input VAT · Inventory; GRNI ↔ PPV for price gap |
| Vendor payment | AP | Bank (or Notes payable for a cheque) |
| Realised FX on settlement | AR/AP or FX loss | FX gain or AR/AP |
| Month-end FX revaluation (reversed next day) | AR / bank (increase) or Unrealised FX loss | Unrealised FX gain or AR / AP (increase) |
| Landed cost | Inventory (on-hand share) · COGS (sold share) | Landed Costs Clearing |
| Stock count gain / loss | Inventory / Adjustments | Adjustments / Inventory |
| Opening stock | Inventory | Opening Balance Equity |
| Production: issue components | WIP | Raw materials |
| Production: labour & overhead | WIP | Labour absorbed · Overhead absorbed |
| Production: output / scrap | Finished goods · Scrap | WIP |
| Depreciation | Depreciation expense | Accumulated depreciation |
| Asset disposal | Accumulated depreciation · Proceeds · Loss | Cost · Gain |
| Bank charges from statement | Bank charges | Bank |
| Accrual with auto-reverse | Expense (on date) → reversed on reverse date | Accrued expenses |
| Year-end close | Revenue accounts · Retained earnings (if loss) | Expense accounts · Retained earnings (if profit) |
| WHT remittance (manual) | WHT payable | Bank |
| VAT settlement (manual) | Output VAT | Input VAT · Bank (balance) |

## Appendix B — Month-end procedure

1. Post all documents dated in the month (invoices, bills, receipts, payments); clear the Approvals inbox.
2. Generate due recurring entries; post accruals (with auto-reverse dates) and prepayment amortisation.
3. Production: finish completed manufacturing orders; post actual factory payroll/overhead; review under/over-absorption.
4. Inventory: post landed costs for the month's imports; post count adjustments.
5. Fixed assets: register capital purchases; **run depreciation** to month end.
6. Treasury: import bank statements, match, post bank charges, reconcile; follow up past-due cheques.
7. Enter month-end exchange rates; run **FX revaluation**.
8. ETA: resolve invalid/rejected documents; fetch received documents and record missing vendor bills.
9. Taxes: run the VAT return and (quarter end) Form 41; post settlement/remittance entries.
10. Review the **Close Checklist** until no blocking item remains; review the trial balance, P&L vs budget and cash flow.
11. **Close the period** (Controller). Lock it when the books are final (CFO). At year end, close all periods and run the **year-end close**.

## Sources

Legal parameters were checked in October 2026 against secondary sources; the tax advisor's confirmation (§21) prevails.

- Withholding rates and Form 41: [PwC — Egypt withholding taxes](https://taxsummaries.pwc.com/egypt/corporate/withholding-taxes), [NPI Egypt — Form 41](https://npiegypt.com/articles/guides/withholding-tax-form-41-egypt)
- Tax depreciation, Law 91/2005 arts. 25–27: [Consortio Law Firm translation](https://consortiolawfirm.com/egypt-income-tax-law-91-2005-english-translation/), [WIPO Lex](https://www.wipo.int/wipolex/en/legislation/details/19870)
- ETA document and API (confirm against the official SDK during pre-production): [ETA SDK — Invoice v1.0](https://sdk.invoicing.eta.gov.eg/documents/invoice-v1-0/), [ETA signature format guide](https://www.eta.gov.eg/sites/default/files/2021-09/Digital%20Signature%20Format%20V1.1_final_0.pdf)
- E-invoicing deadlines and thresholds: [e-invoicing.org — Egypt](https://e-invoicing.org/egypt/), [VATupdate](https://www.vatupdate.com/2026/02/23/briefing-document-podcast-e-invoicing-e-reporting-in-egypt/)
