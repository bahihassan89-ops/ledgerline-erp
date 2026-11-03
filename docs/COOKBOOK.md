# Ledgerline ERP — Cookbook

**What this is:** a task-by-task "how do I…" guide to running the business day to day in Ledgerline,
written for the people who actually click the buttons — clerks, accountants, warehouse staff,
controllers, and administrators. Each recipe is: what you need before you start → the exact clicks →
what to check afterwards.

**What this is not:** the accounting rulebook (why an entry looks the way it does, which account it
hits, the legal references) — that's **[`ACCOUNTING_DESIGN.md`](ACCOUNTING_DESIGN.md)**, especially
its Appendix A (journal entry cookbook) and Appendix B (month-end procedure). Nor is it the server
install/backup/restore guide — that's **[`OPERATIONS.md`](OPERATIONS.md)**. This document sits
between them: it's the screen-level guide for the browser, every module, every day.

Menu items below are written as they appear in the left sidebar, grouped exactly as the app groups
them. If you don't see a menu item mentioned here, you don't hold the permission for it — ask your
administrator (§13).

---

## Contents

1. Getting started
2. Dashboard & notifications
3. Chart of accounts & journal entries
4. The sales cycle (quotations to cash)
5. The purchase cycle (orders to payment)
6. Customers & vendors
7. Receipts, payments and cheques
8. Inventory, stock and landed costs
9. Manufacturing
10. Fixed assets
11. Banking & reconciliation
12. ETA e-invoicing (Egypt)
13. Reports
14. Period end: close checklist, revaluation, imports
15. Budgets & recurring entries
16. Approvals
17. Backups — on-demand & scheduled
18. Administration: users, roles, settings, integrations, audit
19. Multi-company / group features
20. Troubleshooting — "why can't I…"
21. Keyboard & workflow shortcuts reference
22. Quick-reference status glossary

---

## 1. Getting started

**Log in.** Go to your company's web address. Enter your username and password (given to you by
your administrator — never shared between people, so the audit trail can tell who did what).

**Enrol MFA (first login, roles level ≥ 1).** You'll be shown a QR code. Scan it with Google
Authenticator or Microsoft Authenticator, type the 6-digit code it shows, and you're in. If you lose
your phone later, ask an administrator to click **Reset MFA** for you under **Users & Roles**, then
enrol again.

**Change your password any time**: bottom-left of the sidebar → **Password**.

**Switch company.** If you belong to more than one company, a dropdown appears at the top of the
sidebar — pick the one you're working in. Everything you see (accounts, documents, reports) is
scoped to that company; nothing crosses over by accident.

**Find your way around.** The sidebar is grouped the way your work is grouped: Overview, Accounting,
Period End, Treasury, Receivables, Payables, Inventory, Production, Fixed Assets & Tax,
Administration, and (for CEOs/ops only) Central / Admin. You only see the groups and items you have
permission for — an inventory clerk's sidebar looks very different from a controller's, by design.

**The topbar** always shows which company and screen you're on, your currency, an **MFA off**
warning badge if you haven't enrolled yet, and the notification bell (§2). A yellow **TRIAL SYSTEM**
banner means the company is a demo/trial — don't enter real data in it.

---

## 2. Dashboard & notifications

**Dashboard** (home screen) gives you, at a glance: cash & bank, receivables, payables, inventory
value, this month's revenue, year-to-date net profit, how many items are waiting for your approval,
and how many journal entries are still in draft. A 12-month income-vs-expense chart sits below; overdue
receivables are highlighted in red so they can't be missed. Click any KPI to jump straight to the
underlying report or list.

**Notifications** (bell icon, top right): shows an unread count. Click it for the list — approvals
waiting for you, documents ETA rejected, cheques coming due, and similar alerts. Click a notification
to jump straight to the record; it's marked read automatically. **Mark all read** clears the badge.
**Enable push** turns on browser push notifications (useful on a phone/tablet with the site added to
the home screen) so you don't have to keep the tab open to be alerted.

---

## 3. Chart of accounts & journal entries

### Add a new account
1. **Accounting → Chart of Accounts.**
2. Find the parent group in the tree, click **+ Sub** on it (or **+ New account** at the top and pick
   the parent yourself).
