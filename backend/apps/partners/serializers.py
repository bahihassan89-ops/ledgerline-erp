from rest_framework import serializers

from apps.core.serializers_base import CompanyScopedSerializer

from .models import Partner, PaymentTerm


class PaymentTermSerializer(CompanyScopedSerializer):
    class Meta:
        model = PaymentTerm
        fields = ["id", "name", "days"]


class PartnerSerializer(CompanyScopedSerializer):
    mirror_partner_name = serializers.CharField(source="mirror_partner.name", read_only=True, default=None)

    class Meta:
        model = Partner
        fields = ["id", "code", "name", "name_ar", "is_customer", "is_vendor", "tax_number", "email", "phone",
                  "address", "country", "currency", "payment_term", "credit_limit", "credit_check", "on_hold",
                  "receivable_account", "payable_account", "withholding_tax", "eta_receiver_type", "national_id",
                  "governate", "region_city", "street", "building_number", "postal_code", "notes",
                  "is_intercompany", "linked_company", "mirror_partner", "mirror_partner_name", "active"]
        read_only_fields = ["mirror_partner"]

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
        wht = attrs.get("withholding_tax")
        if wht and wht.kind != "withholding":
            raise serializers.ValidationError({"withholding_tax": "Choose a withholding tax code."})
        for f, t in (("receivable_account", "receivable"), ("payable_account", "payable")):
            acc = attrs.get(f)
            if acc and acc.account_type != t:
                raise serializers.ValidationError({f: f"Must be a {t}-type account."})
        if not attrs.get("is_customer", getattr(self.instance, "is_customer", False)) and \
                not attrs.get("is_vendor", getattr(self.instance, "is_vendor", False)):
            raise serializers.ValidationError("A partner must be a customer, a vendor or both.")
        is_ic = attrs.get("is_intercompany", getattr(self.instance, "is_intercompany", False))
        if is_ic:
            code = attrs.get("code", getattr(self.instance, "code", ""))
            if not code or not code.strip():
                raise serializers.ValidationError(
                    {"code": "A partner code is required for intercompany balances (used to match the mirror "
                             "record in the other company's books)."})
            linked = attrs.get("linked_company", getattr(self.instance, "linked_company", None))
            if not linked:
                raise serializers.ValidationError(
                    {"linked_company": "Pick which group company this intercompany partner represents."})
            if company and linked.pk == company.pk:
                raise serializers.ValidationError({"linked_company": "An intercompany partner cannot link to its "
                                                                     "own company."})
        return attrs
