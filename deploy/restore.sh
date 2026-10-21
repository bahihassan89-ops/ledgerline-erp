#!/bin/sh
# Restore a backup set made by the backup service.
#   ./deploy/restore.sh                      list available backups
#   ./deploy/restore.sh erp-20261021-020000  restore that set (REPLACES the current database and attachments)
# Run from the installation folder (the one with docker-compose.yml and .env).
set -eu
cd "$(dirname "$0")/.."
DC="docker compose"
if [ $# -eq 0 ]; then
  echo "Available backups:"; ls -1 backups/ 2>/dev/null | sed -n 's/\.sql\.gz\.gpg$//p' | sort -r
  exit 0
fi
set="$1"
[ -f "backups/$set.sql.gz.gpg" ] || { echo "No such backup: backups/$set.sql.gz.gpg" >&2; exit 1; }
printf "This REPLACES all current data with backup %s. Type RESTORE to continue: " "$set"
read -r answer; [ "$answer" = "RESTORE" ] || { echo "Cancelled."; exit 1; }

echo "Taking a safety backup of the current state first…"
$DC run --rm backup once

$DC stop backend webhooks eta scheduler frontend
DEC='gpg --batch --quiet --pinentry-mode loopback --passphrase-fd 3 -d'
$DC run --rm -T --entrypoint bash backup -c "
  set -eu; set -o pipefail
  psql -h db -U \"\$PGUSER\" -d postgres -v ON_ERROR_STOP=1 \
       -c 'DROP DATABASE IF EXISTS erp WITH (FORCE)' -c 'CREATE DATABASE erp' -c 'GRANT CONNECT ON DATABASE erp TO erp_app'
  $DEC /backups/$set.sql.gz.gpg 3<<P | gunzip | psql -h db -U \"\$PGUSER\" -d erp -v ON_ERROR_STOP=1 -q
\$BACKUP_PASSPHRASE
P
  rm -rf /media/* /media/.[!.]* 2>/dev/null || true
  $DEC /backups/$set.media.tar.gz.gpg 3<<P | tar -C /media -xzf -
\$BACKUP_PASSPHRASE
P
"
$DC run --rm migrate
$DC up -d
echo "Restored $set. Check the trial balance and the audit-log verification (Administration → Audit log → Verify integrity)."
