from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceLine

from .conftest import make_user


def client(env, user):
    c = APIClient()
    c.force_authenticate(user)
    c.credentials(HTTP_X_COMPANY=str(env.c.pk))
    return c


def test_new_endpoints_respond(eg):
    cl = client(eg, eg.cfo)
    for url in ["/api/assets/", "/api/assets/register/", "/api/assets/tax-depreciation/", "/api/asset-categories/",
                "/api/bank-statements/", f"/api/bank-statements/reconciliation/?journal={eg.J['BNK'].pk}",
                "/api/eta-documents/", "/api/eta-settings/", "/api/budgets/", "/api/recurring-entries/",
                "/api/work-centers/", "/api/boms/", "/api/manufacturing-orders/", "/api/manufacturing-orders/wip/",
                "/api/landed-costs/", "/api/close-checklist/", "/api/fx-revaluation/", "/api/payments/cheques/",
                "/api/reports/vat-return/", "/api/reports/withholding/", "/api/reports/withholding-customers/",
                "/api/reports/cash-flow/", "/api/reports/fixed-assets/",
                "/api/reports/income-statement/?compare_from=2025-01-01&compare_to=2025-12-31"]:
        r = cl.get(url)
        assert r.status_code == 200, (url, r.status_code, r.content[:300])


def test_eta_secret_never_returned(eg):
    cl = client(eg, eg.cfo)
    r = cl.patch("/api/eta-settings/", {"client_secret": "TOPSECRET", "taxpayer_activity_code": "4620"}, format="json")
    assert r.status_code == 200 and "TOPSECRET" not in r.content.decode() and r.data["has_secret"]
    r = cl.patch("/api/eta-settings/", {"environment": "prod", "signer_mode": "unsigned_preprod"}, format="json")
    assert r.status_code == 400


def test_clerk_cannot_run_depreciation_or_revaluation(eg):
    clerk = make_user(eg.c, "eg_clerk", "Accounting Clerk")
    cl = client(eg, clerk)
    assert cl.post("/api/assets/run-depreciation/", {}, format="json").status_code == 403
    assert cl.post("/api/fx-revaluation/", {}, format="json").status_code == 403
    assert cl.post("/api/imports/opening-balances/", {"rows": []}, format="json").status_code == 403


def test_attachments(eg):
    inv = Invoice.objects.create(company=eg.c, move_type="out_invoice", partner=eg.customer, currency_id="EGP",
                                 date=eg.d(3, 1))
    InvoiceLine.objects.create(invoice=inv, product=eg.service, description="x", account=eg.A["410102"], quantity=1,
                               unit_price=100, tax=eg.T["VAT14-S"])
    cl = client(eg, eg.cfo)
    pdf = SimpleUploadedFile("contract.pdf", b"%PDF-1.4 test", content_type="application/pdf")
    r = cl.post("/api/attachments/", {"object_type": "billing.invoice", "object_id": inv.pk, "file": pdf},
                format="multipart")
    assert r.status_code == 201, r.content
    aid = r.data["id"]
    fake = SimpleUploadedFile("evil.pdf", b"MZ\x90\x00 not a pdf", content_type="application/pdf")
    assert cl.post("/api/attachments/", {"object_type": "billing.invoice", "object_id": inv.pk, "file": fake},
                   format="multipart").status_code == 400
    exe = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
    assert cl.post("/api/attachments/", {"object_type": "billing.invoice", "object_id": inv.pk, "file": exe},
                   format="multipart").status_code == 400
    r = cl.get(f"/api/attachments/{aid}/download/")
    assert r.status_code == 200 and b"".join(r.streaming_content).startswith(b"%PDF")
    billing.post_invoice(inv, eg.cfo)
    assert cl.delete(f"/api/attachments/{aid}/").status_code == 422  # evidence of a posted invoice
    auditor = make_user(eg.c, "eg_aud", "Auditor (read-only)")
    assert client(eg, auditor).get(f"/api/attachments/?object_type=billing.invoice&object_id={inv.pk}").status_code == 200
    admin = make_user(eg.c, "eg_admin", "System Administrator")
    assert client(eg, admin).get(f"/api/attachments/{aid}/download/").status_code == 403
