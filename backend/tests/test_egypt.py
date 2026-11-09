"""Egyptian tax rules: VAT 14%, withholding (discount & addition), Form 41, VAT return, ETA e-invoicing."""
import json

import pytest

from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceLine
from apps.core.exceptions import BusinessError
from apps.einvoice import services as eta
from apps.einvoice.models import EtaDocument
from apps.partners.models import Partner

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


def test_withholding_threshold_cumulative_per_payee(eg):
    """EGP 300 applies to the amount paid to a payee across the filing period, not to each document
    in isolation (FC review, Section 11/20: "EGP 300 applies to each amount paid, not to each
    document")."""
    b1 = bill(eg, 200, ref="B-cum1")  # 200 alone < 300 → no withholding
    assert b1.amount_wht == 0 and b1.amount_total == D(228)
    b2 = bill(eg, 150, ref="B-cum2")  # same quarter, same vendor: 200 + 150 = 350 ≥ 300 cumulatively
    assert b2.amount_wht == D(150) * D("3") / D(100)
    assert b2.amount_total == D(150) + D(150) * D("14") / D(100) - b2.amount_wht
    # a document in the next quarter starts a fresh cumulation window
    b3 = Invoice.objects.create(company=eg.c, move_type="in_invoice", partner=eg.vendor, currency_id="EGP",
                                date=eg.d(7, 1), reference="B-cum3")
    InvoiceLine.objects.create(invoice=b3, description="Maintenance service", account=eg.A["520203"], quantity=1,
                               unit_price=100, tax=eg.T["VAT14-P"], withholding_tax=eg.T["WHT3-SRV-P"])
    b3 = billing.post_invoice(b3, eg.accountant)
    assert b3.amount_wht == 0
    # a different vendor's payments don't count towards this vendor's cumulative threshold
    other_vendor = Partner.objects.create(company=eg.c, code="V2", name="Other Vendor", is_vendor=True)
    b4 = Invoice.objects.create(company=eg.c, move_type="in_invoice", partner=other_vendor, currency_id="EGP",
                                date=eg.d(4, 11), reference="B-cum4")
    InvoiceLine.objects.create(invoice=b4, description="Maintenance service", account=eg.A["520203"], quantity=1,
                               unit_price=120, tax=eg.T["VAT14-P"], withholding_tax=eg.T["WHT3-SRV-P"])
    b4 = billing.post_invoice(b4, eg.accountant)
    assert b4.amount_wht == 0


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


def test_eta_export_invoice_uses_bank_rate_not_invoice_rate(eg):
    # Item #18: an export invoice can be legitimately settled at a business-negotiated rate that
    # differs from the actual bank/CBE rate of the day -- Invoice.rate (used for amountEGP/vat/wht,
    # which must stay consistent with what's posted to the GL) is NOT what ETA wants reported in
    # currencyExchangeRate; that field must reflect the real bank rate for that date, independent of
    # whatever commercial rate the invoice itself was booked at.
    from apps.core.models import ExchangeRate

    eg.eta.enabled = True
    eg.eta.save()
    ExchangeRate.objects.create(company=eg.c, currency_id="USD", date=eg.d(4, 1), rate=D("50.00"))  # the bank rate
    inv = Invoice.objects.create(company=eg.c, move_type="out_invoice", partner=eg.customer, currency_id="USD",
                                 date=eg.d(4, 12), rate=D("49.00"))  # negotiated settlement rate, != bank rate
    InvoiceLine.objects.create(invoice=inv, product=eg.item, description=eg.item.name, account=eg.A["410101"],
                               quantity=1, unit_price=100, tax=eg.T["VAT14-S"])
    inv = billing.post_invoice(inv, eg.accountant)
    assert inv.rate == D("49.00")  # the GL/books keep the negotiated rate, untouched

    d = eta.build_document(inv, eg.eta)
    ln = d["invoiceLines"][0]
    assert ln["unitValue"]["currencyExchangeRate"] == 50.0  # ETA gets the bank rate, not 49
    # amountEGP must still equal amountSold * currencyExchangeRate for ETA's own math to check out.
    assert round(ln["unitValue"]["amountSold"] * ln["unitValue"]["currencyExchangeRate"], 2) == \
        round(ln["unitValue"]["amountEGP"], 2)

    # Falls back to the invoice's own rate when no bank rate was ever recorded for that date, instead
    # of failing the ETA submission outright.
    ExchangeRate.objects.filter(company=eg.c, currency_id="USD").delete()
    d2 = eta.build_document(inv, eg.eta)
    assert d2["invoiceLines"][0]["unitValue"]["currencyExchangeRate"] == 49.0


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


def test_eta_sync_worker_respects_auto_submit_flag(eg, monkeypatch):
    # Item #11: EtaSettings.auto_submit now defaults to False (a company testing ETA in preprod
    # before go-live should not have every posted invoice auto-submitted). The background
    # eta_sync worker used to call submit_pending() for every *enabled* company regardless of
    # this flag, defeating the point of turning auto_submit off -- within ~60s the next loop
    # iteration would submit everything anyway. This locks in the fix: the worker only submits
    # when auto_submit is True, but still refreshes statuses either way.
    from apps.einvoice.management.commands import eta_sync as eta_sync_cmd

    eg.eta.enabled = True
    assert eg.eta.auto_submit is False  # the new default
    eg.eta.save()

    calls = {"submit": 0, "refresh": 0}
    monkeypatch.setattr(eta_sync_cmd.services, "submit_pending", lambda c: calls.__setitem__("submit", calls["submit"] + 1) or 0)
    monkeypatch.setattr(eta_sync_cmd.services, "refresh_submitted", lambda c: calls.__setitem__("refresh", calls["refresh"] + 1) or 0)

    eta_sync_cmd.Command().handle(loop=False, received=False)
    assert calls == {"submit": 0, "refresh": 1}  # auto_submit off: no submission, status still refreshed

    eg.eta.auto_submit = True
    eg.eta.save()
    eta_sync_cmd.Command().handle(loop=False, received=False)
    assert calls == {"submit": 1, "refresh": 2}  # auto_submit on: the worker submits


def test_opening_items_are_not_e_invoiced(eg):
    from apps.ledger.imports import open_items
    eg.eta.enabled = True
    eg.eta.save()
    open_items(eg.c, "receivable", [{"partner_code": "C1", "number": "OLD-1", "date": str(eg.d(1, 1)),
                                     "amount": "5000"}], eg.cfo)
    assert not EtaDocument.objects.exists()
