# Operations Guide: installing and running Ledgerline ERP

For whoever looks after the server. No programming needed: every task below is a copy-paste command
run in the installation folder (`cd ~/ledgerline-erp`).

---

## 1. Choosing where to host it

| Option | Good for | Notes |
|---|---|---|
| **Cloud server (VPS)** in or near Egypt | Most companies | Reachable from the office, home and branches; no hardware to look after. |
| **Server in your office** | Strict data-residency policy, unreliable internet | Use `ERP_TLS=internal`; reachable only inside the office network or VPN. |

**Minimum size:** 2 vCPU, 4 GB RAM, 40 GB SSD, Ubuntu 22.04 or 24.04. That comfortably serves a
team of 20+ and several companies. Grow the disk as attachments accumulate.

> ⚖ **Data protection:** Egypt's Personal Data Protection Law (151/2020) restricts transferring
> personal data (customers' and employees' names, national IDs, contacts) outside Egypt without
> a licence from the Personal Data Protection Centre. Before choosing a cloud region outside Egypt,
> confirm with your legal advisor. A server in Egypt (local cloud provider or your office) avoids the question.

**The server needs outbound HTTPS** to: Let's Encrypt (certificates), Docker Hub, PyPI and npm (only
when installing/upgrading), and the ETA (`id.eta.gov.eg`, `api.invoicing.eta.gov.eg`, and their
`preprod` equivalents) for e-invoicing.

## 2. Installing

1. **Create the server** and note its public IP address.
2. **Point a web address at it.** At your domain registrar add a DNS **A record**, e.g.
   `erp.yourcompany.com → <server IP>`. Wait until `ping erp.yourcompany.com` shows the IP.
3. **Open ports 80 and 443** in the provider's firewall (80 is only used to obtain the certificate
   and redirect to HTTPS). Keep SSH (22) restricted to your office IP if the provider allows.
4. **Run the installer** (logged in over SSH):
   ```bash
   git clone https://github.com/bahihassan89-ops/ledgerline-erp && cd ledgerline-erp
   sudo ./install.sh
   ```
   It asks for the web address, an email for certificate notices, the company name (English/Arabic),
   a short company code and the administrator's email. At the end it prints the **one-time admin password**.
5. **Store `.env` safely, off the server** (password manager, or printed in a sealed envelope in the
   safe). It holds the encryption key and backup passphrase: **without them the backups are useless.**
6. **First login** at `https://erp.yourcompany.com` with user `admin`: scan the QR code with
   Google/Microsoft Authenticator, then choose a new password (12+ characters, mixed case, digit, symbol).

Then, in the application:
* **Administration → Users:** create one user per person and assign the role matching their level
  (Clerk, Accountant, Senior Accountant, Controller, CFO, Auditor). Never share accounts: the audit
  trail records who did what.
* **Settings → Company:** tax registration number, tax office, address.
* **E-Invoicing → Connection settings:** ETA credentials (start in pre-production).
* **Imports & Go-Live:** opening trial balance, open customer/vendor invoices, opening stock.
  Follow the handbook's go-live section (`ACCOUNTING_DESIGN.md` §20).

## 3. Backups

**Automatic:** every 24 hours the `backup` service writes an encrypted copy of the database and of
all attachments to the `backups/` folder (AES-256, passphrase from `.env`). It keeps the last 14
days plus one per month for 12 months.

**You must copy them off the server.** A backup on the same machine does not survive losing the machine.
Pick one:
* **Cloud storage:** install `rclone` (`sudo apt install rclone`, `rclone config` for Google Drive,
  OneDrive, S3, Backblaze, etc.), then add a nightly job with `sudo crontab -e`:
  ```
  30 3 * * * rclone copy /root/ledgerline-erp/backups remote:ledgerline-backups
  ```
  The files are already encrypted, so the storage provider cannot read them.
* **Office NAS / another server:** `rsync -a ledgerline-erp/backups/ user@nas:/backups/ledgerline/`
  on a nightly cron.

