from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import CompanyReadOnlyViewSet, CompanyViewSet

from . import services
from .models import ApprovalRequest, ApprovalRule
from .serializers import ApprovalRequestSerializer, ApprovalRuleSerializer


class ApprovalRuleViewSet(CompanyViewSet):
    queryset = ApprovalRule.objects.all()
    serializer_class = ApprovalRuleSerializer
    perms = {"list": "approvals.request.view", "matrix": "approvals.rule.manage", "*": "approvals.rule.manage"}
    pagination_class = None

    @action(detail=False, methods=["get"])
    def matrix(self, request):
        """Item #65 -- consistency report between ApprovalRule thresholds and Role approval
        limits/levels for this company. Read-only; fixing a flagged issue means editing the
        relevant Role or ApprovalRule, there's nothing to "apply" here."""
        result = services.approval_matrix(self.company)
        return Response({
            "rule_issues": [
                {"rule": ApprovalRuleSerializer(ri["rule"]).data, "issues": ri["issues"]}
                for ri in result["rule_issues"]
            ],
            "cross_rule_issues": [
                {"document_type": ci["document_type"], "detail": ci["detail"],
                 "lower_rule": ApprovalRuleSerializer(ci["lower_rule"]).data,
                 "higher_rule": ApprovalRuleSerializer(ci["higher_rule"]).data}
                for ci in result["cross_rule_issues"]
            ],
        })


class ApprovalRequestViewSet(CompanyReadOnlyViewSet):
    queryset = ApprovalRequest.objects.select_related("requested_by", "rule").prefetch_related("decisions__user")
    serializer_class = ApprovalRequestSerializer
    perms = {"list": "approvals.request.view", "approve": "approvals.request.decide",
             "reject": "approvals.request.decide"}
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = ["state", "document_type"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("mine") == "1":
            # pending requests the caller can decide now
            u = self.request.user
            level = u.level_for(self.company.pk)
            ids = [r.pk for r in qs.filter(state="pending").exclude(requested_by=u)
                   if r.required_level and r.required_level <= level]
            qs = qs.filter(pk__in=ids)
        return qs

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        r = services.decide(self.get_object(), request.user, True, request.data.get("comment", ""))
        return Response(self.get_serializer(r).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        r = services.decide(self.get_object(), request.user, False, request.data.get("comment", ""))
        return Response(self.get_serializer(r).data)
