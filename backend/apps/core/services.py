"""Company provisioning workflow: draft -> pending_approval -> active (or rejected).

A Controller configures a new company (chart of accounts, journals, taxes, roles, modules,
initial team) while it's a draft, then submits it; the CEO gives final approval before it
becomes usable. `Company.active` is the pre-existing gate that resolve_company()/
User.membership() already filter on, so a draft/pending company is automatically unusable by
every existing endpoint with no change to access resolution -- only approve() ever sets it True.
"""
from decimal import Decimal

from django.utils import timezone

from apps.core.exceptions import BusinessError
from apps.core.management.commands.setup_company import ensure_currencies, setup
from apps.core.modules import DEFAULT_MODULES_BY_TYPE, MODULE_REGISTRY

from . import audit
from .models import Company, CompanyModule, CompanyState, Currency


def create_draft_company(user, *, name, code, company_type, country="", currency=None, vat=None,
                         name_ar="", fy_start_month=1, year=None):
    if Company.objects.filter(code=code).exists():
        raise BusinessError("A company with this code already exists.", "duplicate_code")
    ensure_currencies()
    egypt = country.upper() == "EG"
    currency = currency or ("EGP" if egypt else "USD")
    if not Currency.objects.filter(code=currency).exists():
        raise BusinessError(f"Unknown currency {currency}.", "unknown_currency")
    company = Company.objects.create(
        name=name, name_ar=name_ar, legal_name=name, code=code, base_currency_id=currency,
        country=country.upper(), fiscal_year_start_month=fy_start_month, active=False,
        state=CompanyState.DRAFT, company_type=company_type, created_by=user,
    )
    vat = Decimal(str(vat)) if vat is not None else Decimal("14" if egypt else "15")
    setup(company, vat, year or timezone.localdate().year, egypt=egypt)
    set_modules(company, DEFAULT_MODULES_BY_TYPE.get(company_type, list(MODULE_REGISTRY)))
    audit.record("company_draft_created", company, company_id=company.pk)
    return company


def set_modules(company, module_codes):
    unknown = set(module_codes) - set(MODULE_REGISTRY)
    if unknown:
        raise BusinessError(f"Unknown module(s): {', '.join(sorted(unknown))}.", "unknown_module")
    wanted = set(module_codes)
    existing = {m.module_code: m for m in CompanyModule.objects.filter(company=company)}
    for code in wanted:
        if code in existing:
            if not existing[code].enabled:
                existing[code].enabled = True
                existing[code].save(update_fields=["enabled"])
        else:
            CompanyModule.objects.create(company=company, module_code=code, enabled=True)
    for code, m in existing.items():
        if code not in wanted and m.enabled:
            m.enabled = False
            m.save(update_fields=["enabled"])


def _require_state(company, *allowed):
    if company.state not in allowed:
        raise BusinessError(f"Company is '{company.state}'; expected one of {', '.join(allowed)}.",
                            "wrong_state")


def submit_company(company, user):
    _require_state(company, CompanyState.DRAFT, CompanyState.REJECTED)
    company.state = CompanyState.PENDING_APPROVAL
    company.submitted_by = user
    company.submitted_at = timezone.now()
    company.rejected_reason = ""
    company.save(update_fields=["state", "submitted_by", "submitted_at", "rejected_reason"])
    audit.record("company_submitted", company, company_id=company.pk)
    return company


def approve_company(company, user):
    _require_state(company, CompanyState.PENDING_APPROVAL)
    company.state = CompanyState.ACTIVE
    company.active = True
    company.approved_by = user
    company.approved_at = timezone.now()
    company.save(update_fields=["state", "active", "approved_by", "approved_at"])
    audit.record("company_approved", company, company_id=company.pk)
    return company


def reject_company(company, user, reason):
    _require_state(company, CompanyState.PENDING_APPROVAL)
    company.state = CompanyState.REJECTED
    company.rejected_reason = reason
    company.approved_by = user
    company.approved_at = timezone.now()
    company.save(update_fields=["state", "rejected_reason", "approved_by", "approved_at"])
    audit.record("company_rejected", company, {"reason": reason}, company_id=company.pk)
    return company
