"""Enforce the polymorphic document_type/document_id reference at the DB level.
ApprovalRequest can point at six different tables, so a normal FK isn't possible — this
trigger does the equivalent check in each case, including the tenant (company_id) match."""
from django.db import migrations

SQL = r"""
CREATE OR REPLACE FUNCTION erp_approval_doc_exists() RETURNS trigger AS $$
DECLARE found boolean;
BEGIN
  CASE NEW.document_type
    WHEN 'sales_order' THEN
      SELECT EXISTS(SELECT 1 FROM sales_salesorder WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
    WHEN 'purchase_order' THEN
      SELECT EXISTS(SELECT 1 FROM purchasing_purchaseorder WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
    WHEN 'customer_invoice', 'vendor_bill' THEN
      SELECT EXISTS(SELECT 1 FROM billing_invoice WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
    WHEN 'payment' THEN
      SELECT EXISTS(SELECT 1 FROM billing_payment WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
    WHEN 'journal_entry' THEN
      SELECT EXISTS(SELECT 1 FROM ledger_journalentry WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
    ELSE
      RAISE EXCEPTION 'ERP-INTEGRITY: unknown document_type %', NEW.document_type;
  END CASE;
  IF NOT found THEN
    RAISE EXCEPTION 'ERP-INTEGRITY: approval request references missing % % (company %)',
      NEW.document_type, NEW.document_id, NEW.company_id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER erp_approval_doc_exists BEFORE INSERT OR UPDATE ON approvals_approvalrequest
  FOR EACH ROW EXECUTE FUNCTION erp_approval_doc_exists();
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS erp_approval_doc_exists ON approvals_approvalrequest;
DROP FUNCTION IF EXISTS erp_approval_doc_exists;
"""


class Migration(migrations.Migration):
    dependencies = [("approvals", "0002_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
