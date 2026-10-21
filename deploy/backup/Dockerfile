# Debian-based postgres image: ships pg_dump and GnuPG, so nothing is downloaded at build time.
FROM postgres:16
COPY backup.sh /usr/local/bin/erp-backup
RUN chmod 755 /usr/local/bin/erp-backup
ENTRYPOINT ["/usr/local/bin/erp-backup"]
