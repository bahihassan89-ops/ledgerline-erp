"""Egyptian tax rules: VAT 14%, withholding (discount & addition), Form 41, VAT return, ETA e-invoicing."""
import json

import pytest

from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceLine
from apps.core.exceptions import BusinessError
from apps.einvoice import services as eta
from apps.einvoice.models import EtaDocument

from .conftest import D, tb


def bill(env, amount, wht=True, ref="B-1"):
    b = Invoice.objects.create(company=env.c, move_type="in_invoice", partner=env.vendor, currency_id="EGP",
                               date=env.d(4, 10), reference=ref)
    InvoiceLine.objects.create(invoice=b, description="Maintenance service", account=env.A["520203"], quantity=1,
                               unit_price=amount, tax=env.T["VAT14-P"],
                               withholding_tax=env.T["WHT3-SRV-P"] if wht else None)
    return billing.post_invoice(b, env.accountant)


def invoice(env, product, qty, price, wht=None, partner=None, move_type="out_invoice", origin=None):
    inv = Invoice.objects.create(company=env.c, move_type=move_type, partner=partner or env.customer,
                                 currency_id="EGP", date=env.d(4, 12), origin_invoice=origin)
    InvoiceLine.objects.create(invoice=inv, product=product, description=product.name,
                               account=env.A["410101" if product.type == "stock" else "410102"], quantity=qty,
                               unit_price=price, tax=env.T["VAT14-S"], withholding_tax=wht)
    return inv


def test_vendor_bill_with_vat_and_withholding(eg):
    b = bill(eg, 10000)
    # 10,000 + 14% VAT − 3% WHT on the net = 11,100 payable to the vendor
    assert b.amount_tax == D(1400) and b.amount_wht == D(300) and b.amount_total == D(11100)
    t = tb(eg)
    assert t["520203"] == D(10000) and t["110403"] == D(1400)
    assert t["210101"] == D(-11100) and t["210304"] == D(-300)


def test_withholding_threshold(eg):
    b = bill(eg, 250, ref="B-small")  # below EGP 300 → no withholding
    assert b.amount_wht == 0 and b.amount_total == D(285)


def test_customer_withholding_and_form41(eg):
    inv = billing.post_invoice(invoice(eg, eg.service, 2, 5000, wht=eg.T["WHT3-SRV-S"]), eg.accountant)
    assert inv.amount_total == D(10000 + 1400 - 300)
    assert tb(eg)["110206"] == D(300)  # taxes withheld by customer, an asset
    bill(eg, 10000)
    f41 = billing.withholding_report(eg.c, eg.d(4, 1), eg.d(6, 30), "purchase")
    assert D(f41["total_withheld"]) == D(300) and f41["rows"][0]["tax_number"] == "555666777"
    assert D(f41["gl_movement"]) == D(300)
    rec = billing.withholding_report(eg.c, eg.d(4, 1), eg.d(6, 30), "sale")
    assert D(rec["total_withheld"]) == D(300) and D(rec["gl_movement"]) == D(300)


def test_vat_return(eg):
    billing.post_invoice(invoice(eg, eg.service, 1, 10000), eg.accountant)
    bill(eg, 5000, wht=False)
    vr = billing.vat_return(eg.c, eg.d(4, 1), eg.d(4, 30))
    assert D(vr["output_vat"]) == D(1400) and D(vr["input_vat"]) == D(700) and D(vr["net_vat"]) == D(700)


def test_wrong_tax_scope_rejected(eg):
    inv = invoice(eg, eg.service, 1, 100)
    ln = inv.lines.first()
    ln.tax = eg.T["VAT14-P"]
    ln.save()
    with pytest.raises(BusinessError, match="not a sale VAT"):
        billing.post_invoice(inv, eg.accountant)


# ------------------------------------------------------------------ ETA

class FakeEta:
    """Records calls and answers like the ETA API."""
    def __init__(self, status="Valid"):
        self.calls, self.status = [], status

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, json.loads(body) if body and body[:1] == b"{" else body))
        if url.endswith("/connect/token"):
            return 200, {"access_token": "tok", "expires_in": 3600}
        if url.endswith("/documentsubmissions"):
            docs = json.loads(body)["documents"]
            return 202, {"submissionId": "SUB1", "acceptedDocuments": [
                {"uuid": f"UUID-{d['internalID']}", "longId": "LONG", "internalId": d["internalID"], "hashKey": "H"}
                for d in docs], "rejectedDocuments": []}
        if "/details" in url:
            return 200, {"status": self.status, "validationResults": {"validationSteps": [
                {"name": "Core", "status": "Invalid", "error": {"error": "bad code"}}]}}
        if "/state/" in url:
            return 200, {}
        return 404, {}


