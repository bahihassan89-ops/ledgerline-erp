"""Automatic field-level change capture for audited models."""
from django.db.models.signals import post_delete, post_save, pre_save

from . import audit

SKIP_FIELDS = {"updated_at", "created_at", "password", "mfa_secret", "secret", "key_hash"}
_AUDITED = set()


def audited(model):
    """Class decorator: track create/update/delete of this model in the audit log."""
    _AUDITED.add(model)
    pre_save.connect(_pre_save, sender=model, weak=False)
    post_save.connect(_post_save, sender=model, weak=False)
    post_delete.connect(_post_delete, sender=model, weak=False)
    return model


def _snapshot(instance):
    data = {}
    for f in instance._meta.concrete_fields:
        if f.name in SKIP_FIELDS:
            continue
        data[f.attname] = getattr(instance, f.attname)
    return data


def _pre_save(sender, instance, **kw):
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).first()
        instance._audit_old = _snapshot(old) if old else None
    else:
        instance._audit_old = None


def _post_save(sender, instance, created, **kw):
    new = _snapshot(instance)
    if created:
        audit.record("create", instance, {"new": new})
        return
    old = getattr(instance, "_audit_old", None) or {}
    diff = {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}
    if diff:
        audit.record("update", instance, diff)


def _post_delete(sender, instance, **kw):
    audit.record("delete", instance, {"old": _snapshot(instance)})
