# Ledgerline ERP

An accounting-first ERP: general ledger, receivables, payables, sales, purchasing and inventory
costing, built for a professional accounting team. The accounting rules are enforced in the
application **and** in the PostgreSQL database itself.

* **Accounting handbook (Egypt edition)** → [`docs/ACCOUNTING_DESIGN.md`](docs/ACCOUNTING_DESIGN.md) — every rule, entry, control and report; start here
* **Security model** → [`docs/SECURITY.md`](docs/SECURITY.md)
* **Installing & running it** → [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
* **API** → `http://localhost:8000/api/docs/` (interactive OpenAPI) once running

## What's included

| Area | Highlights |
|---|---|
| General ledger | 4-level chart of accounts tree (class → group → sub-group → ledger), group vs. postable accounts, control accounts, journals, gapless numbering per journal/year, draft → post, reversal only (never edit), multi-currency with base-currency balancing, cost centers |
| Periods | Fiscal years with 12 monthly periods; open / closed / locked; company lock date; year-end closing to retained earnings |
| AR | Quotations, sales orders with credit-limit check, deliveries (COGS), invoices, credit notes, receipts, partial/over-time allocation, realised FX gain/loss, aging, customer statements |
| AP | Purchase orders, goods receipts (GRNI), vendor bills with **3-way match** and price-variance posting, debit notes, payments, duplicate-bill protection, aging |
| Inventory | Perpetual inventory, **FIFO** and **weighted-average** costing, cost layers, multi-warehouse, transfers, count adjustments, opening stock, negative stock blocked, valuation = GL check |
| Tax | VAT/GST codes (exclusive or inclusive), per-line rounding, VAT report (output, input, net) |
| Reports | Trial balance (by level), general ledger, balance sheet, income statement, AR/AP aging, VAT, sub-ledger ↔ GL reconciliation, inventory valuation, CSV export |
| Controls | Roles with managerial **level 0–5** and **approval limits**; multi-step approval rules by amount; segregation of duties (no self-approval); per-company permissions |
| Security | Argon2 passwords, lockout, TOTP MFA (required for level ≥ 4), 15-min JWT with rotating refresh tokens, hash-chained append-only audit log, DB integrity triggers, least-privilege DB role |
| **Egypt** | VAT 14% / 5% / zero-rated / exempt with ETA codes; withholding tax (خصم وإضافة) 1%/3%/5% with EGP 300 threshold; Form 41 and VAT return reports; **ETA e-invoicing** (validation, v1.0 documents, signing agent, submit/status/cancel, received documents); bilingual Arabic/English chart, master data and printed tax invoices |
| Treasury | Bank statement import, auto/manual matching, reconciliation statement; cheques receivable/payable lifecycle (deposit, clear, bounce) |
| Fixed assets | Register with custodian & location, straight-line / declining depreciation, monthly run, disposal gain/loss, tax depreciation per Law 91/2005 art. 25 |
| Production | BOMs, work centers, manufacturing orders with actual absorption costing (materials, labour, overhead → WIP → finished goods), scrap, cost sheets, WIP reconciliation |
| Period end | Close checklist (16 controls), FX revaluation with auto-reversal, recurring entries, auto-reversing accruals, budgets vs actual, cash flow statement, prior-year comparatives, landed costs, returns to stock |
| Go-live | CSV imports: opening trial balance, open customer/vendor items, journal spreadsheets (payroll); attachments on all key documents |
| Integration | REST API for everything (OpenAPI docs), scoped API keys, signed webhooks (outbox + retries), `Idempotency-Key` for safe retries |

## Quick start (development)

Requirements: Python 3.12+, Node 20+, PostgreSQL 14+.

```bash
# 1. database
createuser -P erp            # password: erp_dev_pw (dev only)
createdb -O erp erp

# 2. backend
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # demo company (USD) + users + a month of transactions
python manage.py seed_demo_eg     # Egyptian demo company (EGP, VAT, WHT, cheques, assets, production)
python manage.py runserver        # http://localhost:8000

# 3. frontend (new terminal)
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

### Demo users (password `Ledger!Demo2026`)

| User | Role | Level | Approval limit | Notes |
|---|---|---|---|---|
| `clerk` | Accounting Clerk | 1 | 0 | Drafts only, cannot post |
| `accountant` | Accountant | 2 | 10,000 | Posts entries, invoices, payments |
| `senior` | Senior Accountant | 3 | 50,000 | Reversals, credit notes, master data |
| `controller` | Financial Controller | 4 | 250,000 | Chart of accounts, period close — **MFA enrolment at first login** |
| `cfo` | CFO / Finance Director | 5 | unlimited | Lock periods, year-end close — **MFA** |
| `auditor` | Auditor | 0 | — | Read-only + audit trail |
| `admin` | System Administrator | 0 | — | Users & integrations, **no accounting rights** (SoD) |

### Set up your own company

```bash
python manage.py setup_company --name "Nile Trading SAE" --name-ar "النيل للتجارة" --code nile --country EG
python manage.py createsuperuser      # then assign roles in Users & Roles
python manage.py sync_chart --code nile --update-roles   # after upgrades: new accounts & permissions
```

### Background jobs (run under a process manager or cron)

```bash
python manage.py deliver_webhooks --loop     # webhook outbox
python manage.py eta_sync --loop --received  # ETA submissions, status refresh, received documents
python manage.py run_recurring               # daily: recurring journal entries
```
This creates the standard chart of accounts (≈95 accounts, editable), journals, VAT codes,
default accounts, the seven roles, default approval rules, payment terms, a warehouse and product
categories, and the current fiscal year.

## Tests

```bash
cd backend && .venv/bin/python -m pytest tests
```
71 tests cover: double-entry and period rules, DB triggers (immutability, deferred balance check,
append-only audit), FIFO/AVCO costing and rounding, procure-to-pay with 3-way match and price
variance, order-to-cash with credit notes and aging, FX loss, approvals & SoD, approval limits,
cross-company isolation, privilege-escalation attempts, login lockout, MFA with replay protection,
refresh-token rotation, API-key scope capping, idempotent retries, and every Egyptian/new module
(VAT/WHT postings and reports, ETA build/serialize/sign/submit/cancel, cheques, bank reconciliation,
depreciation & disposal, FX revaluation, returns, landed costs, budgets, recurring entries, imports,
manufacturing costing, cash flow, close checklist, attachments, first-run bootstrap, proxy-aware client IPs).

## Production

On a fresh Ubuntu 22.04/24.04 server (2 vCPU, 4 GB RAM, 40 GB disk is plenty to start):

```bash
git clone https://github.com/bahihassan89-ops/ledgerline-erp && cd ledgerline-erp
sudo ./install.sh
```
The installer sets up Docker, generates every secret into `.env`, starts the stack behind automatic
HTTPS (Caddy + Let's Encrypt), creates your first company with the Egyptian profile and its
administrator, and takes the first encrypted backup. Run it again after `git pull` to upgrade;
it backs up first. Day-to-day running, backups, restore and recovery:
**[`docs/OPERATIONS.md`](docs/OPERATIONS.md)**.

* Only Caddy (ports 80/443) is exposed; the database, API and workers sit on a private network.
* `migrate` runs as the schema owner, then `apply_db_grants` restricts the app role.
* All app processes connect as `erp_app` (DML only: cannot drop triggers, cannot alter the audit log).
* Settings refuse to start in `ERP_ENV=prod` with default/short secrets or `DEBUG` on.
* CI (`.github/workflows/ci.yml`) runs the tests, lint, build and a compose check on every push.

## Integrating other apps

```bash
# 1. In the UI: Integrations → New API key (choose minimal scopes). The key is shown once.
curl -H "Authorization: Api-Key <prefix>.<secret>" https://erp.example.com/api/partners/?is_customer=true
curl -H "Authorization: Api-Key <key>" -H "Content-Type: application/json" \
     -d '{"code":"C900","name":"New Customer","is_customer":true}' https://erp.example.com/api/partners/
```
Webhooks are POSTed with `X-ERP-Event`, `X-ERP-Delivery` (dedupe on this), `X-ERP-Timestamp` and
`X-ERP-Signature: sha256=HMAC(secret, "<timestamp>.<body>")`. Verify in Python:

```python
import hmac, hashlib, time
def verify(secret, ts, body: bytes, header):
    if abs(time.time() - int(ts)) > 300: return False          # replay window
    expected = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", header)
```

## Project layout

```
backend/
  erp/settings.py             hardened settings (env-driven)
  apps/core                   companies, currencies, gapless sequences, audit log, request context
  apps/iam                    users, roles, levels, approval limits, MFA, login, permission checks
  apps/ledger                 chart of accounts, journals, periods, posting engine, reports, DB triggers
  apps/partners               customers & vendors, payment terms, statements
  apps/inventory              products, warehouses, FIFO/AVCO costing engine
  apps/billing                invoices, bills, credit notes, payments, allocations, FX, aging
  apps/sales, apps/purchasing order flows
  apps/approvals              amount/level approval rules, SoD
  apps/integrations           API keys, webhooks (outbox + worker)
  tests/                      pytest suite
frontend/                     React + TypeScript (Vite)
docs/                         accounting design & security model
```

## Not yet built

See §22 of the handbook: B2C e-receipts, table tax, payroll, deferred tax, standard costing, MRP,
consolidation, Arabic (RTL) screen translation, email sending.
