# Accounting Design — Ledgerline ERP

This document explains the accounting rules the system enforces. It is written so a
non-accountant can follow it and an accounting team can verify it. Every rule below is
implemented in code (`backend/apps/...`) and most are also enforced by the database itself.

---

## 1. Core principles

| Principle | What it means | How the system enforces it |
|---|---|---|
| **Double entry** | Every transaction has equal debits and credits. | Posting service rejects unbalanced entries; a PostgreSQL constraint trigger re-checks at commit time, so even a direct SQL insert cannot leave an unbalanced posted entry. |
| **Immutability of the books** | Posted entries are never edited or deleted. Mistakes are corrected by a *reversing entry*. | DB triggers block `UPDATE`/`DELETE` of posted entries and their lines. The API only offers "Reverse". |
| **Gapless numbering** | Posted entries get consecutive numbers per journal per fiscal year (auditors check for gaps). | Numbers are assigned *at posting time*, under a row lock, inside the posting transaction. Drafts have no number. |
| **Period control** | Closed months cannot receive new postings. | Fiscal periods are `open`, `closed` (only users with the *post to closed period* permission), or `locked` (nobody). Plus a company-wide *lock date*. |
| **Accrual basis** | Revenue is recognised when invoiced, expenses when billed, not when cash moves. | Invoices post to AR/AP. Payments clear AR/AP. |
| **Subledger = control account** | The total of customer balances equals the Accounts Receivable GL balance (same for vendors and AP, and stock and the Inventory account). | AR/AP control accounts can only be posted through invoices/payments (`reconcilable` + `control` flags). A reconciliation report proves the match. |
| **Segregation of duties (SoD)** | The person who prepares a document should not approve it. | Approval engine refuses self-approval. Each role has an approval limit. |
| **Audit trail** | Who did what, when, from where. | Append-only audit log, SHA-256 hash-chained (tampering breaks the chain), DB trigger blocks edits/deletes. |

## 2. Chart of accounts (the "accounting tree")

The chart of accounts is a tree. Only **leaf** accounts can receive postings; parent
("group") accounts just total their children.

```
Level 1  Class         1 Assets
Level 2  Group           11 Current Assets
Level 3  Sub-group         1101 Cash and Cash Equivalents
Level 4  Ledger account      110101 Main Cash Box        <- postable
                             110102 Main Bank Account    <- postable
```

* Five root classes: **1 Assets, 2 Liabilities, 3 Equity, 4 Revenue, 5 Expenses** (cost of sales
  is under 5). Children always inherit the class of their parent.
* A child code must start with its parent's code (`1101` → `110101`), which keeps reports
  readable and lets anyone sort by code.
* Each account has an **account type** that drives behaviour: `receivable`, `payable`, `bank`,
  `cash`, `inventory`, `fixed_asset`, `tax`, `current_asset`, `current_liability`, `equity`,
  `retained_earnings`, `revenue`, `other_income`, `cost_of_sales`, `expense`, etc.
* **Normal balance**: assets & expenses are debit-normal; liabilities, equity & revenue are
  credit-normal. Reports present balances with the right sign.
* Accounts can be deactivated but not deleted once used.

## 3. Journals

A journal is a "book" grouping similar entries, each with its own numbering:

| Code | Type | Used for |
|---|---|---|
| SAL | sales | Customer invoices and credit notes |
| PUR | purchase | Vendor bills and debit notes |
| BNK | bank | Bank receipts and payments |
| CSH | cash | Cash box receipts and payments |
| STK | inventory | Stock receipts, deliveries, adjustments (valuation) |
| GEN | general | Manual entries: accruals, depreciation, corrections |
| OPN | general | Opening balances |
| CLS | general | Year-end closing |

## 4. Multi-currency

* Each company has a **base (functional) currency**. Every journal line stores the debit/credit
  in base currency **and** the original transaction currency amount and rate.
* Balance is checked in base currency.
* When a foreign-currency invoice is paid at a different exchange rate, the difference is
  posted automatically to **Realised FX Gain / Loss**.