**Back up now** (e.g. before a big import): `docker compose exec backup erp-backup once`

**Check backups are running:** `ls -lt backups | head`. The newest files should be less than a day old.

## 4. Restoring

```bash
./deploy/restore.sh                       # list available backups
./deploy/restore.sh erp-20261021-020000   # restore one (asks you to type RESTORE)
```
It first takes a safety backup of the current state, then replaces the database and attachments,
re-applies the security grants and restarts. Afterwards sign in and check:
the trial balance, and **Administration → Audit log → Verify integrity** (should say "Chain intact").

**On a brand-new server** (disaster recovery): install as in §2 but **before** running the installer
copy your saved `.env` into the folder (the installer then keeps it), copy the backup files into
`backups/`, run `sudo ./install.sh`, then `./deploy/restore.sh <latest>`.

**Practise a restore every quarter** on a spare server. An untested backup is not a backup.

## 5. Upgrading

```bash
git pull
sudo ./install.sh
```
The installer backs up first, rebuilds, applies database changes and restarts. Users may see a
brief interruption: upgrade outside working hours.

## 6. Everyday checks and troubleshooting

| Task | Command |
|---|---|
| Is everything running? | `docker compose ps` (all "Up"; backend and db "healthy") |
| Health check URL (for an uptime monitor such as UptimeRobot) | `https://erp.yourcompany.com/health/` → `{"status": "ok"}` |
| Application errors | `docker compose logs --tail 200 backend` |
| E-invoicing problems | `docker compose logs --tail 200 eta` |
| HTTPS / certificate problems | `docker compose logs --tail 100 caddy` (usually DNS not pointing at the server yet) |
| Restart everything | `docker compose restart` |
| Disk space | `df -h /` and `du -sh backups` |
| User locked out (too many wrong passwords) | Unlocks automatically after 30 minutes, or an administrator clicks **Unlock** under Administration → Users |
| User lost their phone (MFA) | An administrator clicks **Reset MFA** for that user under Administration → Users; they enrol again at next login |

Install OS security updates monthly: `sudo apt update && sudo apt upgrade -y`, then `sudo reboot`
(everything starts again by itself).

**Error tracking (optional).** By default, errors are only visible in `docker compose logs`. To get
emailed/alerted automatically when something breaks, create a free project at
[sentry.io](https://sentry.io) (one for the backend, one for the frontend, so they don't mix traces),
then add to `.env`:
```
ERP_SENTRY_DSN=<backend project DSN>
ERP_SENTRY_DSN_FRONTEND=<frontend project DSN>
```
then `sudo ./install.sh` (or `docker compose up -d --build backend frontend migrate webhooks eta scheduler`)
to rebuild with it enabled. Leave both unset to keep error tracking off; nothing is sent anywhere
until a DSN is configured. Frontend events never include the page's cookies or form field values
(`sendDefaultPii: false`); backend events never include request bodies or user PII for the same reason.

## 7. Things never to do

* **Never change `ERP_FIELD_ENCRYPTION_KEY`** in `.env`: MFA secrets and ETA credentials are encrypted with it.
* **Never lose `.env`.** Keep the off-server copy current.
* **Never delete the Docker volumes** (`docker compose down -v`, `docker volume rm …`): that deletes
  the database. `docker compose down` (without `-v`) is safe.
* **Never edit the database directly** to "fix" an entry. Posted entries are protected by the database
  itself; corrections are made with reversing entries in the application, which keeps the audit trail intact.
* **Never expose the database port** or disable the firewall.

## 8. What runs where

```
Internet ─443─▶ caddy  (HTTPS certificates, HTTP→HTTPS redirect)
                  └─▶ frontend  (the web app + security headers)
                        └─▶ backend  (API; 4 worker processes)
                              └─▶ db  (PostgreSQL 16; private network only)
Background:  eta (e-invoice submission & status, every minute)
             webhooks (notifications to connected apps)
             scheduler (recurring entries, hourly)
             backup (encrypted backups, daily)
```
