#!/bin/bash
# Encrypted backups of the database and attachments.
#   erp-backup loop   — back up every BACKUP_INTERVAL_HOURS (default 24), prune old files, and
#                        react within ~5s to an on-demand "back up now" click (see BackupView in
#                        apps/core/views.py) dropped as a request-*.json file in the shared
#                        /control volume.
#   erp-backup once   — one backup now, synchronously (manual/cron use outside the loop)
# Files: /backups/erp-YYYYmmdd-HHMMSS.{sql.gz,media.tar.gz}.gpg  (AES-256, passphrase BACKUP_PASSPHRASE)
# Keeps BACKUP_KEEP_DAILY (default 14) newest sets plus the first set of each month for
# BACKUP_KEEP_MONTHS (default 60, i.e. 5 years -- the minimum period for retaining accounting
# books, records and e-invoices under Egypt's Unified Tax Procedures Law 206/2020 and the
# Commercial Law 17/1999, so a disaster that takes out the live database never leaves the
# business short of the legally required history; see docs/ACCOUNTING_DESIGN.md section 19).
# Status for the button is written to /control/status.json after every run (manual or scheduled).
# No cloud destination is wired up yet -- when one is, push it from the end of backup() below;
# everything above it (encryption, naming, pruning, status) stays the same.
set -eu
set -o pipefail
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE must be set}"
export PGPASSWORD="${PGPASSWORD:?}"
DEST=/backups
CTL=/control
mkdir -p "$DEST" "$CTL"
# $CTL is a shared volume with the non-root backend container (uid 10001), which must be able to
# drop request-*.json files and read status.json; it holds no secrets (just small handshake JSON),
# unlike $DEST (encrypted dumps), so world-writable with the sticky bit (like /tmp) is fine here.
chmod 1777 "$CTL"

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
  LAST_TS="$ts"
  LAST_SIZE_KB=$(du -ck "$DEST/erp-$ts".* | tail -1 | cut -f1)
  echo "$(date -u) backup erp-$ts done (${LAST_SIZE_KB}K)"
}

prune() {
  keep_daily=${BACKUP_KEEP_DAILY:-14}; keep_months=${BACKUP_KEEP_MONTHS:-60}
  sets=$(ls "$DEST"/erp-*.sql.gz.gpg 2>/dev/null | sed 's#.*/erp-##; s#\.sql\.gz\.gpg##' | sort -r)
  keep=$(echo "$sets" | head -n "$keep_daily")
  monthly=$(echo "$sets" | sort | awk '{m=substr($0,1,6); if (!(m in s)) {s[m]=1; print}}' | sort -r | head -n "$keep_months")
  for s in $sets; do
    echo "$keep $monthly" | tr ' ' '\n' | grep -qx "$s" || { rm -f "$DEST/erp-$s".*; echo "pruned erp-$s"; }
  done
}

write_status() {
  # $1=ok|error  $2=manual|scheduled|cli  $3=requested_by (optional)
  who=null; [ -n "${3:-}" ] && who="\"$3\""
  tmp="$CTL/.status.json.tmp"
  printf '{"last_backup_at":"%s","last_status":"%s","last_trigger":"%s","requested_by":%s,"last_size_kb":%s,"last_filename":"erp-%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$who" "${LAST_SIZE_KB:-0}" "${LAST_TS:-}" > "$tmp"
  mv "$tmp" "$CTL/status.json"
}

run_backup() {
  # $1=manual|scheduled|cli  $2=requested_by (optional)
  if backup; then prune || true; write_status ok "$1" "${2:-}"
  else write_status error "$1" "${2:-}"; echo "$(date -u) BACKUP FAILED" >&2; fi
}

case "${1:-loop}" in
  once) run_backup cli ;;
  loop)
    hours=${BACKUP_INTERVAL_HOURS:-24}
    interval=$((hours * 3600))
    next=$(( $(date +%s) + interval ))
    while true; do
      req=$(ls "$CTL"/request-*.json 2>/dev/null | head -n1 || true)
      if [ -n "$req" ]; then
        who=$(sed -n 's/.*"requested_by" *: *"\([^"]*\)".*/\1/p' "$req")
        run_backup manual "$who"
        rm -f "$req"
        next=$(( $(date +%s) + interval ))
      elif [ "$(date +%s)" -ge "$next" ]; then
        run_backup scheduled
        next=$(( $(date +%s) + interval ))
      fi
      sleep 5
    done ;;
  *) echo "usage: erp-backup [once|loop]" >&2; exit 2 ;;
esac
