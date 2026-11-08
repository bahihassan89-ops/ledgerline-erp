from django.db import transaction

from apps.core.exceptions import BusinessError

from .models import Partner


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
