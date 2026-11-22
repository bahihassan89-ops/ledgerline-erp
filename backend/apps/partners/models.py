from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import CompanyScoped, Currency
from apps.core.signals import audited


@audited
class PaymentTerm(CompanyScoped):
    name = models.CharField(max_length=60)
    days = models.PositiveIntegerField(default=0, help_text="Due date = document date + days")

    class Meta:
        unique_together = [("company", "name")]
        ordering = ["days"]

    def __str__(self):
        return self.name


@audited
class Partner(CompanyScoped):
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True)
    is_customer = models.BooleanField(default=False)
    is_vendor = models.BooleanField(default=False)
    tax_number = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    country = models.CharField(max_length=2, blank=True)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    payment_term = models.ForeignKey(PaymentTerm, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    credit_limit = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"),
                                       help_text="0 = no credit allowed check disabled; see credit_check")
    credit_check = models.BooleanField(default=False, help_text="Block sales orders exceeding the credit limit")
    on_hold = models.BooleanField(default=False, help_text="Block new sales / purchases")
    # Optional overrides of the company default AR / AP control accounts
    receivable_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                           related_name="+")
    payable_account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+")
    # Withholding tax (Egypt: discount & addition system). For vendors: the rate we withhold from
    # their bills. For customers: the rate they withhold from our invoices.
    withholding_tax = models.ForeignKey("ledger.Tax", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+")
    # E-invoicing receiver data (ETA): B = business (tax reg. no.), P = person (national ID), F = foreign
    eta_receiver_type = models.CharField(max_length=1, default="B", choices=[("B", "Business"), ("P", "Person"),
                                                                            ("F", "Foreign")])
    national_id = models.CharField(max_length=20, blank=True)
    governate = models.CharField(max_length=60, blank=True)
    region_city = models.CharField(max_length=60, blank=True)
    street = models.CharField(max_length=200, blank=True)
    building_number = models.CharField(max_length=30, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    # Intercompany: this partner record actually represents another company in our own group
    # (e.g. a sibling branch set up as a separate legal entity). Transactions against it are
    # recorded as ordinary sales/purchases via due-to/due-from control accounts -- no automatic
    # elimination is performed; see the "Intercompany reconciliation" report.
    is_intercompany = models.BooleanField(default=False, help_text="This partner is another company in our group")
    linked_company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="intercompany_partners",
                                       help_text="The group company this partner record corresponds to")
    # Explicit, bidirectional link to the mirroring Partner row kept in linked_company's own books
    # (the record that represents *us* in their ledger). Set automatically by
    # apps.partners.services.sync_intercompany_mirror() -- never guessed at report time, so the
    # reconciliation report and the pre-close lock can both rely on it actually existing.
    mirror_partner = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                       help_text="The matching partner record in the linked company's own books")
    # Bank details -- deliberately NOT writable via PartnerSerializer (see partners.serializers).
    # Vendor/customer bank-detail changes are a classic BEC / invoice-redirection fraud vector, so
    # these fields are only ever written by apps.partners.services.apply_bank_change(), after a
    # PartnerBankChangeRequest has cleared both maker-checker approval AND an independent callback
    # verification (PartnerBankVerification). Never edit these directly, even from the admin.
    bank_name = models.CharField(max_length=120, blank=True,
                                 help_text="Changed only via PartnerBankChangeRequest -- see apps.partners.services")
    bank_account_number = models.CharField(max_length=40, blank=True,
                                           help_text="Changed only via PartnerBankChangeRequest")
    iban = models.CharField(max_length=34, blank=True, help_text="Changed only via PartnerBankChangeRequest")
    swift_bic = models.CharField(max_length=11, blank=True, help_text="Changed only via PartnerBankChangeRequest")
    bank_country = models.CharField(max_length=2, blank=True, help_text="Changed only via PartnerBankChangeRequest")

    class Meta:
        unique_together = [("company", "code")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} {self.name}"


class BankChangeReasonCode(models.TextChoices):
    """Structured reason for why a vendor/customer's bank details are being changed -- mirrors
    billing.ReturnReason / inventory.CountRootCause. Required on every PartnerBankChangeRequest so
    the change is reportable, not a free-text afterthought."""
    VENDOR_REQUESTED = "vendor_requested", "Vendor/customer requested the change"
    BANK_MIGRATION = "bank_migration", "Bank migration / account consolidation"
    CORRECTION = "correction", "Correcting a data-entry error"
    NEW_VENDOR_SETUP = "new_vendor_setup", "Initial setup for a new vendor/customer"
    OTHER = "other", "Other (explain in notes)"


@audited
class PartnerBankChangeRequest(CompanyScoped):
    """The maker-checker 'document' the generic approval engine (apps.approvals) acts on for a
    proposed change to a Partner's bank details. Nothing writes to Partner.bank_* directly -- every
    change must flow through here: apps.partners.services.request_bank_change() creates this record
    (state 'draft' or, once an ApprovalRule matches, 'to_approve'), approvals.services.decide() flips
    it to 'approved' once a Controller signs off, and apply_bank_change() then ALSO requires a linked
    PartnerBankVerification -- an independent callback confirmation, on a phone number sourced from
    existing records rather than from this request -- before it will actually copy the proposed
    values onto the real Partner row. Both gates exist because a single compromised inbox or a single
    rubber-stamp approval is not enough to catch vendor-bank-detail-change fraud (a classic BEC /
    invoice-redirection attack); the approval proves someone senior reviewed the *request*, the
    verification proves someone independently confirmed the *vendor/customer themselves* asked for it."""
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="bank_change_requests")
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=40, blank=True)
    iban = models.CharField(max_length=34, blank=True)
    swift_bic = models.CharField(max_length=11, blank=True)
    bank_country = models.CharField(max_length=2, blank=True)
    reason_code = models.CharField(max_length=24, choices=BankChangeReasonCode.choices)
    reason = models.TextField(blank=True)
    state = models.CharField(max_length=10, default="draft")

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"Bank change for {self.partner.name}"


class PartnerBankVerification(models.Model):
    """The independent callback confirmation that apply_bank_change() requires in addition to
    maker-checker approval: a human actually called the vendor/customer back, on a number sourced
    from existing records (the partner's phone on file, a known prior contact -- never from the
    change request itself, which could be the very channel an attacker compromised), and confirms
    the new bank details are genuine."""
    request = models.OneToOneField(PartnerBankChangeRequest, on_delete=models.PROTECT, related_name="verification")
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    verified_at = models.DateTimeField(auto_now_add=True)
    phone_called = models.CharField(max_length=40, help_text="Number called to confirm -- must be sourced from "
                                                              "existing records (partner's phone on file or a "
                                                              "known prior contact), never from this request.")
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Verification of {self.request}"
