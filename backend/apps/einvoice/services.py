"""ETA e-invoicing: validation, document building (schema v1.0), canonical serialization, signing,
submission and status tracking.

Signing: ETA requires a CAdES-BES signature made with an approved eSeal certificate, normally on a USB
token. Tokens cannot be used from a server process, so the system calls a *signing agent* running on a
trusted machine with the token attached (see docs/ACCOUNTING_DESIGN.md §ETA for the contract).
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta
from datetime import timezone as dt_tz
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core import audit
from apps.core.exceptions import BusinessError
from apps.core.money import ZERO, D, round_to

from .models import EtaDocState, EtaDocument, EtaReceivedDocument, EtaSettings

log = logging.getLogger("erp.eta")
_token_cache = {}


def eta_settings(company):
    return EtaSettings.objects.filter(company=company).first()


def enabled_for(company):
    s = eta_settings(company)
    return s if s and s.enabled else None


def doc_type(inv):
    if inv.move_type == "out_refund":
        return "C"
    if inv.move_type == "out_invoice" and inv.origin_invoice_id:
        return "D"
    return "I"


# ------------------------------------------------------------------ validation (before posting)

def _digits(v):
    return (v or "").replace(" ", "").replace("-", "")


def validate_for_eta(inv, lines):
    """Block posting of a customer document that ETA would reject, listing every problem at once."""
    st = enabled_for(inv.company)
    if not st:
        return
    c, p = inv.company, inv.partner
    errs = []
    if c.base_currency_id != "EGP":
        errs.append("ETA e-invoicing requires the company base currency to be EGP.")
    if len(_digits(c.tax_number)) != 9:
        errs.append("Company tax registration number must be 9 digits (Accounting Setup → Company).")
    if not st.taxpayer_activity_code:
        errs.append("Set the ETA taxpayer activity code (Integrations → ETA).")
    for f in ("governate", "region_city", "street", "building_number"):
        if not getattr(st, f):
            errs.append(f"Issuer address: '{f.replace('_', ' ')}' is required (Integrations → ETA).")
    if p.eta_receiver_type == "B":
        if len(_digits(p.tax_number)) != 9:
            errs.append(f"Customer {p.name}: a 9-digit tax registration number is required for business receivers.")
        for f in ("governate", "region_city", "street", "building_number"):
            if not getattr(p, f):
                errs.append(f"Customer {p.name}: address '{f.replace('_', ' ')}' is required.")
    elif p.eta_receiver_type == "P":
        total_egp = D(inv.amount_total) * D(inv.rate or 1)
        if total_egp >= st.person_id_threshold and len(_digits(p.national_id)) != 14:
            errs.append(f"Customer {p.name}: a 14-digit national ID is required for individuals when the total "
                        f"reaches EGP {st.person_id_threshold:,.0f}.")
    for ln in lines:
        tag = f"Line '{ln.description}'"
        if not ln.product_id:
            errs.append(f"{tag}: choose a product — ETA requires a registered item code on every line.")
        elif not ln.product.eta_item_code:
            errs.append(f"{tag}: product {ln.product.sku} has no ETA item code (EGS/GS1).")
        if not ln.tax_id:
            errs.append(f"{tag}: choose a VAT code (use the exempt/zero-rated code if no VAT applies).")
        elif not (ln.tax.eta_type and ln.tax.eta_subtype):
            errs.append(f"{tag}: VAT code {ln.tax.code} has no ETA tax type/sub-type.")
        if ln.withholding_tax_id and not (ln.withholding_tax.eta_type and ln.withholding_tax.eta_subtype):
            errs.append(f"{tag}: withholding code {ln.withholding_tax.code} has no ETA tax type/sub-type.")
    if doc_type(inv) in ("C", "D"):
        o = getattr(inv.origin_invoice, "eta_document", None) if inv.origin_invoice_id else None
        if not o or not o.uuid:
            errs.append("Credit/debit notes must reference an original invoice that was accepted by ETA.")
    if errs:
        raise BusinessError("This document cannot be e-invoiced as entered.", "eta_validation", {"errors": errs})


# ------------------------------------------------------------------ document building

def _n(x, places=5):
    return float(round_to(x, places))


def _issued_at(inv):
    if inv.posted_at and timezone.localtime(inv.posted_at).date() == inv.date:
        dt = inv.posted_at
    else:
        dt = timezone.make_aware(datetime.combine(inv.date, time(10, 0)))
    return dt.astimezone(dt_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_document(inv, st):
    c, p = inv.company, inv.partner
    rate = D(inv.rate or 1)
    foreign = inv.currency_id != "EGP"
    issuer_address = {"branchID": st.branch_id or "0", "country": "EG", "governate": st.governate,
                      "regionCity": st.region_city, "street": st.street, "buildingNumber": st.building_number}
    for k, f in (("postalCode", "postal_code"), ("floor", "floor"), ("room", "room"), ("landmark", "landmark"),
                 ("additionalInformation", "additional_information")):
        if getattr(st, f):
            issuer_address[k] = getattr(st, f)
    receiver = {"address": {"country": (p.country or "EG").upper(), "governate": p.governate,
                            "regionCity": p.region_city, "street": p.street, "buildingNumber": p.building_number},
                "type": p.eta_receiver_type,
                "id": _digits(p.tax_number) if p.eta_receiver_type == "B" else
                (_digits(p.national_id) if p.eta_receiver_type == "P" else (p.tax_number or "")),
                "name": p.name_ar or p.name}
    lines, t1_total, t4_total = [], ZERO, ZERO
    tot_sales = tot_disc = tot_net = ZERO
    for ln in inv.lines.select_related("product", "tax", "withholding_tax").order_by("sequence", "id"):
        net = D(ln.subtotal) * rate
        disc_pct = D(ln.discount_pct)
        sales = net / (1 - disc_pct / 100) if disc_pct < 100 else net
        disc_amt = sales - net
        unit_egp = sales / D(ln.quantity)
        vat = D(ln.tax_amount) * rate
        wht = D(ln.wht_amount) * rate
        taxable = [{"taxType": ln.tax.eta_type, "amount": _n(vat), "subType": ln.tax.eta_subtype,
                    "rate": _n(ln.tax.rate, 2)}]
        if wht:
            taxable.append({"taxType": ln.withholding_tax.eta_type, "amount": _n(wht),
                            "subType": ln.withholding_tax.eta_subtype, "rate": _n(ln.withholding_tax.rate, 2)})
        unit_value = {"currencySold": inv.currency_id, "amountEGP": _n(unit_egp)}
        if foreign:
            unit_value.update({"amountSold": _n(unit_egp / rate), "currencyExchangeRate": _n(rate)})
        lines.append({
            "description": ln.description, "itemType": ln.product.eta_item_type, "itemCode": ln.product.eta_item_code,
            "unitType": ln.product.eta_unit_type or "EA", "quantity": _n(ln.quantity), "internalCode": ln.product.sku,
            "salesTotal": _n(sales), "total": _n(net + vat - wht), "valueDifference": 0, "totalTaxableFees": 0,
            "netTotal": _n(net), "itemsDiscount": 0, "unitValue": unit_value,
            "discount": {"rate": _n(disc_pct, 2), "amount": _n(disc_amt)}, "taxableItems": taxable,
        })
        tot_sales += round_to(sales, 5)
        tot_disc += round_to(disc_amt, 5)
        tot_net += round_to(net, 5)
        t1_total += round_to(vat, 5)
        t4_total += round_to(wht, 5)
    tax_totals = [{"taxType": "T1", "amount": _n(t1_total)}]
    if t4_total:
        tax_totals.append({"taxType": "T4", "amount": _n(t4_total)})
    doc = {
        "issuer": {"address": issuer_address, "type": "B", "id": _digits(c.tax_number),
                   "name": c.name_ar or c.legal_name or c.name},
        "receiver": receiver,
        "documentType": doc_type(inv),
        "documentTypeVersion": "0.9" if st.signer_mode == "unsigned_preprod" else "1.0",
        "dateTimeIssued": _issued_at(inv),
        "taxpayerActivityCode": st.taxpayer_activity_code,
        "internalID": inv.number,
    }
    if inv.reference:
        doc["purchaseOrderReference"] = inv.reference[:100]
    if inv.sales_order_id and inv.sales_order.number:
        doc["salesOrderReference"] = inv.sales_order.number
    if doc["documentType"] in ("C", "D"):
        doc["references"] = [inv.origin_invoice.eta_document.uuid]
    doc.update({
        "invoiceLines": lines,
        "totalDiscountAmount": _n(tot_disc), "totalSalesAmount": _n(tot_sales), "netAmount": _n(tot_net),
        "taxTotals": tax_totals, "totalAmount": _n(tot_net + t1_total - t4_total),
        "extraDiscountAmount": 0, "totalItemsDiscountAmount": 0,
    })
    return doc


# ------------------------------------------------------------------ canonical serialization

def _scalar(v):
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)[1:-1]
    return json.dumps(v)


def serialize(value):
    """ETA canonical form: for every property, in document order, "NAME" (upper-case) followed by the
    serialized value; scalar values are quoted; for arrays the property name is written once and then
    repeated before every element. The `signatures` property is excluded."""
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            if k == "signatures":
                continue
            key = '"' + k.upper() + '"'
            if isinstance(v, list):
                out.append(key + "".join(key + serialize(item) for item in v))
            else:
                out.append(key + serialize(v))
        return "".join(out)
    return '"' + _scalar(value) + '"'


# ------------------------------------------------------------------ transport / client

def http_transport(method, url, headers, body):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except ValueError:
            return e.code, {"error": raw.decode(errors="replace")[:500]}


class EtaClient:
    def __init__(self, st, transport=None):
        self.st, self.t = st, transport or http_transport

    def token(self):
        key = (self.st.company_id, self.st.environment, self.st.client_id)
        cached = _token_cache.get(key)
        if cached and cached[1] > timezone.now():
            return cached[0]
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.st.client_id,
                                       "client_secret": self.st.client_secret, "scope": "InvoicingAPI"}).encode()
        code, data = self.t("POST", self.st.hosts()["id"] + "/connect/token",
                            {"Content-Type": "application/x-www-form-urlencoded"}, body)
        if code != 200 or "access_token" not in data:
            raise BusinessError(f"ETA authentication failed ({code}). Check client ID/secret.", "eta_auth",
                                {"response": data})
        _token_cache[key] = (data["access_token"], timezone.now() + timedelta(seconds=int(data.get("expires_in", 3600)) - 60))
        return data["access_token"]

    def call(self, method, path, payload=None):
        headers = {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json",
                   "Accept-Language": "en"}
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        return self.t(method, self.st.hosts()["api"] + path, headers, body)

    def submit(self, documents):
        return self.call("POST", "/api/v1/documentsubmissions", {"documents": documents})

    def details(self, uuid):
        return self.call("GET", f"/api/v1/documents/{uuid}/details")

    def cancel(self, uuid, reason):
        return self.call("PUT", f"/api/v1/documents/state/{uuid}/state", {"status": "cancelled", "reason": reason})

    def recent(self, page=1, size=100):
        return self.call("GET", f"/api/v1/documents/recent?pageNo={page}&pageSize={size}&InvoiceDirection=Received")


# ------------------------------------------------------------------ signing

def sign(st, document, transport=None):
    if st.signer_mode == "unsigned_preprod":
        if st.environment != "preprod":
            raise BusinessError("Unsigned documents are only accepted in pre-production.", "eta_signer")
        return document
    if not st.signer_url:
        raise BusinessError("Configure the signing agent URL (Integrations → ETA).", "eta_signer")
    canonical = serialize(document)
    code, data = (transport or http_transport)(
        "POST", st.signer_url, {"Content-Type": "application/json", "Authorization": f"Bearer {st.signer_token}"},
        json.dumps({"document": document, "canonical": canonical}, ensure_ascii=False).encode())
    if code != 200 or not data.get("signature"):
        raise BusinessError(f"Signing agent error ({code}): {data.get('error', '')}", "eta_signer")
    return {**document, "signatures": [{"signatureType": "I", "value": data["signature"]}]}


# ------------------------------------------------------------------ lifecycle

def queue(inv):
    """Called inside post_invoice for customer documents."""
    st = enabled_for(inv.company)
    if not st or inv.is_opening or not inv.is_customer:
        return None
    doc, _ = EtaDocument.objects.update_or_create(
        invoice=inv, defaults={"company": inv.company, "document_type": doc_type(inv), "internal_id": inv.number,
                               "state": EtaDocState.PENDING, "payload": {}, "errors": []})
    return doc


def submit_pending(company, transport=None, signer_transport=None, docs=None):
    st = enabled_for(company)
    if not st:
        return 0
    qs = docs if docs is not None else EtaDocument.objects.filter(company=company, state=EtaDocState.PENDING)[:50]
    batch, by_internal = [], {}
    for d in qs:
        try:
            payload = sign(st, build_document(d.invoice, st), signer_transport)
        except BusinessError as e:
            d.state, d.errors, d.attempts = EtaDocState.FAILED, [e.message], d.attempts + 1
            d.last_attempt_at = timezone.now()
            d.save()
            continue
        d.payload = payload
        batch.append(payload)
        by_internal[d.internal_id] = d
    if not batch:
        return 0
    client = EtaClient(st, transport)
    code, data = client.submit(batch)
    now = timezone.now()
    if code not in (200, 202):
        for d in by_internal.values():
            d.state, d.attempts, d.last_attempt_at = EtaDocState.FAILED, d.attempts + 1, now
            d.errors = [f"HTTP {code}", data]
            d.save()
        return 0
    sub = data.get("submissionId") or data.get("submissionUUID") or ""
    for a in data.get("acceptedDocuments", []):
        d = by_internal.get(a.get("internalId"))
        if d:
            d.state, d.submission_uuid = EtaDocState.SUBMITTED, sub
            d.uuid, d.long_id, d.hash_key = a.get("uuid", ""), a.get("longId", ""), a.get("hashKey", "")
            d.submitted_at, d.errors = now, []
    for r in data.get("rejectedDocuments", []):
        d = by_internal.get(r.get("internalId"))
        if d:
            d.state, d.errors = EtaDocState.REJECTED, [r.get("error", r)]
    for d in by_internal.values():
        d.attempts += 1
        d.last_attempt_at = now
        d.save()
        audit.record("eta_submit", d.invoice, {"state": d.state, "uuid": d.uuid, "errors": d.errors})
    return len(by_internal)


STATUS_MAP = {"valid": EtaDocState.VALID, "invalid": EtaDocState.INVALID, "rejected": EtaDocState.REJECTED,
              "cancelled": EtaDocState.CANCELLED, "submitted": EtaDocState.SUBMITTED}


def refresh(doc, transport=None):
    st = eta_settings(doc.company)
    code, data = EtaClient(st, transport).details(doc.uuid)
    if code != 200:
        raise BusinessError(f"ETA status lookup failed ({code}).", "eta_status", {"response": data})
    new = STATUS_MAP.get(str(data.get("status", "")).lower(), doc.state)
    if doc.state == EtaDocState.CANCEL_REQUESTED and new == EtaDocState.VALID:
        new = EtaDocState.CANCEL_REQUESTED  # still waiting for the receiver's approval
    doc.state = new
    if new == EtaDocState.VALID and not doc.validated_at:
        doc.validated_at = timezone.now()
    if new == EtaDocState.INVALID:
        steps = (data.get("validationResults") or {}).get("validationSteps", [])
        doc.errors = [{"step": s.get("name"), "error": s.get("error")} for s in steps
                      if str(s.get("status", "")).lower() == "invalid"] or [data.get("validationResults")]
    doc.save()
    return doc


def refresh_submitted(company, transport=None):
    n = 0
    for d in EtaDocument.objects.filter(company=company, state__in=[EtaDocState.SUBMITTED,
                                                                    EtaDocState.CANCEL_REQUESTED]):
        try:
            refresh(d, transport)
            n += 1
        except BusinessError as e:
            log.warning("ETA refresh %s: %s", d.uuid, e.message)
    return n


@transaction.atomic
def request_cancel(doc, reason, user, transport=None):
    if doc.state != EtaDocState.VALID:
        raise BusinessError("Only valid documents can be cancelled on ETA.", "invalid_state")
    if not reason:
        raise BusinessError("A cancellation reason is required.", "reason_required")
    code, data = EtaClient(eta_settings(doc.company), transport).cancel(doc.uuid, reason)
    if code not in (200, 202, 204):
        raise BusinessError(f"ETA refused the cancellation ({code}).", "eta_cancel", {"response": data})
    doc.state = EtaDocState.CANCEL_REQUESTED
    doc.save(update_fields=["state"])
    audit.record("eta_cancel_request", doc.invoice, {"uuid": doc.uuid, "reason": reason})
    return doc


def resubmit(doc):
    """After fixing master data (addresses, item codes) for an invalid/rejected/failed document."""
    if doc.state not in (EtaDocState.INVALID, EtaDocState.REJECTED, EtaDocState.FAILED):
        raise BusinessError("Only invalid, rejected or failed documents can be resubmitted.", "invalid_state")
    lines = list(doc.invoice.lines.select_related("product", "tax", "withholding_tax"))
    validate_for_eta(doc.invoice, lines)
    doc.state, doc.errors = EtaDocState.PENDING, []
    doc.save(update_fields=["state", "errors"])
    return doc


def fetch_received(company, transport=None, pages=5):
    """Pull documents vendors issued to us and match them to vendor bills (tax no. + bill reference)."""
    from apps.billing.models import Invoice
    st = enabled_for(company)
    if not st:
        return 0
    client, n = EtaClient(st, transport), 0
    for page in range(1, pages + 1):
        code, data = client.recent(page)
        if code != 200:
            break
        items = data.get("result", [])
        for it in items:
            obj, _ = EtaReceivedDocument.objects.update_or_create(company=company, uuid=it.get("uuid"), defaults={
                "internal_id": it.get("internalId", ""), "document_type": it.get("typeName", "")[:2],
                "issuer_id": it.get("issuerId", ""), "issuer_name": it.get("issuerName", ""),
                "date_issued": it.get("dateTimeIssued"), "total": D(it.get("total", 0)),
                "status": it.get("status", "")})
            if not obj.bill_id:
                bill = Invoice.objects.filter(company=company, move_type="in_invoice", state="posted",
                                              partner__tax_number__in=[obj.issuer_id],
                                              reference=obj.internal_id).first()
                if bill:
                    obj.bill = bill
                    obj.save(update_fields=["bill"])
            n += 1
        if len(items) < 100:
            break
    return n


def overdue_documents(company):
    st = eta_settings(company)
    days = st.max_submission_days if st else 7
    limit = timezone.now() - timedelta(days=days)
    return EtaDocument.objects.filter(company=company, state__in=[EtaDocState.PENDING, EtaDocState.FAILED,
                                                                  EtaDocState.REJECTED, EtaDocState.INVALID],
                                      created_at__lt=limit)
