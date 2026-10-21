# Trial server on Oracle Cloud (free)

A step-by-step guide to putting a **trial** copy of Ledgerline ERP on the internet at no cost, so the
team can test it from anywhere before you choose a paid provider. About 30–45 minutes, most of it
waiting. No technical background needed: every command is copy-paste.

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

## Step 2: Create the server (10 min)

1. In the Oracle console: **☰ menu → Compute → Instances → Create instance**.
2. **Name:** `ledgerline-trial`.
3. **Image and shape** → **Edit**:
   * **Change image** → **Canonical Ubuntu** → version **24.04** → Select.
   * **Change shape** → **Ampere** → **VM.Standard.A1.Flex** → **2 OCPUs, 12 GB memory** → Select.
     (The label "Always Free-eligible" must be visible.)
4. **Networking:** keep "Create new virtual cloud network" and "Create new public subnet", and make
   sure **Assign a public IPv4 address** is on.
5. **Add SSH keys:** choose **Generate a key pair for me** and click **Save private key**.
   Keep this file safe: it is the key to your server.
6. Click **Create**. After a minute or two the status turns green **RUNNING**. Copy the
   **Public IP address** shown on the page (e.g. `130.61.25.17`).

> **"Out of capacity" error?** Free servers are popular. Try again with **1 OCPU / 6 GB**, choose a
> different **Availability domain** in the Placement section, or retry a few hours later.

## Step 3: Open the web ports (3 min)

1. On the instance page, under **Primary VNIC**, click the **Subnet** link.
2. Open **Security Lists** (sometimes under the **Security** tab) → **Default Security List** →
   **Add Ingress Rules**.
3. Fill in: **Source CIDR** `0.0.0.0/0`, **IP Protocol** `TCP`, **Destination Port Range** `80,443`.
   Click **Add Ingress Rules**.

(The installer opens the same ports in the server's own firewall automatically.)

## Step 4: Connect to the server (2 min)

**Windows 10/11:** open **PowerShell**. **Mac:** open **Terminal**. Then (replace the file
name and IP with yours):

```bash
ssh -i Downloads/ssh-key-2026-10-21.key ubuntu@130.61.25.17
```
* Mac only, if it says the key's permissions are too open: run `chmod 600 Downloads/ssh-key-*.key` first.
* Answer `yes` to the "authenticity of host" question the first time.

You are in when the prompt shows `ubuntu@ledgerline-trial`.

## Step 5: Install the trial (15 min, mostly waiting)

Paste this and press Enter:

```bash
git clone https://github.com/bahihassan89-ops/ledgerline-erp && cd ledgerline-erp && sudo ./install.sh --trial
```

It asks three questions:

| Question | What to answer |
|---|---|
| Web address | Press **Enter** to accept the suggested address ending in **.sslip.io**. It points to your server automatically, so no domain is needed for the trial. |
| Email for HTTPS certificate notices | Your email. |
| Backup passphrase | Press **Enter** (one is generated). |

The first install takes 10–15 minutes. At the end it prints a table of **user names and passwords**
(one per role) and the web address. Copy the table somewhere safe (it is also saved on the server:
`sudo cat ~/ledgerline-erp/trial-users.txt`).

**Open the web address in your browser** (e.g. `https://130-61-25-17.sslip.io`) and sign in.

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

Send each tester the web address plus the login for the role they should try. To give someone
their own account, sign in as `admin` → **Users** → **+ New user**.

Each Controller/CFO login is tied to one phone's authenticator app. If a second person needs that
role, create a separate user for them, or have `admin` click **Reset MFA** on the account.

## Updating the trial after changes

When changes have been made (e.g. after Ibrahim's review), connect as in Step 4 and run:

```bash
cd ledgerline-erp && git pull && sudo ./install.sh
```
It backs up, updates and restarts in a few minutes. Data entered by testers is kept.

**Start over with fresh demo data:** `cd ledgerline-erp && sudo docker compose down -v && sudo rm .env trial-users.txt && sudo ./install.sh --trial`

## Good to know

* **Idle servers:** Oracle may reclaim Always Free servers that sit almost unused for 7 days. Regular
  testing keeps it alive. If it is reclaimed, repeat Steps 2–5 (15 minutes).
* **Cost:** nothing, as long as you stay within Always Free (the shape above is). You can set a budget
  alert at **☰ → Billing → Budgets** for peace of mind.
* **Private repository:** if the GitHub repository is made private, the server needs a token to
  download it. Create one at GitHub → Settings → Developer settings → **Fine-grained tokens**
  (repository: ledgerline-erp, permission: Contents → Read-only), then clone with
  `git clone https://<token>@github.com/bahihassan89-ops/ledgerline-erp`.
* **Moving to production later:** install fresh on the chosen provider with `sudo ./install.sh`
  (no `--trial`), following [`OPERATIONS.md`](OPERATIONS.md). Nothing from the trial carries over.
