#!/usr/bin/env bash
# Ledgerline ERP: one-command installer for a fresh Ubuntu/Debian server.
#
#   git clone https://github.com/bahihassan89-ops/ledgerline-erp && cd ledgerline-erp && sudo ./install.sh
#   sudo ./install.sh --trial    # trial server: Egyptian demo company, one user per role, "TRIAL" banner
#
# It installs Docker if needed, writes .env with random secrets (never overwrites an existing one),
# builds and starts the stack behind automatic HTTPS, creates the first company and administrator,
# and prints the one-time admin password. Re-running it is safe: it upgrades to the checked-out code.
#
# Non-interactive: set ERP_DOMAIN, ERP_TLS, COMPANY_NAME, COMPANY_NAME_AR, COMPANY_CODE, ADMIN_EMAIL.
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf '\n\033[1;34m▸ %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
ask()  { local var=$1 prompt=$2 def=${3:-}; if [ -z "${!var:-}" ]; then
           read -r -p "$prompt${def:+ [$def]}: " "$var" </dev/tty; printf -v "$var" '%s' "${!var:-$def}"; fi
         [ -n "${!var}" ] || die "$var is required"; }
secret() { head -c 64 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 64; }

TRIAL=0
for arg in "$@"; do case "$arg" in --trial) TRIAL=1 ;; *) die "Unknown option: $arg" ;; esac; done

[ "$(id -u)" -eq 0 ] || die "Run as root: sudo ./install.sh"

# Oracle Cloud's Ubuntu images block every inbound port except SSH in the host firewall (in addition
# to the VCN security list). Open 80/443 there and make it permanent.
if iptables -S INPUT 2>/dev/null | grep -q -- '-j REJECT --reject-with icmp-host-prohibited'; then
  pos=$(iptables -L INPUT --line-numbers -n | awk '/REJECT/ && /icmp-host-prohibited/ {print $1; exit}')
  for rule in "-p tcp --dport 80" "-p tcp --dport 443" "-p udp --dport 443"; do
    # shellcheck disable=SC2086
    iptables -C INPUT $rule -j ACCEPT 2>/dev/null || iptables -I INPUT "$pos" $rule -j ACCEPT
  done
  command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null 2>&1 || true
fi

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  say "Installing Docker"
  command -v curl >/dev/null || { apt-get update -qq && apt-get install -y -qq curl ca-certificates; }
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

if [ ! -f .env ]; then
  say "Configuration"
  echo "The web address must already point (DNS A record) to this server's public IP for HTTPS to work."
  echo "No domain yet? Accept the suggested <ip>.sslip.io address: it points to this server automatically."
  echo "For an office server without a public address, use its LAN name and choose 'internal' for HTTPS."
  PUBLIC_IP=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)
  ask ERP_DOMAIN   "Web address for the ERP (e.g. erp.yourcompany.com)" "${PUBLIC_IP:+${PUBLIC_IP//./-}.sslip.io}"
  ask ERP_TLS      "Email for HTTPS certificate notices, or 'internal' for a private network"
  ask BACKUP_PASSPHRASE_CHOICE "Backup passphrase (leave empty to generate one)" "generate"
  [ "$BACKUP_PASSPHRASE_CHOICE" = generate ] && BACKUP_PASSPHRASE=$(secret) || BACKUP_PASSPHRASE=$BACKUP_PASSPHRASE_CHOICE
  umask 077
  cat > .env <<ENV
# Ledgerline ERP configuration: created $(date -u +%F). Keep this file private (chmod 600) and backed up
# OFF the server: without ERP_FIELD_ENCRYPTION_KEY and BACKUP_PASSPHRASE the backups cannot be restored.
ERP_DOMAIN=$ERP_DOMAIN
ERP_TLS=$ERP_TLS
ERP_SECRET_KEY=$(secret)
ERP_FIELD_ENCRYPTION_KEY=$(secret)
DB_OWNER_PASSWORD=$(secret)
ERP_DB_PASSWORD=$(secret)
BACKUP_PASSPHRASE=$BACKUP_PASSPHRASE
BACKUP_INTERVAL_HOURS=24
BACKUP_KEEP_DAILY=14
BACKUP_KEEP_MONTHS=12
ERP_TRIAL=$TRIAL
ENV
  chmod 600 .env
  NEW_INSTALL=1
else
  say "Existing installation found: upgrading"
  NEW_INSTALL=0
  if [ -n "$(docker compose ps -q backup 2>/dev/null)" ]; then
    say "Backing up before the upgrade"
    docker compose exec -T backup erp-backup once || die "Pre-upgrade backup failed; nothing was changed."
  fi
fi
mkdir -p backups && chmod 700 backups

say "Building and starting (first run takes a few minutes)"
docker compose up -d --build --remove-orphans

say "Waiting for the application to become healthy"
for _ in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q backend)" 2>/dev/null)" = healthy ] && break
  sleep 5
done
[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q backend)")" = healthy ] \
  || die "Backend did not start. See: docker compose logs backend migrate"

if [ "$NEW_INSTALL" = 1 ] && [ "$TRIAL" = 1 ]; then
  say "Loading the trial company and users (about a minute)"
  docker compose exec -T backend python manage.py setup_trial | tee trial-users.txt
  chmod 600 trial-users.txt
elif [ "$NEW_INSTALL" = 1 ]; then
  say "First company and administrator"
  ask COMPANY_NAME    "Company name (English)"
  ask COMPANY_NAME_AR "Company name (Arabic, optional)" "-"
  [ "$COMPANY_NAME_AR" = "-" ] && COMPANY_NAME_AR=""
  ask COMPANY_CODE    "Short company code (letters/digits, e.g. NILE)"
  ask ADMIN_EMAIL     "Administrator email"
  docker compose exec -T backend python manage.py bootstrap --country EG \
    --company-name "$COMPANY_NAME" --company-name-ar "$COMPANY_NAME_AR" \
    --company-code "$COMPANY_CODE" --admin-email "$ADMIN_EMAIL"
fi

if [ "$NEW_INSTALL" = 1 ]; then
  say "Taking the first backup"
  docker compose exec -T backup erp-backup once
fi

. ./.env
printf '\n\033[1;32m✓ Ledgerline ERP is running at https://%s\033[0m\n' "$ERP_DOMAIN"
[ "$NEW_INSTALL" = 1 ] || exit 0
if [ "$TRIAL" = 1 ]; then
cat <<MSG

  Trial server ready. The user names and passwords above are also saved in trial-users.txt
  (readable only by root). Give each tester the login for the role you want them to try.
  Everything is demo data: do not enter real customer or company data on this server.
  To install updates later:  git pull && sudo ./install.sh
MSG
exit 0
fi
cat <<MSG

  Next steps
  1. Sign in with the admin login above. You will scan a QR code with an authenticator app
     (Google/Microsoft Authenticator) and then choose your own password.
  2. Copy .env to a safe place OFF this server (password manager / sealed envelope).
  3. Arrange off-site copies of the ./backups folder (see docs/OPERATIONS.md §3).
  4. Create users for the team under Administration → Users and assign each person a role.
MSG
