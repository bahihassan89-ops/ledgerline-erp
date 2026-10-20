from rest_framework import serializers

from apps.core.serializers_base import CompanyScopedSerializer

from .models import ApprovalDecision, ApprovalRequest, ApprovalRule


class ApprovalRuleSerializer(CompanyScopedSerializer):
    class Meta:
        model = ApprovalRule
        fields = ["id", "name", "document_type", "min_amount", "approver_levels", "active"]

    def validate_approver_levels(self, value):
        if not isinstance(value, list) or not value or not all(isinstance(v, int) and 1 <= v <= 5 for v in value):
            raise serializers.ValidationError("Give a non-empty list of levels between 1 and 5, e.g. [3, 4].")
        return value


class ApprovalDecisionSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = ApprovalDecision
        fields = ["step", "user", "user_name", "approved", "comment", "at"]


class ApprovalRequestSerializer(serializers.ModelSerializer):
    decisions = ApprovalDecisionSerializer(many=True, read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.username", read_only=True)
    rule_name = serializers.CharField(source="rule.name", read_only=True)
    required_level = serializers.IntegerField(read_only=True)

    class Meta:
        model = ApprovalRequest
        fields = ["id", "document_type", "document_id", "document_label", "amount", "rule", "rule_name", "levels",
                  "current_step", "required_level", "state", "requested_by", "requested_by_name", "created_at",
                  "decisions"]
