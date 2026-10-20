from rest_framework import serializers

from apps.core.serializers_base import CompanyScopedSerializer

from .models import Partner, PaymentTerm


class PaymentTermSerializer(CompanyScopedSerializer):
    class Meta:
        model = PaymentTerm
        fields = ["id", "name", "days"]


class PartnerSerializer(CompanyScopedSerializer):
    class Meta:
        model = Partner
        fields = ["id", "code", "name", "is_customer", "is_vendor", "tax_number", "email", "phone", "address",
                  "country", "currency", "payment_term", "credit_limit", "credit_check", "on_hold",
                  "receivable_account", "payable_account", "notes", "active"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        req = self.context.get("request")
        company = self.context.get("company")
        sensitive = {"credit_limit", "credit_check", "on_hold"}
        changing = [k for k in sensitive if k in attrs and (not self.instance or getattr(self.instance, k) != attrs[k])]
        if changing and req and "partners.credit_limit.manage" not in req.user.permissions_for(company.pk):
            if self.instance or attrs.get("credit_limit") or attrs.get("credit_check") or attrs.get("on_hold"):
                raise serializers.ValidationError({changing[0]: "Changing credit settings requires the "
                                                                "credit-limit permission."})
        for f, t in (("receivable_account", "receivable"), ("payable_account", "payable")):
            acc = attrs.get(f)
            if acc and acc.account_type != t:
                raise serializers.ValidationError({f: f"Must be a {t}-type account."})
        if not attrs.get("is_customer", getattr(self.instance, "is_customer", False)) and \
                not attrs.get("is_vendor", getattr(self.instance, "is_vendor", False)):
            raise serializers.ValidationError("A partner must be a customer, a vendor or both.")
        return attrs