3. Fill in **Code** (must start with the parent's code), **Name** in English *and* Arabic, the
   **Type** (only matters for leaf/postable accounts — see the type table in
   `ACCOUNTING_DESIGN.md` §2), cash-flow classification if relevant, and whether it **requires a
   cost center**.
4. Save. You can rename, move within level, or deactivate an account later — but once it has any
   postings you cannot change its class, turn it into a group, or delete it.

Use **Search** and **Show up to level** to manage a large tree; **Ledger** on any row jumps to that
account's general ledger (running balance, drill-down to every source document).

### Post a manual journal entry
Used for anything that isn't generated automatically by a document (accruals, corrections, payroll
summaries, adjustments):
1. **Accounting → Journal Entries → + New journal entry.**
2. Pick the **Journal** (usually GEN), **Date**, and optionally a **Reference** and **Narration**.
3. Add lines: **Account**, optionally **Partner** / **Cost center** / line **Description**, and
   either a **Debit** or a **Credit** amount (never both on the same line). The balance checker tells
   you if debits ≠ credits.
4. If this entry should automatically reverse itself later (e.g. a month-end accrual), set
   **Auto-reverse date** — the reversal posts itself on that date, no extra step needed.
5. **Save** as a draft, then **Post**. Once posted it cannot be edited or deleted — only reversed
   (button appears on posted entries) and, if it needs approval first, it sits as **to_approve**
   until someone with the right level approves it (§16).

Entries generated by the system (invoices, bills, payments, depreciation, etc.) show up in this list
too, read-only, so you always have one place to see everything that hit the ledger.

---

## 4. The sales cycle (quotations to cash)

```
Quotation → Sales Order (confirm) → Delivery → Customer Invoice → Receipt
```

### Create and confirm a sales order
1. **Receivables → Sales Orders → + New Quotation.**
2. Pick the **Customer**, **Order date**, **Warehouse**, **Currency** and **Payment terms**;
   optionally the customer's own PO reference.
3. Add lines: **Product**, quantity, unit price (defaults from the product/customer), discount %,
   tax code.
4. **Save**, then **Confirm order**. If the customer has a credit limit and this order would push
   them over it (open receivables + confirmed-not-invoiced orders + this order), confirmation is
   blocked — raise the limit or get the order approved, don't just "work around" the block.
5. Depending on the order's value, it may need approval first (**to_approve** state) before it can be
   confirmed — it'll show up in the approver's **Approvals** inbox.

