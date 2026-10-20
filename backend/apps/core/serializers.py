from rest_framework import serializers

from .models import AuditLog, Company, CostCenter, Currency, ExchangeRate
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


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ["id", "at", "user", "actor_label", "action", "object_type", "object_id", "object_repr",
                  "changes", "ip", "request_id", "hash"]