* Rounding follows each currency's decimals (e.g. 2 for USD/SAR/AED, 3 for KWD/BHD/OMR).

## 5. Sales cycle → Accounts Receivable

```
Quotation → Sales Order (confirm; credit-limit check; approval if above limit)
          → Delivery (stock leaves)           Dr Cost of Goods Sold / Cr Inventory
          → Customer Invoice (post)           Dr Accounts Receivable
                                              Cr Revenue
                                              Cr Output VAT payable
          → Customer Receipt (post)           Dr Bank / Cr Accounts Receivable
          → Allocation to invoice(s)          reduces invoice "amount due"
Credit note = mirror of the invoice (reduces AR and revenue).
```

## 6. Purchase cycle → Accounts Payable (three-way match)

```
Purchase Order (approval by amount & level)
  → Goods Receipt                 Dr Inventory / Cr Goods Received Not Invoiced (GRNI)
  → Vendor Bill (post)            Dr GRNI (at receipt value)
                                  Dr Input VAT recoverable
                                  Dr/Cr Purchase Price Variance (bill price ≠ receipt price)
                                  Cr Accounts Payable
  → Vendor Payment                Dr Accounts Payable / Cr Bank
```
**Three-way match**: a bill line for a stocked item cannot bill more quantity than was received
on the PO, and the price may not exceed the PO price beyond a configurable tolerance without approval.
Services / non-stock items post directly to their expense account.

## 7. Inventory and costing

* **Perpetual inventory**: every stock movement posts to the GL immediately, so the Inventory
  account always equals the stock valuation report.
* Costing methods per product category:
  * **FIFO** — first in, first out. Each receipt creates a *cost layer*; issues consume the
    oldest layers first. COGS = cost of the layers consumed.
  * **Weighted average (AVCO)** — after each receipt: `new avg = (old qty × old avg + in qty × in cost) / (old qty + in qty)`.
    Issues are costed at the current average.
* Negative stock is blocked (it would make cost undefined).
* Adjustments (count differences, damage) post to Inventory vs. *Inventory Adjustment* expense.

## 8. Tax (VAT / GST)

* Tax codes have a rate, type (sale / purchase), and the GL account they post to.
* Prices can be tax-exclusive or tax-inclusive.
* Tax is computed per line and rounded per line (configurable), posted to Output VAT (sales) or
  Input VAT (purchases). A VAT report summarises taxable base and tax by code and period.

## 9. Permissions by managerial level

Permissions are fine-grained codes (e.g. `ledger.entry.post`). Roles bundle them and carry a
**level** and an **approval limit**:

| Level | Typical role | Can do |
|---|---|---|
| 1 | Accounting Clerk / Data Entry | Create drafts (entries, invoices, bills, orders). Cannot post. |
| 2 | Accountant | Post entries & invoices within approval limit. Run reports. |
| 3 | Senior Accountant / Supervisor | Approve up to a higher limit, reverse entries, manage partners & products. |
| 4 | Chief Accountant / Financial Controller | Close periods, maintain chart of accounts, post to closed periods, approve large amounts. |
| 5 | CFO / Finance Director | Unlimited approval, lock periods, year-end close. |
| — | Auditor (read-only) | View everything including audit log; change nothing. |
| — | System Administrator | Users, roles, integrations. **No** accounting permissions by default (SoD). |

Roles are assigned **per company**, so a user can be Controller in Company A and read-only in Company B.

## 10. Reports

Trial Balance (by level, with opening / movement / closing), General Ledger with running balance,
Balance Sheet, Income Statement (P&L), AR Aging & AP Aging (current, 1–30, 31–60, 61–90, 90+),
Customer / Vendor statement, Inventory Valuation, VAT summary, Subledger-to-GL reconciliation,
Audit-chain verification.

## 11. Year-end close

Closing moves the balances of all revenue and expense accounts into **Retained Earnings** via a
closing entry in the CLS journal, then the fiscal year is locked. Balance-sheet accounts carry
forward automatically (their balances are cumulative).
