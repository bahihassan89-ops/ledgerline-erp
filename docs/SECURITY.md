# Security Model

Defence in depth: each control is enforced in more than one layer, so a single bug or
misconfiguration does not compromise the books.

## 1. Identity & sessions

| Control | Implementation |
|---|---|
| Password storage | Argon2id (Django `Argon2PasswordHasher`) |
| Password policy | ≥ 12 chars, upper + lower + digit + symbol, not common, not similar to username; 90-day expiry (configurable); admin-set passwords must be changed at first login |
| Brute force | Account lockout after 5 failures for 30 min; login endpoints rate-limited (10/min per IP); constant-time username handling to prevent user enumeration |
| MFA | TOTP (RFC 6238). **Mandatory** for roles at level ≥ 4 (`ERP_MFA_REQUIRED_LEVEL`) and superusers. ±1 step drift, replay of a used code rejected. MFA seeds encrypted at rest (Fernet, separate key). |
| Tokens | Access JWT 15 min, kept in memory only (never localStorage). Refresh token rotates on every use and the old one is blacklisted, so a stolen refresh token is detected on reuse. Logout blacklists it. 30-min idle logout in the UI. |
| Integration keys | `prefix.secret`; only SHA-256 hash stored; shown once; optional expiry and IP allow-list; effective rights = **owner's rights ∩ key scopes** (a key can never exceed its creator). |

## 2. Authorization

* 55 fine-grained permissions (`ledger.entry.post`, `billing.bill.post`, ...). Every API view declares the permission per action; **undeclared actions are denied by default**.
* Roles are **per company**; a user's rights in Company A say nothing about Company B.
* **Tenant isolation**: every query is filtered by the caller's company; every foreign key a client submits is checked to belong to the same company (blocks IDOR / FK smuggling). Covered by tests.
* **Anti-escalation**: you cannot create a role with permissions you don't hold, assign a role above your level, or change your own roles.
* **Segregation of duties**: the System Administrator role has no accounting rights; approvers cannot approve documents they prepared or submitted, nor two steps of the same request; approval requires level ≥ step level **and** amount ≤ approval limit.

## 3. Integrity of the books (database-enforced)

PostgreSQL triggers (`apps/ledger/migrations/0003_integrity_triggers.py`):

1. Posted/cancelled journal entries cannot be updated or deleted.
2. Lines of posted entries cannot be inserted, updated or deleted.
3. A deferred constraint trigger re-checks at **COMMIT** that every posted entry balances, has ≥ 2 lines, matches its totals, and only touches postable accounts of the same company.
4. The audit log rejects UPDATE, DELETE and TRUNCATE.

Plus CHECK constraints (non-negative amounts, one-sided lines, non-negative stock and residuals,
quantities delivered ≤ ordered...) and unique constraints (entry numbers, vendor bill reference per vendor).

In production the application connects as `erp_app`, which **cannot** alter tables, drop or disable
triggers, or modify audit rows (verified: `must be owner of table`, `permission denied`).
Only the schema-owner role (used for migrations) can — protect that credential like a root key.

## 4. Audit trail

* Every create/update/delete of master data (accounts, partners, products, roles, memberships, taxes, periods, API keys, webhooks...) with field-level before/after values.
* Every business event: post, reverse, cancel, allocate, approve/reject, period state change, year close, login success/failure, lockout, MFA events, password change.
* Each record stores user, IP, user-agent, request id and a SHA-256 hash of (previous hash + record). **Verify integrity** in the UI recomputes the chain; any edit, deletion or insertion breaks it.
* Recommended: ship the audit log daily to write-once storage (S3 Object Lock / WORM) for independent evidence.

## 5. Transport & browser

HSTS, TLS redirect and secure cookies in production; strict Content-Security-Policy (no inline
scripts, no third-party origins), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`,
`Permissions-Policy`, `Cache-Control: no-store` on API responses. CORS limited to configured origins.
CSV exports are protected against formula injection.

## 6. Integrations

Webhooks: HTTPS only, URL checked against private/loopback/link-local ranges at creation **and**
at send time (SSRF), redirects not followed, HMAC-SHA256 signature with timestamp, transactional
outbox (events only leave after the business change commits), exponential retries.

## 7. E-invoicing and attachments

* ETA client secret and signing-agent token are encrypted at rest and never returned by the API.
  Production refuses unsigned (v0.9) mode. The eSeal private key never leaves the USB token: the
  server only sends the document to the signing agent over HTTPS with a bearer token. Run the agent
  on a hardened PC on the internal network, reachable only from the ERP server.
* Attachments: allow-listed types, content checked against the extension (magic bytes), 10 MB cap,
  stored outside any web root under random names, served only through the authenticated API with
  per-record-type permission checks; SHA-256 recorded in the audit log; files of posted records
  cannot be deleted. Include the media volume in backups.

## 8. Error handling & secrets

* Unhandled errors return a generic message; details go to server logs only.
* All secrets come from environment variables; production refuses to start with defaults, short keys or DEBUG on.
* Money is `Decimal` end to end (never float) and serialized as strings.

## 9. Operational checklist before go-live

- [ ] Long random `ERP_SECRET_KEY` and `ERP_FIELD_ENCRYPTION_KEY` stored in a secrets manager
- [ ] TLS certificate; only port 443 exposed; database not reachable from the internet
- [ ] Separate DB owner and `erp_app` credentials; owner credential not on application hosts
- [ ] Encrypted backups with point-in-time recovery; quarterly restore test
- [ ] MFA enrolled for every finance user (consider lowering `ERP_MFA_REQUIRED_LEVEL` to 1)
- [ ] Audit log export to WORM storage
- [ ] Independent penetration test
- [ ] Dependency updates monitored (`pip-audit`, `npm audit`)
- [ ] ETA: pre-production tests accepted before switching to production; signing-agent PC hardened
- [ ] Workers running: `eta_sync`, `deliver_webhooks`, scheduler (`run_recurring`)
