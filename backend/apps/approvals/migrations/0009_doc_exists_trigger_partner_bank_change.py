# Item #34: partner_bank_change is a brand-new DocumentType (maker-checker for vendor/customer
# bank-detail changes). Items #32 and #33 both discovered that erp_approval_doc_exists() silently
# falls out of sync with DocumentType -- a new document type was added to the Python enum without a
# matching CASE/WHEN branch in this trigger, and the first ApprovalRequest insert for it crashed
# with "ERP-INTEGRITY: unknown document_type ...". Adding the branch in the SAME migration that
# introduces the type this time, instead of leaving it as a gap to be discovered later.
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
    WHEN 'inventory_count' THEN
      SELECT EXISTS(SELECT 1 FROM inventory_inventorycount WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
    WHEN 'partner_bank_change' THEN
      SELECT EXISTS(SELECT 1 FROM partners_partnerbankchangerequest WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
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
    WHEN 'customer_invoice', 'vendor_bill', 'customer_return', 'vendor_return' THEN
      SELECT EXISTS(SELECT 1 FROM billing_invoice WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
    WHEN 'payment' THEN
      SELECT EXISTS(SELECT 1 FROM billing_payment WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
    WHEN 'journal_entry' THEN
      SELECT EXISTS(SELECT 1 FROM ledger_journalentry WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
    WHEN 'inventory_count' THEN
      SELECT EXISTS(SELECT 1 FROM inventory_inventorycount WHERE id = NEW.document_id AND company_id = NEW.company_id) INTO found;
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
    # No dependency on "partners" needed -- like 0007/0008 before it, this only replaces the
    # erp_approval_doc_exists() function body; the CASE branch only matters once a row actually
    # tries to use document_type='partner_bank_change', by which point partners' own migrations
    # (run independently) will already have created the table.
    dependencies = [("approvals", "0008_doc_exists_trigger_inventory_count")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
