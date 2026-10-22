"""Extend the erp_entry_balanced() integrity trigger (see 0003_integrity_triggers) to also
reject posted entries whose denormalized JournalLine.account_type has drifted from its
account's current account_type -- the same way it already checks JournalLine.date against
the entry's date. This closes the gap opened by 0005, which added that denormalized column.
"""
from django.db import migrations

SQL = r"""
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
         OR l.company_id <> NEW.company_id OR NOT l.posted OR l.date IS DISTINCT FROM NEW.date
         OR l.account_type IS DISTINCT FROM a.account_type);
  IF bad > 0 THEN
    RAISE EXCEPTION 'ERP-INTEGRITY: entry % has lines on group/foreign accounts or unsynced lines', NEW.number;
  END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql;
"""

REVERSE = r"""
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
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0005_denormalize_journalline_account_type")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
