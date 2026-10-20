"""Amount- and level-based approval workflow with segregation of duties."""
from django.apps import apps as django_apps
from django.db import transaction

from apps.core import audit
from apps.core.exceptions import BusinessError

from .models import ApprovalDecision, ApprovalRequest, ApprovalRule, DocumentType

DOC_MODELS = {
    DocumentType.SALES_ORDER: "sales.SalesOrder",
    DocumentType.PURCHASE_ORDER: "purchasing.PurchaseOrder",
    DocumentType.CUSTOMER_INVOICE: "billing.Invoice",
    DocumentType.VENDOR_BILL: "billing.Invoice",
    DocumentType.PAYMENT: "billing.Payment",
    DocumentType.JOURNAL_ENTRY: "ledger.JournalEntry",
}


def find_rule(company, doc_type, amount):
    return (ApprovalRule.objects.filter(company=company, document_type=doc_type, active=True, min_amount__lte=amount)
            .order_by("-min_amount").first())


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
    return req
