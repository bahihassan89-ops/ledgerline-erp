#!/bin/bash
# Encrypted backups of the database and attachments.
#   erp-backup loop   — back up every BACKUP_INTERVAL_HOURS (default 24) and prune old files
#   erp-backup once   — one backup now
# Files: /backups/erp-YYYYmmdd-HHMMSS.{sql.gz,media.tar.gz}.gpg  (AES-256, passphrase BACKUP_PASSPHRASE)
# Keeps BACKUP_KEEP_DAILY (default 14) newest sets plus the first set of each month for BACKUP_KEEP_MONTHS (12).
set -eu
set -o pipefail
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE must be set}"
export PGPASSWORD="${PGPASSWORD:?}"
DEST=/backups
mkdir -p "$DEST"

enc() { gpg --batch --yes --pinentry-mode loopback --passphrase-fd 3 --symmetric --cipher-algo AES256 -o "$1" 3<<P
$BACKUP_PASSPHRASE
P
}

backup() {
  ts=$(date -u +%Y%m%d-%H%M%S)
  tmp="$DEST/.erp-$ts"
  pg_dump -h db -U "$PGUSER" -d "$PGDATABASE" --no-owner --format=plain | gzip -9 | enc "$tmp.sql.gz.gpg"
  tar -C /media -czf - . | enc "$tmp.media.tar.gz.gpg"
  mv "$tmp.sql.gz.gpg" "$DEST/erp-$ts.sql.gz.gpg"
  mv "$tmp.media.tar.gz.gpg" "$DEST/erp-$ts.media.tar.gz.gpg"
  echo "$(date -u) backup erp-$ts done ($(du -ch "$DEST"/erp-$ts.* | tail -1 | cut -f1))"
}

prune() {
  keep_daily=${BACKUP_KEEP_DAILY:-14}; keep_months=${BACKUP_KEEP_MONTHS:-12}
  sets=$(ls "$DEST"/erp-*.sql.gz.gpg 2>/dev/null | sed 's#.*/erp-##; s#\.sql\.gz\.gpg##' | sort -r)
  keep=$(echo "$sets" | head -n "$keep_daily")
  monthly=$(echo "$sets" | sort | awk '{m=substr($0,1,6); if (!(m in s)) {s[m]=1; print}}' | sort -r | head -n "$keep_months")
  for s in $sets; do
    echo "$keep $monthly" | tr ' ' '\n' | grep -qx "$s" || { rm -f "$DEST/erp-$s".*; echo "pruned erp-$s"; }
  done
}

case "${1:-loop}" in
  once) backup ;;
  loop)
    hours=${BACKUP_INTERVAL_HOURS:-24}
    while true; do backup && prune || echo "$(date -u) BACKUP FAILED" >&2; sleep $((hours * 3600)); done ;;
  *) echo "usage: erp-backup [once|loop]" >&2; exit 2 ;;
esac
