"""Amount- and level-based approval workflow with segregation of duties."""
from django.apps import apps as django_apps
from django.db import transaction

from apps.core import audit, notify
from apps.core.exceptions import BusinessError

from .models import ApprovalDecision, ApprovalRequest, ApprovalRule, DocumentType

DOC_MODELS = {
    DocumentType.SALES_ORDER: "sales.SalesOrder",
    DocumentType.PURCHASE_ORDER: "purchasing.PurchaseOrder",
    DocumentType.CUSTOMER_INVOICE: "billing.Invoice",
    DocumentType.VENDOR_BILL: "billing.Invoice",
    DocumentType.CUSTOMER_RETURN: "billing.Invoice",
    DocumentType.VENDOR_RETURN: "billing.Invoice",
    DocumentType.PAYMENT: "billing.Payment",
    DocumentType.JOURNAL_ENTRY: "ledger.JournalEntry",
    DocumentType.INVENTORY_COUNT: "inventory.InventoryCount",
    DocumentType.PARTNER_BANK_CHANGE: "partners.PartnerBankChangeRequest",
}


def find_rule(company, doc_type, amount):
    return (ApprovalRule.objects.filter(company=company, document_type=doc_type, active=True, min_amount__lte=amount)
            .order_by("-min_amount").first())


def _eligible_approvers(company_id, level):
    """Everyone in this company whose active role is level >= `level` and can decide approvals
    -- a step's "assignee" is a managerial level, not one named person (see decide()), so every
    eligible approver gets notified and whoever acts first wins."""
    from apps.iam.models import Membership
    users, seen = [], set()
    for m in (Membership.objects.filter(company_id=company_id, active=True)
             .select_related("user").prefetch_related("roles")):
        if m.user_id in seen:
            continue
        for r in m.roles.all():
            if r.active and r.level >= level and "approvals.request.decide" in r.permissions:
                users.append(m.user)
                seen.add(m.user_id)
                break
    return users


def _notify_pending_step(req, company_id):
    notify.send_many(
        _eligible_approvers(company_id, req.required_level),
        f"Approval needed: {req.document_label}",
        body=f"{DocumentType(req.document_type).label} for {req.amount}",
        url=f"/approvals?request={req.pk}", category="approval", company_id=company_id,
    )


def ensure_approved(company, doc_type, doc, amount_base, user) -> bool:
    """True if `doc` may proceed. Otherwise opens an approval request, moves the document to
    'to_approve' and returns False."""
    if doc.state == "approved":
        return True
    if doc.state == "to_approve":
        raise BusinessError("This document is waiting for approval.", "awaiting_approval")
    rule = find_rule(company, doc_type, amount_base)
    if not rule or not rule.approver_levels:
        return True
    ApprovalRequest.objects.filter(company=company, document_type=doc_type, document_id=doc.pk,
                                   state=ApprovalRequest.State.PENDING).update(state=ApprovalRequest.State.CANCELLED)
    req = ApprovalRequest.objects.create(
        company=company, document_type=doc_type, document_id=doc.pk, document_label=str(doc)[:120],
        amount=amount_base, rule=rule, levels=list(rule.approver_levels), requested_by=user, created_by=user)
    doc.state = "to_approve"
    doc.save(update_fields=["state"])
    audit.record("approval_requested", doc, {"request": req.pk, "rule": rule.name, "amount": amount_base})
    _notify_pending_step(req, company.pk)
    return False


def load_document(req):
    model = django_apps.get_model(DOC_MODELS[req.document_type])
    return model.objects.select_for_update().get(pk=req.document_id, company_id=req.company_id)