def test_eta_validation_blocks_incomplete_documents(eg):
    eg.eta.enabled = True
    eg.eta.save()
    p = eg.customer
    p.tax_number, p.street = "", ""
    p.save()
    eg.item.eta_item_code = ""
    eg.item.save()
    with pytest.raises(BusinessError) as ex:
        billing.post_invoice(invoice(eg, eg.item, 1, 100), eg.accountant)
    errs = " ".join(ex.value.details["errors"])
    assert "9-digit tax registration" in errs and "street" in errs and "no ETA item code" in errs


def test_eta_document_build_submit_and_status(eg):
    eg.eta.enabled = True
    eg.eta.save()
    inv = billing.post_invoice(invoice(eg, eg.service, 2, 5000, wht=eg.T["WHT3-SRV-S"]), eg.accountant)
    doc = EtaDocument.objects.get(invoice=inv)
    assert doc.state == "pending" and doc.document_type == "I"
    d = eta.build_document(inv, eg.eta)
    assert d["documentTypeVersion"] == "0.9"  # unsigned pre-production mode
    assert d["issuer"]["id"] == "123456789" and d["receiver"]["id"] == "987654321"
    ln = d["invoiceLines"][0]
    assert ln["salesTotal"] == 10000 and ln["netTotal"] == 10000
    assert {t["taxType"]: t["amount"] for t in ln["taxableItems"]} == {"T1": 1400, "T4": 300}
    assert d["totalAmount"] == 11100 == float(inv.amount_total)
    fake = FakeEta()
    assert eta.submit_pending(eg.c, transport=fake) == 1
    doc.refresh_from_db()
    assert doc.state == "submitted" and doc.uuid == f"UUID-{inv.number}"
    assert eta.refresh(doc, transport=fake).state == "valid"
    assert doc.public_url().endswith(f"/print/documents/UUID-{inv.number}/share/LONG")
    # credit note references the original UUID
    cn = billing.create_refund(inv, eg.cfo, reason="price adjustment")
    cn = billing.post_invoice(cn, eg.cfo)
    assert eta.build_document(cn, eg.eta)["references"] == [doc.uuid]
    assert EtaDocument.objects.get(invoice=cn).document_type == "C"
    eta.request_cancel(doc, "duplicate", eg.cfo, transport=fake)
    assert doc.state == "cancel_requested"


def test_eta_invalid_document_and_resubmit(eg):
    eg.eta.enabled = True
    eg.eta.save()
    inv = billing.post_invoice(invoice(eg, eg.item, 1, 100), eg.accountant)
    eta.submit_pending(eg.c, transport=FakeEta())
    doc = EtaDocument.objects.get(invoice=inv)
    eta.refresh(doc, transport=FakeEta(status="Invalid"))
    assert doc.state == "invalid" and doc.errors[0]["step"] == "Core"
    eta.resubmit(doc)
    assert doc.state == "pending"


def test_eta_signing_agent(eg):
    eg.eta.signer_mode, eg.eta.signer_url, eg.eta.signer_token = "service", "https://signer.local/sign", "t"
    eg.eta.enabled = True
    eg.eta.save()
    inv = billing.post_invoice(invoice(eg, eg.item, 1, 100), eg.accountant)
    seen = {}

    def signer(method, url, headers, body):
        seen.update(json.loads(body))
        return 200, {"signature": "MIIB...base64"}
    signed = eta.sign(eg.eta, eta.build_document(inv, eg.eta), transport=signer)
    assert signed["documentTypeVersion"] == "1.0"
    assert signed["signatures"] == [{"signatureType": "I", "value": "MIIB...base64"}]
    assert seen["canonical"].startswith('"ISSUER""ADDRESS""BRANCHID""0""COUNTRY""EG"')


def test_canonical_serialization():
    doc = {"a": "x", "lines": [{"v": 1.5}, {"v": 2}], "n": {"k": "q\"t"}, "signatures": [{"value": "s"}]}
    assert eta.serialize(doc) == '"A""x""LINES""LINES""V""1.5""LINES""V""2""N""K""q\\"t"'


def test_opening_items_are_not_e_invoiced(eg):
    from apps.ledger.imports import open_items
    eg.eta.enabled = True
    eg.eta.save()
    open_items(eg.c, "receivable", [{"partner_code": "C1", "number": "OLD-1", "date": str(eg.d(1, 1)),
                                     "amount": "5000"}], eg.cfo)
    assert not EtaDocument.objects.exists()
