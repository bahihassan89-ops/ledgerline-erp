from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from .exceptions import BusinessError

EDITABLE_STATES = ("draft",)


class CompanyScopedSerializer(serializers.ModelSerializer):
    """* Every related object a client references must belong to the caller's company
         (blocks cross-company data access through foreign keys).
       * Runs the model's clean() so business validation lives in one place."""
    run_model_clean = False

    def validate(self, attrs):
        company = self.context.get("company")
        if company is not None:
            for name, value in attrs.items():
                check = value if isinstance(value, (list, tuple)) else [value]
                for v in check:
                    cid = getattr(v, "company_id", None)
                    if cid is not None and cid != company.pk:
                        raise serializers.ValidationError({name: "Object belongs to another company."})
        attrs = super().validate(attrs)
        if self.run_model_clean:
            inst = self.instance or self.Meta.model()
            backup = {}
            for k, v in attrs.items():
                if isinstance(v, (list, tuple)) or k in getattr(self.Meta, "m2m", ()):
                    continue
                backup[k] = getattr(inst, k, None)
                setattr(inst, k, v)
            if company is not None and hasattr(inst, "company_id") and not inst.company_id:
                inst.company = company
            try:
                inst.clean()
            except DjangoValidationError as e:
                raise serializers.ValidationError(e.message_dict if hasattr(e, "error_dict") else e.messages)
            finally:
                if self.instance:
                    for k, v in backup.items():
                        setattr(inst, k, v)
        return attrs


class DocumentSerializer(CompanyScopedSerializer):
    """A document with nested `lines`. Lines are replaced wholesale on update, and only while the
    document is in draft."""
    line_model = None
    line_fk = None

    def _write_lines(self, doc, lines):
        doc.lines.all().delete()
        for i, data in enumerate(lines):
            data.pop("id", None)
            self.line_model.objects.create(**{self.line_fk: doc}, sequence=i, **data)

    @transaction.atomic
    def create(self, validated):
        lines = validated.pop("lines", [])
        doc = super().create(validated)
        self._write_lines(doc, lines)
        self.after_lines(doc)
        return doc

    @transaction.atomic
    def update(self, instance, validated):
        if getattr(instance, "state", "draft") not in EDITABLE_STATES:
            raise BusinessError(f"Only draft documents can be edited (this one is '{instance.state}').", "not_draft")
        lines = validated.pop("lines", None)
        doc = super().update(instance, validated)
        if lines is not None:
            self._write_lines(doc, lines)
        self.after_lines(doc)
        return doc

    def after_lines(self, doc):
        pass
