from django.db import transaction

from apps.approvals.models import DocumentType
from apps.approvals.services import ensure_approved
from apps.core import audit
from apps.core.exceptions import BusinessError

from .models import BankChangeReasonCode, Partner, PartnerBankChangeRequest, PartnerBankVerification

BANK_FIELDS = ["bank_name", "bank_account_number", "iban", "swift_bic", "bank_country"]


@transaction.atomic
def sync_intercompany_mirror(partner):
    """Guarantee that an is_intercompany Partner has a real, explicit, bidirectional mirror
    record in the linked company's own books -- the record that represents *us* in their ledger.

    Previously the "mirror" was only ever guessed at report time (apps.ledger.reports.
    intercompany_reconciliation reverse-matching company/linked_company), so it was entirely
    possible to run an intercompany partner for months with no reciprocal record anywhere and
    never find out. This makes the link a real FK (Partner.mirror_partner) created/repaired here,
    called from PartnerViewSet.perform_create/perform_update whenever is_intercompany or
    linked_company changes. No-op for ordinary (non-intercompany) partners.

    Returns the mirror Partner, or None if partner isn't intercompany / has no linked_company yet.
    """
    if not partner.is_intercompany or not partner.linked_company_id:
        return None
    if partner.linked_company_id == partner.company_id:
        raise BusinessError("An intercompany partner cannot link to its own company.", "ic_self_link")

    mirror = partner.mirror_partner
    if mirror is None or mirror.company_id != partner.linked_company_id or mirror.linked_company_id != partner.company_id:
        # Repair path: an older record (or a hand-edited one) may already have a reverse-pointing
        # partner that was only ever discovered by guessing -- reuse it instead of duplicating.
        mirror = Partner.objects.filter(company_id=partner.linked_company_id,
                                        linked_company_id=partner.company_id, is_intercompany=True).first()

    if mirror is None:
        mirror = Partner.objects.create(
            company_id=partner.linked_company_id,
            code=partner.company.code,
            name=partner.company.name,
            name_ar=partner.company.name_ar or "",
            is_customer=partner.is_customer,
            is_vendor=partner.is_vendor,
            is_intercompany=True,
            linked_company_id=partner.company_id,
        )

    if mirror.mirror_partner_id != partner.pk:
        mirror.mirror_partner = partner
        mirror.save(update_fields=["mirror_partner"])
    if partner.mirror_partner_id != mirror.pk:
        Partner.objects.filter(pk=partner.pk).update(mirror_partner=mirror)
        partner.mirror_partner = mirror
    return mirror


@transaction.atomic
def request_bank_change(partner, user, *, bank_name="", bank_account_number="", iban="", swift_bic="",
                        bank_country="", reason_code="", reason=""):
    """Open a maker-checker request to change `partner`'s bank details. Never writes to
    Partner.bank_* directly -- see apply_bank_change() for the only path that does, and only once
    this request has cleared BOTH approval and an independent callback verification."""
    if reason_code not in BankChangeReasonCode.values:
        raise BusinessError("Choose a reason for this bank-detail change.", "reason_code_required")
    if reason_code == BankChangeReasonCode.OTHER and not reason.strip():
        raise BusinessError("Explain the reason when 'Other' is selected.", "reason_required")
    req = PartnerBankChangeRequest.objects.create(
        company=partner.company, partner=partner, bank_name=bank_name, bank_account_number=bank_account_number,
        iban=iban, swift_bic=swift_bic, bank_country=bank_country, reason_code=reason_code, reason=reason,
        created_by=user)
    # Bank-detail changes always require approval -- unlike returns/counts there is no "small enough
    # to skip" case, so the seeded ApprovalRule for this document type always has min_amount=0. Pass
    # 0 as the "amount" purely because ensure_approved()/find_rule() are amount-keyed; the rule match
    # is unconditional here, not threshold-gated. If a company somehow has no matching rule yet (e.g.
    # the backfill migration hasn't run), ensure_approved() returns True without touching req.state --
    # still route it through "approved" so verify_bank_change()/apply_bank_change() aren't skipped.
    if ensure_approved(partner.company, DocumentType.PARTNER_BANK_CHANGE, req, 0, user):
        req.state = "approved"
        req.save(update_fields=["state"])
    return req


@transaction.atomic
def verify_bank_change(req, user, phone_called, notes=""):
    """Record the independent callback confirmation apply_bank_change() requires in addition to
    maker-checker approval. `phone_called` must be sourced from existing records (the partner's
    phone on file, or another known prior contact) -- never copied from the request itself, since
    that's the exact channel a BEC/invoice-redirection attacker would have compromised. Segregation
    of duties: whoever requested the change cannot also be the one who verifies it."""
    if req.state != "approved":
        raise BusinessError("This bank-detail change must be approved before it can be verified.", "not_approved")
    if hasattr(req, "verification"):
        raise BusinessError("This bank-detail change has already been verified.", "already_verified")
    if user.pk == req.created_by_id:
        raise BusinessError("Segregation of duties: you cannot verify a bank-detail change you requested.",
                            "sod_self_verification")
    if not phone_called or not phone_called.strip():
        raise BusinessError("Record the phone number called to confirm this change.", "phone_required")
    verification = PartnerBankVerification.objects.create(
        request=req, verified_by=user, phone_called=phone_called, notes=notes)
    audit.record("verify_bank_change", req, {"phone_called": phone_called, "verified_by": user.pk})
    return verification


@transaction.atomic
def apply_bank_change(req, user):
    """Copy the proposed bank details onto the real Partner row -- the only place that ever writes
    to Partner.bank_*. Requires BOTH an 'approved' maker-checker decision AND a linked
    PartnerBankVerification; either gate missing blocks this, mirroring the
    approved-but-not-yet-applied pattern used by inventory.services.apply_count() and
    LandedCostViewSet.post()."""
    if req.state != "approved":
        raise BusinessError("This bank-detail change must be approved before it can be applied.", "not_approved")
    if not hasattr(req, "verification"):
        raise BusinessError("This bank-detail change needs an independent callback verification before it can be "
                            "applied.", "not_verified")
    partner = req.partner
    for f in BANK_FIELDS:
        setattr(partner, f, getattr(req, f))
    partner.save(update_fields=BANK_FIELDS)
    req.state = "done"
    req.save(update_fields=["state"])
    audit.record("apply_bank_change", partner, {"request": req.pk})
    return partner
