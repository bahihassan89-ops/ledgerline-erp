# Item #32: customer_return/vendor_return are new document_type values for credit/debit notes
# (see DocumentType in models.py). The erp_approval_doc_exists() trigger (0003_doc_exists_trigger)
# whitelists valid document_type values at the DB level and would reject any ApprovalRequest for
# these two with "ERP-INTEGRITY: unknown document_type" -- both point at the same billing_invoice
# table as customer_invoice/vendor_bill, so they're added to that same WHEN branch.
#
# NOTE: inventory_count was never added to this trigger when it was introduced (0004) -- that gap
# is pre-existing and unrelated to this change; it belongs with item #33 (stock count adjustment
# approvals), not here.
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
    WHEN 'customer_invoice', 'vendor_bill', 'customer_return', 'vendor_return' THEN
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
"""

REVERSE = r"""
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
"""


class Migration(migrations.Migration):
    dependencies = [("approvals", "0006_alter_approvalrequest_document_type_and_more")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
