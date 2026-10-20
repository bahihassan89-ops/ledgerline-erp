#!/bin/sh
# Least-privilege application role: it can read/write business tables but cannot alter the schema,
# drop the integrity triggers, or modify/delete audit records.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE erp_app LOGIN PASSWORD '${ERP_APP_PASSWORD}';
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO erp_app;
GRANT USAGE ON SCHEMA public TO erp_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO erp_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO erp_app;
SQL
