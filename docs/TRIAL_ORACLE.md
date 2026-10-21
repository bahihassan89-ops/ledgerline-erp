# Trial server on Oracle Cloud (free)

A step-by-step guide to putting a **trial** copy of Ledgerline ERP on the internet at no cost, so the
team can test it from anywhere before you choose a paid provider. About 30–45 minutes, most of it
waiting. No technical background needed and nothing to type: the server installs itself.

> **Demo data only.** The trial server is for testing with the demo company (Nile Industries SAE).
> Do not enter real customer, employee or financial data: Oracle's free regions are outside Egypt,
> and the trial has not been through the go-live checks. The app shows a banner as a reminder.

---

## Step 1: Create the Oracle Cloud account (10 min)

1. Go to **oracle.com/cloud/free** → **Start for free**.
2. Fill in your details. For **Home Region**, pick one near Egypt, e.g. **UAE East (Dubai)**,
   **Saudi Arabia West (Jeddah)** or **Germany Central (Frankfurt)**. *This cannot be changed later.*
3. Add a credit or debit card. **Oracle uses it to verify your identity and does not charge it** for
   "Always Free" resources. You may see a small temporary hold that is released.
4. Wait for the "account ready" email (usually minutes, sometimes an hour), then sign in.

## Step 2: Create the server, which installs itself (10 min of clicking)

1. In the Oracle console: **☰ menu → Compute → Instances → Create instance**.
2. **Name:** `ledgerline-trial`.
3. **Image and shape** → **Edit**:
   * **Change image** → **Canonical Ubuntu** → version **24.04** → Select.
   * **Change shape** → **Ampere** → **VM.Standard.A1.Flex** → **2 OCPUs, 12 GB memory** → Select.
     (The label "Always Free-eligible" must be visible.)
4. **Networking:** keep "Create new virtual cloud network" and "Create new public subnet", and make
   sure **Assign a public IPv4 address** is on.
5. **Add SSH keys:** choose **Generate a key pair for me** and click **Save private key**. You will
   probably never need it, but keep the file: it is the emergency key to your server.
6. **The self-install script:** click **Show advanced options** (or **Advanced options**) →
   **Management** → **Initialization script** → **Paste cloud-init script**, and paste the whole
   contents of [`deploy/oracle-cloud-init.sh`](../deploy/oracle-cloud-init.sh) with your two values
   filled in at the top: your **email** and the **trial password** every tester will use
   (12+ characters with upper and lower case, a digit and a symbol).
7. Click **Create**. After a minute or two the status turns green **RUNNING**. Note the
   **Public IP address** (e.g. `130.61.25.17`).

> **"Out of capacity" error?** Free servers are popular. Try again with **1 OCPU / 6 GB**, choose a
> different **Availability domain** in the Placement section, or retry a few hours later.

## Step 3: Open the web ports (3 min)

1. On the instance page, under **Primary VNIC**, click the **Subnet** link.
2. Open **Security Lists** (sometimes under the **Security** tab) → **Default Security List** →
   **Add Ingress Rules**.
3. Fill in: **Source CIDR** `0.0.0.0/0`, **IP Protocol** `TCP`, **Destination Port Range** `80,443`.
   Click **Add Ingress Rules**.

(The server opens the same ports in its own firewall automatically.)

## Step 4: Wait, then open it (15–20 min)

The server is now downloading and building everything by itself. After 15–20 minutes, open your
address in the browser. It is your public IP with dashes instead of dots, followed by `.sslip.io`:

**IP `130.61.25.17` → `https://130-61-25-17.sslip.io`**

Sign in with any trial user name (table below) and the trial password you chose.
If the page does not load yet, wait 5 more minutes and refresh. If it still does not work after
30 minutes, see "If something goes wrong" at the end.

## Step 5 (optional): Connecting to the server yourself

Only needed for updates and troubleshooting. **Windows 10/11:** open **PowerShell**. **Mac:** open
**Terminal**. Then (replace the file name and IP with yours):

```bash
ssh -i Downloads/ssh-key-2026-10-21.key ubuntu@130.61.25.17
```
* Mac only, if it says the key's permissions are too open: run `chmod 600 Downloads/ssh-key-*.key` first.
* Answer `yes` to the "authenticity of host" question the first time.

## Step 6: Give the team access

| Login | Role | Good for testing |
|---|---|---|
| `clerk` | Accounting Clerk | Data entry: drafts invoices, bills and receipts; cannot post |
| `accountant` | Accountant | Posting invoices, bills, payments and journal entries; bank reconciliation; approvals up to EGP 100,000 |
| `senior` | Senior Accountant | Cancellations and reversals, fixed assets, stock adjustments, product/customer/vendor master data; approvals up to EGP 500,000 |
| `controller` | Financial Controller | Period close, FX revaluation, approvals up to EGP 2,500,000 (sets up an authenticator app at first login) |
| `cfo` | CFO / Finance Director | Everything, including period lock and year-end close; unlimited approvals (authenticator app at first login) |
| `auditor` | Auditor | Read-only: reports and audit log |
| `admin` | System Administrator | Users and roles (no accounting access, by design) |

All trial users share the trial password you chose. Send each tester the web address plus the
user name for the role they should try. To give someone
their own account, sign in as `admin` → **Users** → **+ New user**.

Each Controller/CFO login is tied to one phone's authenticator app. If a second person needs that
role, create a separate user for them, or have `admin` click **Reset MFA** on the account.

## Updating the trial after changes

When changes have been made (e.g. after Ibrahim's review), connect as in Step 5 and run:

```bash
cd /opt/ledgerline-erp && sudo git pull && sudo ./install.sh
```
It backs up, updates and restarts in a few minutes. Data entered by testers is kept.

**Start over with fresh demo data:** `cd /opt/ledgerline-erp && sudo docker compose down -v && sudo rm .env trial-users.txt && sudo ./install.sh --trial` (prints new random passwords).

## Good to know

* **Idle servers:** Oracle may reclaim Always Free servers that sit almost unused for 7 days. Regular
  testing keeps it alive. If it is reclaimed, repeat Steps 2–4.
* **Cost:** nothing, as long as you stay within Always Free (the shape above is). You can set a budget
  alert at **☰ → Billing → Budgets** for peace of mind.
* **Private repository:** if the GitHub repository is made private, the server needs a token to
  download it. Create one at GitHub → Settings → Developer settings → **Fine-grained tokens**
  (repository: ledgerline-erp, permission: Contents → Read-only), then clone with
  `git clone https://<token>@github.com/bahihassan89-ops/ledgerline-erp`.
* **Moving to production later:** install fresh on the chosen provider with `sudo ./install.sh`
  (no `--trial`), following [`OPERATIONS.md`](OPERATIONS.md). Nothing from the trial carries over.

## If something goes wrong

Connect as in Step 5 and look at the installation log:

```bash
sudo tail -30 /var/log/ledgerline-install.log
```
The last line is `LEDGERLINE READY: https://…` when it finished. Anything else, copy the last
30 lines and send them to whoever maintains the system. Common causes: the ports in Step 3 are not
open yet (the HTTPS certificate cannot be issued until they are; it retries by itself), or the
trial password did not meet the rules (fix it with
`cd /opt/ledgerline-erp && sudo docker compose exec backend python manage.py setup_trial --password 'New-Password-2026!'`).
