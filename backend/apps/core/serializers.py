from rest_framework import serializers

from .models import (AuditLog, Branch, Company, CompanyModule, CompanyType, CostCenter, Currency, ExchangeRate,
                     Notification, PushSubscription)
from .modules import MODULE_REGISTRY
from .serializers_base import CompanyScopedSerializer


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ["code", "name", "symbol", "decimals", "active"]


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name", "name_ar", "legal_name", "code", "tax_number", "registration_number", "tax_office",
                  "base_currency",
                  "country", "address", "fiscal_year_start_month", "lock_date", "bill_price_tolerance_pct", "active"]
        read_only_fields = ["code", "base_currency", "active"]

    def validate_lock_date(self, value):
        old = self.instance.lock_date if self.instance else None
        if old and (value is None or value < old):
            req = self.context.get("request")
            if not req or not req.user.permissions_for(self.instance.pk) >= {"ledger.period.lock"}:
                raise serializers.ValidationError("Moving the lock date backwards requires the period-lock permission.")
        return value


class ExchangeRateSerializer(CompanyScopedSerializer):
    run_model_clean = True

    class Meta:
        model = ExchangeRate
        fields = ["id", "currency", "date", "rate"]


class CostCenterSerializer(CompanyScopedSerializer):
    class Meta:
        model = CostCenter
        fields = ["id", "code", "name", "parent", "active"]


class BranchSerializer(CompanyScopedSerializer):
    class Meta:
        model = Branch
        fields = ["id", "code", "name", "address", "is_main", "active"]


class CompanyDraftCreateSerializer(serializers.Serializer):
    """Input for starting a new company draft -- not a ModelSerializer: a handful of these
    fields (currency, vat, year) are only used once, by setup_company.setup(), not stored."""
    name = serializers.CharField(max_length=200)
    name_ar = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    code = serializers.SlugField(max_length=20)
    company_type = serializers.ChoiceField(choices=CompanyType.choices)
    country = serializers.CharField(max_length=2, required=False, allow_blank=True, default="")
    currency = serializers.CharField(max_length=3, required=False, allow_null=True, default=None)
    vat = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True, default=None)
    fy_start_month = serializers.IntegerField(required=False, min_value=1, max_value=12, default=1)
    year = serializers.IntegerField(required=False, allow_null=True, default=None)


class CompanyProvisioningSerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name", "name_ar", "legal_name", "code", "country", "base_currency", "company_type",
                  "state", "active", "fiscal_year_start_month", "submitted_by", "submitted_at", "approved_by",
                  "approved_at", "rejected_reason", "created_at"]
        read_only_fields = ["id", "code", "base_currency", "state", "active", "submitted_by", "submitted_at",
                            "approved_by", "approved_at", "rejected_reason", "created_at"]


class CompanyModuleSerializer(serializers.ModelSerializer):
    description = serializers.SerializerMethodField()

    class Meta:
        model = CompanyModule
        fields = ["id", "module_code", "enabled", "description"]
        read_only_fields = ["id"]

    def get_description(self, obj):
        return MODULE_REGISTRY.get(obj.module_code, "")


class RejectCompanySerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ["id", "at", "user", "actor_label", "action", "object_type", "object_id", "object_repr",
                  "changes", "ip", "request_id", "hash"]


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "company", "category", "title", "body", "url", "read_at", "created_at"]
        read_only_fields = fields


class PushSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushSubscription
        fields = ["id", "endpoint", "p256dh", "auth"]
