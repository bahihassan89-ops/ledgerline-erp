"""Database-level accounting integrity. These rules hold even for direct SQL access."""
from django.db import migrations

SQL = r"""
-- 1. Posted / cancelled journal entries are immutable and cannot be deleted.
CREATE OR REPLACE FUNCTION erp_entry_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.state IN ('posted', 'cancelled') THEN
      RAISE EXCEPTION 'ERP-INTEGRITY: journal entry % is % and cannot be deleted', OLD.number, OLD.state;
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.state IN ('posted', 'cancelled') AND (to_jsonb(NEW) - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'updated_at') THEN
    RAISE EXCEPTION 'ERP-INTEGRITY: journal entry % is % and immutable; reverse it instead', OLD.number, OLD.state;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER erp_entry_guard BEFORE UPDATE OR DELETE ON ledger_journalentry
  FOR EACH ROW EXECUTE FUNCTION erp_entry_guard();

-- 2. Lines of a posted entry cannot be inserted, changed or deleted.
CREATE OR REPLACE FUNCTION erp_line_guard() RETURNS trigger AS $$
DECLARE st text;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    SELECT state INTO st FROM ledger_journalentry WHERE id = OLD.entry_id;
    IF st IN ('posted', 'cancelled') THEN
      RAISE EXCEPTION 'ERP-INTEGRITY: lines of % entry % are immutable', st, OLD.entry_id;
    END IF;
  END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    SELECT state INTO st FROM ledger_journalentry WHERE id = NEW.entry_id;
    IF st IN ('posted', 'cancelled') THEN
      RAISE EXCEPTION 'ERP-INTEGRITY: cannot add lines to % entry %', st, NEW.entry_id;
    END IF;
    RETURN NEW;
  END IF;
  RETURN OLD;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER erp_line_guard BEFORE INSERT OR UPDATE OR DELETE ON ledger_journalline
  FOR EACH ROW EXECUTE FUNCTION erp_line_guard();

-- 3. At COMMIT, every posted entry must balance, be non-empty and have consistent lines.
CREATE OR REPLACE FUNCTION erp_entry_balanced() RETURNS trigger AS $$
DECLARE d numeric; c numeric; n int; bad int;
BEGIN
  IF NEW.state <> 'posted' THEN RETURN NULL; END IF;
  SELECT COALESCE(SUM(debit),0), COALESCE(SUM(credit),0), COUNT(*) INTO d, c, n
    FROM ledger_journalline WHERE entry_id = NEW.id;
  IF n < 2 THEN
    RAISE EXCEPTION 'ERP-INTEGRITY: posted entry % has fewer than two lines', NEW.number;
  END IF;
  IF d <> c OR d = 0 THEN
    RAISE EXCEPTION 'ERP-INTEGRITY: posted entry % is not balanced (debit %, credit %)', NEW.number, d, c;
  END IF;
  IF d <> NEW.total_debit OR c <> NEW.total_credit THEN
    RAISE EXCEPTION 'ERP-INTEGRITY: entry % totals do not match its lines', NEW.number;
  END IF;
  SELECT COUNT(*) INTO bad FROM ledger_journalline l JOIN ledger_account a ON a.id = l.account_id
   WHERE l.entry_id = NEW.id AND (a.account_type = 'group' OR a.company_id <> NEW.company_id
         OR l.company_id <> NEW.company_id OR NOT l.posted OR l.date IS DISTINCT FROM NEW.date);
  IF bad > 0 THEN
    RAISE EXCEPTION 'ERP-INTEGRITY: entry % has lines on group/foreign accounts or unsynced lines', NEW.number;
  END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER erp_entry_balanced AFTER INSERT OR UPDATE ON ledger_journalentry
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION erp_entry_balanced();

-- 4. The audit log is append-only.
CREATE OR REPLACE FUNCTION erp_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'ERP-INTEGRITY: % is append-only', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER erp_audit_append_only BEFORE UPDATE OR DELETE ON core_auditlog
  FOR EACH ROW EXECUTE FUNCTION erp_append_only();
CREATE TRIGGER erp_audit_no_truncate BEFORE TRUNCATE ON core_auditlog
  FOR EACH STATEMENT EXECUTE FUNCTION erp_append_only();
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS erp_audit_no_truncate ON core_auditlog;
DROP TRIGGER IF EXISTS erp_audit_append_only ON core_auditlog;
DROP TRIGGER IF EXISTS erp_entry_balanced ON ledger_journalentry;
DROP TRIGGER IF EXISTS erp_line_guard ON ledger_journalline;
DROP TRIGGER IF EXISTS erp_entry_guard ON ledger_journalentry;
DROP FUNCTION IF EXISTS erp_append_only, erp_entry_balanced, erp_line_guard, erp_entry_guard;
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0002_initial"), ("core", "0002_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