### Deliver and invoice
1. Open the confirmed order, click **Deliver**, enter the quantities actually shipped (partial
   deliveries are fine — the order's **Delivered %** column tracks progress) and the date. This posts
   stock out at cost (you don't need to do anything in Inventory separately).
2. Click **Create invoice**. It pre-fills from what's been delivered (services: mark as delivered
   manually since there's no physical stock move). Review the lines, VAT and withholding codes, then
   **Save** → **Post**.
3. If e-invoicing is enabled or you serve customers in Egypt who need an ETA document, posting checks
   everything ETA would reject (tax numbers, addresses, item codes) **before** letting you post — fix
   anything it flags.

### Credit notes, debit notes, and returns
On a posted invoice: **Create refund/debit note**. Choose **credit note** (reduces what the customer
owes — for returns, pricing errors, discounts) or **debit note** (additional charges). Credit notes
offer **return to stock** — tick it if the goods are physically coming back; otherwise it's a pure
value adjustment with no stock movement.

### Collect payment
1. **Receivables → Customer Receipts → + New receipt.**
2. Pick the **Customer**, **Bank/cash journal**, **Date**, **Amount**, and the **Method** (bank
   transfer, cash, or cheque — cheque adds cheque number, drawee bank, and due date fields).
3. In the allocation table, tick which open invoices this pays (or use **Auto-apply oldest first** if
   you don't need to pick). Partial and split-across-invoices payments are both fine.
4. **Post & apply.** If there's anything left unallocated, it stays as an open credit you can apply
   later from the same screen (**Apply to invoices**).

### Check who owes what
**Receivables → Customers → [pick a customer] → Statement** shows a running balance with every
transaction; **Reports → AR Aging** (as-of any date) buckets everyone's balance into current /
1–30 / 31–60 / 61–90 / 90+ days overdue, with unapplied receipts shown separately so they don't get
missed.

---

## 5. The purchase cycle (orders to payment)

```
RFQ → Purchase Order (approval by amount & level) → Goods Receipt → Vendor Bill (3-way match) → Payment
```

### Raise and approve a purchase order
1. **Payables → Purchase Orders → + New RFQ.**
2. Pick the **Vendor**, **Order date**, **Warehouse**, **Currency**, **Payment terms**.
3. Add lines (Product, quantity, unit price, discount, tax). **Save**, then **Confirm order**.
4. Orders above your configured threshold route through the approval chain automatically (e.g.
   ≥ EGP 500,000 to the Controller, ≥ 2,500,000 to the Controller then CFO — configured under
   **Settings → Approval rules**) — you'll see it sitting at **to_approve** until that happens.

### Receive goods and bill the vendor
1. On the confirmed order, click **Receive goods**, enter quantities actually received and the date.
   This posts Dr Inventory / Cr GRNI automatically.
2. When the vendor's bill arrives: open the order, **Create bill** (or start a bill directly and
   reference the PO). The system **3-way matches**: billed quantity can't exceed received-minus-
   already-billed, and unit price can't exceed the PO price by more than the configured tolerance %
   — if it's over tolerance, only a Senior Accountant/Controller can still post it (this is
   deliberate, not a bug: it stops silent overbilling).
3. Review tax and **withholding** codes on the bill (defaults come from the vendor's master record),
   **Save** → **Post**. The vendor is recorded as owed the *net-of-withholding* amount; withholding
   payable is booked for you.
4. The same vendor bill number can never be entered twice for the same vendor — the system will
   refuse it (this has caught genuine double-entry mistakes before; if you hit it, you probably
   already posted this bill).

### Pay a vendor
1. **Payables → Vendor Payments → + New payment.**
2. Pick **Vendor**, **Bank/cash journal**, **Date**, **Amount**, **Method** (transfer/cash/cheque —
   cheque issued adds cheque number and due date).
3. Allocate against open bills (same allocation table as customer receipts), **Post & apply**.

### Debit notes & returns to a vendor
On a posted bill: create a **vendor debit note**. With **return to stock** ticked, the goods leave
inventory at their current cost, the GRNI/price gap goes to Purchase Price Variance, and the PO's
received/billed quantities are reduced so future 3-way matching stays correct.

### Check what you owe
**Payables → Vendors → [pick a vendor] → Statement**; **Reports → AP Aging** for the bucketed view
across all vendors.

---

## 6. Customers & vendors

**Receivables → Customers** / **Payables → Vendors → + New customer/vendor.** Fill in: **Code**,
**Name** in English and Arabic, **Tax/VAT number**, contact details, **Default currency**,
**Payment terms**, address. For Egypt-specific use: the **ETA receiver type** (B = business with a
9-digit tax number, P = individual — enter a national ID once invoices to them pass the configured
threshold, F = foreign), full governorate/city/street/building address for e-invoicing, and a default
**withholding tax** code so it's pre-filled on every bill/invoice automatically.

**Credit control (customers only):** set a **Credit limit** and tick **Block sales above limit** to
have new orders blocked once the customer's open receivables + unbilled confirmed orders would exceed
it. **On hold** blocks all new orders regardless of amount — use it the moment a customer becomes a
collection problem, before more orders pile up. Changing these settings needs a dedicated permission
on purpose — don't be surprised if a clerk can't touch them.

**Intercompany:** if this partner is actually another company in your group, link it under
**Linked company** — it's what powers the intercompany reconciliation in §19.

---

## 7. Receipts, payments and cheques

Receipts and vendor payments are covered in §§4–5. Cheques get their own lifecycle because they sit in
limbo between "paid/received" and "cleared":

| Stage | What you do | What posts |
|---|---|---|
| Customer gives you a cheque / you issue one | Record the receipt/payment as usual, method = cheque | Moves the invoice/bill out of AR/AP into **Notes receivable/payable** — not yet cash |
| Deposited for collection | **Payments → Cheques → Deposit** | Status only, no GL entry yet |
| Bank clears it | **Cleared** | Dr/Cr Bank against Notes receivable/payable (plus any FX difference) |
| Bounces or you stop one | **Bounced / Stop** | Reverses the cheque; the original invoice/bill reopens as unpaid |
| You pass a received cheque to a vendor instead of depositing it | **Endorse** | Moves it from your Notes receivable to the vendor, no bank movement |

**Cheque instalment plans** (**Payments → Instalment Cheque Plans**): generate a schedule of equal
(or custom) post-dated cheques against a balance due — **Generate** proposes the dates/amounts, review
and **Save**; each instalment then follows the normal cheque lifecycle above as it comes due.

**Guarantee cheques** (**Payments → Guarantee Cheques**): cheques held as security rather than
payment. **Return** gives it back once it's no longer needed; **Forfeit** if you're entitled to cash
it because the guarantee was called.

The **cheque portfolio** (same Cheques screen) lists everything by due date and status, so overdue
uncleared cheques — yours or your customers' — can't be quietly forgotten; they also show up on the
close checklist (§14).

---

## 8. Inventory, stock and landed costs

### Add a product
**Inventory → Products → + New product.** Fill in **SKU**, **Name** (English/Arabic), **Type**
(Stockable, Consumable, or Service — services skip stock tracking entirely), **Category** (which
determines FIFO vs weighted-average costing — see `ACCOUNTING_DESIGN.md` §8.2; it can't be changed
once the category has stock), **Unit of measure**, sale/purchase prices, **Reorder level**, default
tax codes, and the ETA item type/code if e-invoicing is on.

### Opening stock, counts, and transfers
- **Opening stock** (go-live only, or a brand-new product): quantity and unit cost per warehouse —
  posts against Opening Balance Equity.
- **Count adjustment**: enter the counted quantity; the gain/loss posts automatically to Inventory
  Adjustments & Shrinkage.
- **Transfer**: move quantity between warehouses — no GL effect, it's the same company's stock.
- **Consignment in/out**: for stock held on consignment (either direction) — tagged separately so it
  doesn't mix with owned inventory in valuation.

Products below their **Reorder level** are flagged in the product list so purchasing knows to act
before a stockout, not after.

### Landed costs (import clearing, freight, customs)
1. First post the customs/freight/clearing bill(s) as normal vendor bills, with the line account set
   to **Landed Costs Clearing** (so they don't hit an expense account directly).
2. **Inventory → Landed Costs → + New landed cost.** Pick the receipt(s) this cost relates to, the
   total **Amount**, and the **allocation method** (by value or by quantity).
3. **Save** as draft, review the proposed split, then **Allocate & post** — the share of goods still
   on hand raises their inventory cost, the share already sold goes straight to COGS, and Landed
   Costs Clearing nets to zero.

**Checking yourself:** Stock valuation report should always equal the Inventory GL accounts — if it
doesn't, something was posted around the stock system rather than through it; the close checklist
(§14) flags this automatically every month so it's never a surprise at year end.

---

## 9. Manufacturing

```
Bill of materials → Manufacturing Order: confirm → issue components → record labour → produce
```

### Set up a BOM and work center once
- **Production → Manufacturing → Work Centers → + New**: code, name, **labour rate/hour**,
  **overhead rate/hour** (budgeted overhead ÷ budgeted hours), cost center.
- **Production → Manufacturing → Bills of Materials → + New**: finished product, output quantity,
  **components** (product, quantity per output, expected loss %), **operations** (sequence, name,
  work center, minutes per output).

### Run an order
1. **Production → Manufacturing Orders → + Manufacturing order.** Pick the product, its BOM, the
   quantity to produce, warehouse, planned date. **Save** → **Confirm**.
2. **Issue components** — confirm (or adjust) the quantities consumed; this moves raw materials into
   Work in Progress at their current FIFO/average cost.
3. **Record labour hours** — enter actual minutes per operation as work happens (not the planned
   minutes; actuals drive the real cost).
4. **Produce** — enter the good quantity and any scrap, and tick **finish order** once there's
   nothing left to make. Partial production is fine for long runs; finishing clears all remaining WIP
   for the order.

**Work in Progress** tab shows every open order's WIP balance, reconciled to the WIP GL account.
**Cost sheet** on a finished order compares planned vs actual material/labour/overhead and the
resulting variance — useful for spotting a BOM that's drifted from reality.

---

## 10. Fixed assets

### Register an asset
Two ways: **Fixed Assets & Tax → Fixed Assets → + New asset** (manually), or — faster — just post the
vendor bill for the purchase with its line booked to a fixed-asset account; it shows up under
**Unregistered purchases** waiting to be turned into a proper asset record (code, category, serial
number, location, **custodian**, cost center, in-service date, useful life, method). The close
checklist nags you if a capital purchase sits unregistered too long.

### Depreciate, every month
**Fixed Assets & Tax → Fixed Assets → Run depreciation**, pick the date (normally month end). It
posts one entry per asset (Dr Depreciation expense / Cr Accumulated depreciation) using whichever
method each asset uses — straight line or declining balance. Do this as part of month-end close
(§14) before anyone looks at the P&L for the month; it's a blocking item on the checklist for exactly
that reason.

### Dispose of or sell an asset
Open the asset → **Dispose**. It first posts depreciation up to the disposal date (so the asset is
current), then asks for the **proceeds** and a reason; it posts cost off, accumulated depreciation
off, the proceeds, and the resulting gain or loss automatically.

### Tax depreciation
The **Tax depreciation** tab gives the same register valued under Law 91/2005 art. 25 pooled rates
(buildings 5%, most equipment 25%, computers/software 50%) for your tax return reconciliation — it
runs alongside, not instead of, the accounting depreciation above.

---

## 11. Banking & reconciliation

1. **Treasury → Bank Reconciliation → Import statement.** Upload the bank's CSV (date, description,
   reference, amount or debit/credit columns), pick the bank journal, and enter the opening/closing
   balance from the statement — the import is refused if they don't add up, which catches a bad CSV
   before it pollutes your books.
2. **Auto-match** pairs statement lines to unmatched posted GL lines on that account (same amount,
   within ~10 days, preferring a reference match).
3. For anything left: **Match…** against a specific book entry, or **Create entry** for things only
   the bank told you about (bank charges, direct debits, interest) — pick the counter account and,
   if it's receivable/payable, the partner.
4. Once every line is matched, **Mark reconciled**.
5. **Reconciliation statement** tab shows, per bank account: balance per bank + deposits in transit −
   outstanding payments = adjusted bank balance, against balance per books ± unbooked statement
   items — the two adjusted figures must match exactly, or something's still missing.

Posted GL lines are never changed by matching — matching only records the link, so your journal
entries stay exactly as posted.

---

## 12. ETA e-invoicing (Egypt)

**One-time setup** (**Fixed Assets & Tax → E-Invoicing (ETA) → Connection settings**): enable
e-invoicing, start in **pre-production**, enter the client ID/secret from the ETA portal, taxpayer
activity code, branch ID, and the issuer address exactly as registered. Register your products' item
codes on the ETA portal first; customers need a tax/national ID per their receiver type (§6).

**Day to day:** posting a customer invoice, credit note or debit note checks everything ETA would
reject *before* letting you post — fix whatever it flags there, not after. Once posted, the
**Submissions** tab shows the document moving through pending → submitted → valid (gets a UUID, long
ID and a public URL/QR code, printed on the invoice) or invalid/rejected (shown with the exact
validation errors). **Fix data & resubmit** after correcting the master data that caused the
rejection; amount errors are corrected with a credit note instead, never by resubmitting a changed
amount. **Cancel on ETA** requests cancellation of a valid document with a reason.

**Received documents** tab pulls e-invoices vendors issued to you and matches them to your posted
bills; anything unmatched is either a bill you haven't entered yet or one you should dispute — and
until it's resolved, that input VAT is at risk, so don't let this tab pile up.

Switch **Environment** to production only after ETA confirms your pre-production test documents.

---

## 13. Reports

**Accounting → Financial Reports** (also **Reports** generally) gives you 14+ report types: Trial
Balance, Balance Sheet, Income Statement, Cash Flow, General Ledger, AR/AP Aging, VAT Return, WHT
Form 41, WHT by Customers, sub-ledger Reconciliation, Inventory Valuation, Liquidity, ECL
Receivables, Petty Cash. Most take a date range; point-in-time reports (Balance Sheet, aging,
liquidity) take an **as-of** date instead. Tick **Compare with prior year** on the Balance Sheet or
Income Statement to see last year's column alongside this year's. Use **Account level** to collapse
the report to groups instead of every leaf account. Every report **prints** as a clean document and
**exports to CSV** (safe against spreadsheet formula injection, so it's fine to open in Excel).
Clicking an account row on most reports drills straight down into its general ledger.

For "is everything actually reconciled" questions specifically, go straight to **Reconciliation**
(AR/AP sub-ledger vs control account) and **Inventory Valuation** (vs the inventory GL accounts) —
these are the same checks the close checklist runs automatically every month.

---

## 14. Period end: close checklist, revaluation, imports

### Run the close checklist (every month)
**Period End → Close Checklist**, pick the period. It re-runs every control live: unposted entries,
depreciation run, bank statements matched, AR/AP ↔ GL agreement, stock valuation ↔ GL agreement, WIP
↔ GL agreement, overdue goods-received-not-billed, un-revalued foreign balances, invalid ETA
documents, Opening Balance Equity not yet cleared (go-live only), and the trial balance itself.
✗ items block closing the period; ! items are warnings worth a look but don't block. Work down the
list — each item links straight to what needs fixing. The full step-by-step order to do this in is
`ACCOUNTING_DESIGN.md` Appendix B.

### Revalue foreign-currency balances
**Period End → FX Revaluation**, pick the closing date and rate source. It restates open
foreign-currency invoices/bills and foreign-currency bank/cash balances at the closing rate, posts
the difference to Unrealised FX gain/loss, and **automatically reverses it the next day** — so you
never have to remember to undo it, and settlement still measures against the real document rate.
Running it twice for the same date is blocked on purpose.

### Close and lock a period
Once the checklist is clean: **Administration → Fiscal Periods → Close period** (Controller level).
**Lock** it once the books are truly final and won't be touched again (CFO level, irreversible). At
year end, close every period in the year and run **Year-end close**, which sweeps revenue and
expense balances into Retained Earnings — the Income Statement still shows the year's result; only
the balance carried forward moves.

### Go-live imports
**Period End → Imports & Go-Live**, four tabs: **Opening trial balance** (every account except
receivables/payables/inventory/WIP — the difference parks in Opening Balance Equity, which must hit
zero by the time you're done), **Open customer items** / **Open vendor items** (each unpaid invoice/
bill with its real number and due date, so aging works from day one), and **Journal from
spreadsheet** (e.g. a payroll summary — creates a draft entry for review, never auto-posts). Each tab
has a **Download template** button and shows a preview of the first rows before you commit. The
checklist in `ACCOUNTING_DESIGN.md` §20 tells you exactly what "done" looks like.

---

## 15. Budgets & recurring entries

### Set a budget
**Accounting → Budgets → + New budget.** Pick the fiscal year. On **Budget Lines**, fill the grid
(accounts × months) — **Fill** can spread an annual figure evenly, or type month by month. While
**draft**, you can keep editing; **Approve and freeze** locks it so actuals can be compared against a
number nobody can quietly change later. **Budget vs actual** (same page) shows the variance by
account for any date range, with favourable/unfavourable flagged (income above budget, or expense
below it, is favourable).

### Set up a recurring entry
**Accounting → Recurring Entries → + New template.** Pick the journal, frequency (monthly/quarterly/
yearly), the first **next date**, optional **end date**, and whether it should **auto-post** (only
works if the template's owner holds posting rights — otherwise it always lands as a draft for
someone to check). Fill the lines like a normal journal entry. The scheduler generates due entries
daily by itself; **Generate due now** runs it on demand if you don't want to wait (e.g. you're closing
early this month). Set **Auto-reverse after (days)** for things like a prepayment amortised over
several months.

---

## 16. Approvals

**Overview → Approvals.** Three tabs: **Waiting for me** (act now), **All pending** (visibility
across the company), **History** (past decisions). Each item shows the approval chain (e.g.
Controller → CFO) and where it currently sits. **Approve** (optional comment) or **Reject** (reason
required — the submitter needs to know why). You cannot approve something you submitted yourself, and
you cannot approve the same document twice at different steps — the system enforces this, it's not
just a convention. If you don't see something you were told to approve, you likely don't hold the
required level or your approval limit is below the amount — check with your administrator rather than
asking someone else to approve it for you, which would defeat the point of the control.

---

## 17. Backups — on-demand & scheduled

**Central / Admin → Backups** (visible to whoever holds `ops.backup.manage` — normally the CEO/
platform owner and the Controller/CFO, not every user).

The page shows: **last backup** time, **status** (OK / Failed / "In progress…"), who/what triggered
it (Manual, Scheduled, or Command line) and by whom, and the backup's **size**.

**Back up now** requests an immediate, out-of-cycle backup instead of waiting for the next scheduled
run — use it right before a big import, a risky bulk change, or simply whenever you want extra peace
of mind. Click it, the button changes to **Backup in progress…**, and the page polls itself every 3
seconds until it's done — you don't need to refresh. Under the hood this drops a request into the
backup service, which picks it up within about 5 seconds, runs the same AES-256-encrypted dump of the
database and attachments as a scheduled backup, and reports back; it's logged to the audit trail like
any other action, so there's a record of who asked for it and when.

**Scheduling is already running in the background** (every 24 hours by default) — this button is for
the "I need one right now" case, it doesn't replace the schedule. An administrator can change how
often the schedule runs and how many sets are kept (`BACKUP_INTERVAL_HOURS`, `BACKUP_KEEP_DAILY`,
`BACKUP_KEEP_MONTHS` in `.env` — see `OPERATIONS.md` §3) and can tell this screen about a configured
retention window; the sub-text on the page states plainly if no cloud destination is wired up yet, so
nobody assumes backups are leaving the server when they aren't.

**No cloud destination is configured yet** — backups are encrypted and kept on the server, pruned
automatically (14 daily + 1/month for 12 months). The architecture is ready to add one (push from the
end of `deploy/backup/backup.sh`, after encryption — see the comment at the top of that file) the
moment a destination (S3, Backblaze, a second server, etc.) is chosen; until then, **copy backups off
the server yourself** — see `OPERATIONS.md` §3 for the `rclone`/`rsync` one-liners. A backup that only
lives on the machine that might fail is not a backup.

**Restoring** from any backup set is a server-side operation (`./deploy/restore.sh`), not a button in
the app — see `OPERATIONS.md` §4. Practise it every quarter on a spare server.

---

## 18. Administration: users, roles, settings, integrations, audit

### Create a user and assign a role
**Administration → Users & Roles → Users → + New user.** Username, email, full name, tick **Active**,
assign one or more **Roles** (each role carries a level 0–5 and an approval limit — see the table in
`ACCOUNTING_DESIGN.md` §18). Never share one login between two people — the audit trail and the
approval engine both depend on knowing exactly who did what. **Lock/unlock**, **enable/disable MFA**
and **reset password** are available per user from the same screen for the inevitable "I'm locked
out" / "I lost my phone" calls.

### Create or edit a role
**Users & Roles → Roles → + New role.** Name, **Level** (0–5 — drives what the role can approve, not
just what it can see), tick permissions from the module × action tree, set the **approval limit**.
Nobody can grant a permission or assign a role above their own level, and nobody can edit their own
roles — this is enforced, not a suggestion, so don't be surprised if the option simply isn't there.

### Company-wide accounting setup
**Administration → Accounting Setup**, seven tabs: **Company** (legal/trading names, tax numbers,
base currency, books-locked-up-to date, bill tolerance %), **Default accounts** (AR/AP/GRNI/PPV/FX/
retained earnings, default journals, ECL provision rates by aging bucket), **Journals** (code, type,
bank/cash link, manual-entry allowed or not, petty-cash float), **Taxes** (VAT and withholding codes,
rates, ETA codes, minimum base, price-inclusive flag), **Exchange rates** (per currency, per
effective date), **Approval rules** (document type, from-amount, which levels must approve in order),
**Cost centers**.

### Integrations
**Administration → Integrations → New API key**: pick the minimal scopes it actually needs (never
grant everything by default), the key is shown exactly once — copy it immediately, it can't be
retrieved again, only revoked and replaced. Webhooks (same page) notify other systems with a signed,
HMAC-verified payload and automatic retries — see the signature-verification snippet in the main
`README.md` if you're integrating something yourself.

### Audit trail
**Administration → Audit Trail**: every master-data change (with before/after values), every posting
event, every login, searchable by user/date/object. **Verify integrity** recomputes the SHA-256
hash chain and should always say "Chain intact" — if it ever doesn't, treat it as a security incident,
not a glitch.

---

## 19. Multi-company / group features

(**Central / Admin** group — visible only to the CEO/platform-owner role or holders of the specific
global permission named, same pattern as Backups in §17.)

**Company Provisioning**: three steps — basic info (names, code, country, base currency, VAT rate,
fiscal year start) → modules (toggle which ones this company uses) → review & submit. It sits as
**pending_approval** until a second person signs off (segregation of duties even for creating a whole
company).

**Company Approvals**: review a submitted company, **Approve & activate** (it goes live) or
**Reject** with a reason.

**Team Activity**: who's online right now, in which company, doing what (module/section), how long
they've been active today, and when they were last seen — click anyone for their history. This is
the presence data the heartbeat in the topbar quietly reports every 30 seconds while a tab is open.

**Group Reports**: **Intercompany Reconciliation** compares each linked-partner pair's balance across
the two companies' books (should **Agree**; **Differs** flags a timing or posting mismatch to chase),
and **Consolidated** gives a combined trial balance across every company in the group, with a
by-company breakdown and the same reconciliation badges you'd expect at single-company level.

---

## 20. Troubleshooting — "why can't I…"

| Symptom | Most likely reason | What to do |
|---|---|---|
| A menu item I need isn't in the sidebar | You don't hold the permission for it | Ask your administrator to add the right role/permission — don't ask someone else to do the task "for" you, that defeats the audit trail |
| "Confirm order" is blocked on a sales order | Customer is over their credit limit or **On hold** | Get the limit raised/order approved, or clear the hold — both are deliberate controls, not bugs |
| Can't post a vendor bill at the price entered | It's outside the 3-way-match tolerance % | Either the price is genuinely wrong (fix it), or a Senior Accountant/Controller needs to post it |
| "Vendor bill ref already exists" | That bill number is already entered for this vendor | Check you (or someone else) didn't already post it — duplicate protection is doing its job |
| Can't edit or delete a posted journal entry / invoice / bill | Posted records are immutable by design, enforced at the database level | Post a **reversal** / credit note / debit note instead — never try to "fix" it with direct access |
| An approval I was told to action isn't in my **Waiting for me** list | You don't hold the required level, or your approval limit is below the amount | Check with your administrator; don't have someone else approve on your behalf |
| A report doesn't match another report (e.g. stock valuation vs GL) | Something was posted around the sub-ledger instead of through it | Run the **Close Checklist** (§14) — it's built to surface exactly this before month end, not after |
| FX revaluation entry is still there next month | It reverses automatically the *next day*, not next month — check you ran it on the right date | Re-run with the correct closing date; running it twice for the same date is blocked |
| "Back up now" button stays on "In progress…" | A backup (manual or scheduled) is already running | Wait — it polls itself every 3 seconds and will update when done; check `docker compose logs backup` on the server if it never clears |
| I enrolled MFA but lost the phone | Normal — doesn't mean anything is broken | Ask an administrator to **Reset MFA** for you, then enrol again on the new device |

---

## 21. Keyboard & workflow shortcuts reference

Ledgerline doesn't (yet) have power-user keyboard shortcuts beyond the browser's own (search-in-page,
back/forward). The workflow shortcuts worth knowing instead:

* **Auto-apply oldest first** on any receipt/payment allocation screen, instead of ticking invoices
  one by one.
* **Generate due now** on Recurring Entries, instead of waiting for the daily scheduler.
* **Fill** on a budget grid, instead of typing the same figure into 12 month columns.
* **Download template** on every Imports tab — always start from the template rather than guessing
  the CSV column order.
* Clicking any account/customer/vendor row on almost every report **drills down** to the underlying
  ledger/statement — faster than re-running a different report from scratch.

---

## 22. Quick-reference status glossary

| Entity | States you'll see |
|---|---|
| Sales/purchase orders | draft → to_approve → approved → confirmed → done (or cancelled) |
| Invoices / bills | draft → to_approve → posted (or cancelled); payment: not_paid → partial → paid |
| Payments | draft → posted |
| Cheques | in_hand → deposited → cleared (or issued → cleared; or bounced at any point) |
| Journal entries | draft → to_approve → approved → posted |
| Fixed assets | draft → running → fully_depreciated (or disposed) |
| Fiscal periods | open → closed → locked (locking is permanent) |
| Budgets | draft (editable) → approved (frozen) |
| Manufacturing orders | draft → confirmed → in_progress → done (or cancelled) |
| Company provisioning | draft → pending_approval → active (or rejected) |
| ETA documents | pending → submitted → valid (or invalid / rejected / cancel_requested → cancelled) |

---

*This cookbook describes the screens as built; if something on screen doesn't match what's written
here, trust the screen and tell an administrator so this document can be corrected.*
