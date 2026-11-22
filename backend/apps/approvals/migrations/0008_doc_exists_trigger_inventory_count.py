# Item #33: inventory_count is a DocumentType value that has existed since 0004 (the default
# ApprovalRule "Large inventory count variance -> Controller" was seeded for it from day one), but
# the erp_approval_doc_exists() trigger (0003, last touched for returns in 0007) never got a branch
# for it. Any InventoryCount variance crossing that rule's threshold hits ensure_approved(), which
# inserts an ApprovalRequest with document_type='inventory_count' -- and the trigger rejects that
# insert with "ERP-INTEGRITY: unknown document_type inventory_count", crashing apply_count()'s
# transaction instead of putting the count into 'to_approve'. This was flagged as a pre-existing gap
# while fixing the equivalent returns gap in 0007 and is fixed here, alongside item #33's root-cause
# requirement for the same class of adjustment.
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
    dependencies = [("approvals", "0007_doc_exists_trigger_returns")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
