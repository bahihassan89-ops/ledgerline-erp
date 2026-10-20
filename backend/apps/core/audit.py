"""Audit trail helpers. Use `record()` for explicit business events; model changes on
audited models are captured automatically by signals in `signals.py`."""
from datetime import date, datetime
from decimal import Decimal

from . import context


def _safe(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_safe(x) for x in v]
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def record(action, obj=None, changes=None, company_id=None, object_type=None, object_id=None, repr_=None):
    from .models import AuditLog
    ctx = context.get()
    user = ctx.user if getattr(ctx.user, "is_authenticated", False) else None
    if obj is not None:
        object_type = object_type or f"{obj._meta.app_label}.{obj._meta.model_name}"
        object_id = object_id or str(obj.pk)
        repr_ = repr_ or str(obj)[:255]
        company_id = company_id or getattr(obj, "company_id", None)
    return AuditLog.objects.create(
        company_id=company_id or ctx.company_id,
        user=user,
        actor_label=ctx.actor_label or (user.get_username() if user else "system"),
        action=action,
        object_type=object_type or "",
        object_id=object_id or "",
        object_repr=repr_ or "",
        changes=_safe(changes or {}),
        ip=ctx.ip,
        user_agent=ctx.user_agent,
        request_id=ctx.request_id,
    )
