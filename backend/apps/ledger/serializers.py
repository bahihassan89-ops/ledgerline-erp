from rest_framework import serializers

from apps.core.serializers_base import CompanyScopedSerializer

from .models import (Account, AccountingSettings, FiscalPeriod, FiscalYear, Journal, JournalEntry, JournalLine,
                     Tax)


class AccountSerializer(CompanyScopedSerializer):
    run_model_clean = True
    parent_code = serializers.CharField(source="parent.code", read_only=True, default=None)
    is_group = serializers.BooleanField(read_only=True)

    class Meta:
        model = Account
        fields = ["id", "code", "name", "parent", "parent_code", "account_class", "account_type", "level", "is_group",
                  "reconcilable", "is_control", "currency", "requires_cost_center", "description", "active"]
        read_only_fields = ["level"]

    def validate(self, attrs):
        parent = attrs.get("parent", getattr(self.instance, "parent", None))
        if parent and "account_class" not in attrs and not self.instance:
            attrs["account_class"] = parent.account_class
        return super().validate(attrs)


class JournalSerializer(CompanyScopedSerializer):
    class Meta:
        model = Journal
        fields = ["id", "code", "name", "type", "default_account", "currency", "allow_manual", "active"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        t = attrs.get("type", getattr(self.instance, "type", None))
        acc = attrs.get("default_account", getattr(self.instance, "default_account", None))
        if t in ("bank", "cash"):
            if not acc or acc.account_type != t:
                raise serializers.ValidationError({"default_account": f"A {t} journal needs a {t}-type account."})
        return attrs


class FiscalPeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = FiscalPeriod
        fields = ["id", "fiscal_year", "number", "name", "start_date", "end_date", "state"]
        read_only_fields = fields


class FiscalYearSerializer(serializers.ModelSerializer):
    periods = FiscalPeriodSerializer(many=True, read_only=True)
    closing_entry_number = serializers.CharField(source="closing_entry.number", read_only=True, default=None)

    class Meta:
        model = FiscalYear
        fields = ["id", "name", "start_date", "end_date", "closed", "closing_entry", "closing_entry_number", "periods"]
        read_only_fields = fields


class TaxSerializer(CompanyScopedSerializer):
    class Meta:
        model = Tax
        fields = ["id", "code", "name", "scope", "rate", "price_include", "account", "active"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        acc = attrs.get("account", getattr(self.instance, "account", None))
        scope = attrs.get("scope", getattr(self.instance, "scope", None))
        if acc and acc.is_group:
            raise serializers.ValidationError({"account": "Choose a postable (non-group) account."})
        expected = "tax_output" if scope == "sale" else "tax_input"
        if acc and acc.account_type != expected:
            raise serializers.ValidationError({"account": f"A {scope} tax must post to a '{expected}' account."})
        rate = attrs.get("rate")
        if rate is not None and not (0 <= rate <= 100):
            raise serializers.ValidationError({"rate": "Rate must be between 0 and 100."})
        return attrs


class AccountingSettingsSerializer(CompanyScopedSerializer):
    class Meta:
        model = AccountingSettings
        exclude = ["id", "company"]


class JournalLineSerializer(CompanyScopedSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    partner_name = serializers.CharField(source="partner.name", read_only=True, default=None)

    class Meta:
        model = JournalLine
        fields = ["id", "sequence", "account", "account_code", "account_name", "partner", "partner_name",
                  "description", "debit", "credit", "currency", "amount_currency", "cost_center", "tax", "tax_base",
                  "due_date"]
        read_only_fields = ["sequence", "currency", "amount_currency", "tax_base"]


class JournalEntrySerializer(CompanyScopedSerializer):
    """For manual entries: line debit/credit are entered in the entry currency; base amounts are
    computed by the server."""
    lines = JournalLineSerializer(many=True)
    journal_code = serializers.CharField(source="journal.code", read_only=True)
    posted_by_name = serializers.CharField(source="posted_by.username", read_only=True, default=None)
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)
    reversal_of_number = serializers.CharField(source="reversal_of.number", read_only=True, default=None)
    reversed_by = serializers.SerializerMethodField()

    class Meta:
        model = JournalEntry
        fields = ["id", "number", "journal", "journal_code", "date", "reference", "narration", "state", "currency",
                  "rate", "source_type", "source_id", "total_debit", "total_credit", "posted_by_name", "posted_at",
                  "created_by_name", "created_at", "reversal_of", "reversal_of_number", "reversed_by", "is_closing",
                  "lines"]
        read_only_fields = ["number", "state", "source_type", "source_id", "total_debit", "total_credit",
                            "posted_at", "reversal_of", "is_closing"]
        extra_kwargs = {"currency": {"required": False}, "rate": {"required": False}}

    def get_reversed_by(self, obj):
        try:
            return {"id": obj.reversed_by.pk, "number": obj.reversed_by.number}
        except JournalEntry.reversed_by.RelatedObjectDoesNotExist:
            return None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        journal = attrs.get("journal", getattr(self.instance, "journal", None))
        if journal and not journal.allow_manual:
            raise serializers.ValidationError({"journal": f"Journal {journal.code} does not accept manual entries."})
        lines = attrs.get("lines")
        if lines is not None:
            if len(lines) < 2:
                raise serializers.ValidationError({"lines": "At least two lines are required."})
            for i, ln in enumerate(lines, 1):
                d, c = ln.get("debit") or 0, ln.get("credit") or 0
                if d < 0 or c < 0:
                    raise serializers.ValidationError({"lines": f"Line {i}: amounts cannot be negative."})
                if d and c:
                    raise serializers.ValidationError({"lines": f"Line {i}: enter either a debit or a credit, not both."})
                if not d and not c:
                    raise serializers.ValidationError({"lines": f"Line {i}: amount is zero."})
            if sum(ln.get("debit") or 0 for ln in lines) != sum(ln.get("credit") or 0 for ln in lines):
                raise serializers.ValidationError({"lines": "Total debits must equal total credits."})
        return attrs