@transaction.atomic
def decide(req, user, approve: bool, comment=""):
    req = ApprovalRequest.objects.select_for_update().get(pk=req.pk)
    company_id = req.company_id
    if req.state != ApprovalRequest.State.PENDING:
        raise BusinessError("This request is no longer pending.", "not_pending")
    if "approvals.request.decide" not in user.permissions_for(company_id):
        raise BusinessError("You are not allowed to decide approvals.", "forbidden")
    doc = load_document(req)
    preparers = {req.requested_by_id, getattr(doc, "created_by_id", None)}
    if user.pk in preparers:
        raise BusinessError("Segregation of duties: you cannot approve a document you prepared or submitted.",
                            "sod_self_approval")
    if req.decisions.filter(user=user).exists():
        raise BusinessError("You already approved an earlier step of this request.", "sod_double_approval")
    if approve:
        level = user.level_for(company_id)
        if level < req.required_level:
            raise BusinessError(f"This step requires managerial level {req.required_level}; yours is {level}.",
                                "level_too_low")
        limit = user.approval_limit_for(company_id)
        if limit is not None and req.amount > limit:
            raise BusinessError(f"Amount {req.amount} exceeds your approval limit of {limit}.", "over_limit")
    elif not comment:
        raise BusinessError("Please give a reason for rejecting.", "comment_required")
    ApprovalDecision.objects.create(request=req, step=req.current_step, user=user, approved=approve, comment=comment)
    if not approve:
        req.state = ApprovalRequest.State.REJECTED
        doc.state = "draft"
    else:
        req.current_step += 1
        if req.current_step >= len(req.levels):
            req.state = ApprovalRequest.State.APPROVED
            doc.state = "approved"
    req.save()
    doc.save(update_fields=["state"])
    audit.record("approve" if approve else "reject", doc,
                 {"request": req.pk, "step": req.current_step, "comment": comment})
    if not approve:
        notify.send(req.requested_by, f"Rejected: {req.document_label}", body=comment,
                    url=f"/approvals?request={req.pk}", category="approval", company_id=company_id)
    elif req.state == ApprovalRequest.State.APPROVED:
        notify.send(req.requested_by, f"Approved: {req.document_label}",
                    url=f"/approvals?request={req.pk}", category="approval", company_id=company_id)
    else:
        _notify_pending_step(req, company_id)
    return req


def approval_matrix(company):
    """Item #65 -- reconcile the ApprovalRule threshold table against the Role approval-limit
    table for this company, so misconfiguration (a step nobody can ever clear) is surfaced
    instead of silently discovered later when a real document gets stuck.

    For each active rule and each level in its approver_levels, a step is only ever clearable if
    some active role at that company has role.level >= level, holds approvals.request.decide, and
    (if the role's approval_limit is set) that limit is >= the rule's min_amount -- a lower limit
    means the role could never approve an amount that actually triggers this rule, since decide()
    rejects req.amount > limit and req.amount is always >= rule.min_amount by construction of
    find_rule(). Separately flags a rule whose min_amount is 0 (always matches) alongside another
    active rule for the same document_type with a lower level requirement and a HIGHER min_amount
    -- that ordering would mean a bigger document needs a lower level than a smaller one, which is
    almost certainly a data-entry mistake in the rule table rather than intentional design.
    """
    from apps.iam.models import Role

    roles = list(Role.objects.filter(company=company, active=True))
    issues = []
    for rule in ApprovalRule.objects.filter(company=company, active=True).order_by("document_type", "min_amount"):
        rule_issues = []
        for level in rule.approver_levels:
            eligible = [r for r in roles if r.level >= level and "approvals.request.decide" in r.permissions]
            if not eligible:
                rule_issues.append({
                    "code": "no_eligible_role", "level": level,
                    "detail": f"No active role at level {level}+ holds approvals.request.decide."})
                continue
            capped = [r for r in eligible if r.approval_limit is not None]
            if capped and all(r.approval_limit < rule.min_amount for r in capped) and len(capped) == len(eligible):
                best = max(r.approval_limit for r in capped)
                rule_issues.append({
                    "code": "limit_too_low", "level": level,
                    "detail": (f"Every role at level {level}+ that can decide approvals has an approval_limit "
                               f"below this rule's min_amount ({best} < {rule.min_amount}); this step can never "
                               "be cleared for an amount that actually triggers this rule.")})
        if rule_issues:
            issues.append({"rule": rule, "issues": rule_issues})
    # Cross-rule sanity check: within the same document_type, a higher min_amount should never
    # require a lower minimum level than a lower min_amount (bigger documents needing less
    # oversight than smaller ones is almost always a mistake).
    by_doc = {}
    for rule in ApprovalRule.objects.filter(company=company, active=True):
        by_doc.setdefault(rule.document_type, []).append(rule)
    cross_rule_issues = []
    for doc_type, rules in by_doc.items():
        rules = sorted(rules, key=lambda r: r.min_amount)
        for lo, hi in zip(rules, rules[1:]):
            lo_min_level = min(lo.approver_levels) if lo.approver_levels else 0
            hi_min_level = min(hi.approver_levels) if hi.approver_levels else 0
            if hi.min_amount > lo.min_amount and hi_min_level < lo_min_level:
                cross_rule_issues.append({
                    "document_type": doc_type, "lower_rule": lo, "higher_rule": hi,
                    "detail": (f"'{hi.name}' (min_amount={hi.min_amount}) requires a lower level "
                               f"({hi_min_level}) than '{lo.name}' (min_amount={lo.min_amount}, "
                               f"level {lo_min_level}) for the same document type.")})
    return {"rule_issues": issues, "cross_rule_issues": cross_rule_issues}
