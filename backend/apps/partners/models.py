from decimal import Decimal

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

    class Meta:
        unique_together = [("company", "code")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} {self.name}"
