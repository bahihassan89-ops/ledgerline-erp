#!/bin/bash
# Ledgerline ERP: self-installing TRIAL server.
# Paste this whole script into Oracle Cloud's "Cloud-init script" box when creating the instance
# (Create instance → Show advanced options → Management → Paste cloud-init script).
# The server installs itself in ~15 minutes; then open https://<public-ip-with-dashes>.sslip.io
# e.g. public IP 130.61.25.17  →  https://130-61-25-17.sslip.io
#
# Fill in the two values below. The password is used by every trial user (admin, clerk, accountant,
# senior, controller, cfo, auditor): 12+ characters with upper and lower case, a digit and a symbol.
CERT_EMAIL='you@example.com'
TRIAL_PASSWORD='Choose-A-Strong-Password-1!'

set -euxo pipefail
exec > /var/log/ledgerline-install.log 2>&1
export DEBIAN_FRONTEND=noninteractive HOME=/root
apt-get update -q && apt-get install -y -q git curl ca-certificates
git clone https://github.com/bahihassan89-ops/ledgerline-erp /opt/ledgerline-erp
cd /opt/ledgerline-erp
IP=$(curl -fsS --retry 5 https://api.ipify.org)
ERP_DOMAIN="${IP//./-}.sslip.io" ERP_TLS="$CERT_EMAIL" BACKUP_PASSPHRASE_CHOICE=generate \
  ERP_TRIAL_PASSWORD="$TRIAL_PASSWORD" ./install.sh --trial
echo "LEDGERLINE READY: https://${IP//./-}.sslip.io"
