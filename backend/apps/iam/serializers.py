from django.contrib.auth import password_validation
from rest_framework import serializers

from .models import Membership, Role, User
from .permissions_catalog import CATALOG


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name", "description", "level", "permissions", "approval_limit", "active"]

    def validate_permissions(self, value):
        unknown = set(value) - set(CATALOG)
        if unknown:
            raise serializers.ValidationError(f"Unknown permissions: {', '.join(sorted(unknown))}")
        return sorted(set(value))

    def validate_level(self, value):
        if value > 5:
            raise serializers.ValidationError("Level must be between 0 and 5.")
        return value

    def validate(self, attrs):
        # Anti-escalation: you cannot grant permissions or a level you do not hold yourself.
        req = self.context["request"]
        company = self.context["company"]
        if not req.user.is_superuser:
            mine = req.user.permissions_for(company.pk)
            extra = set(attrs.get("permissions", [])) - mine
            if extra:
                raise serializers.ValidationError(
                    {"permissions": f"You cannot grant permissions you don't have: {', '.join(sorted(extra))}"})
        return attrs


class UserSerializer(serializers.ModelSerializer):
    """Users as seen from the current company (roles are per company)."""
    roles = serializers.SerializerMethodField()
    role_ids = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)
    locked = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "is_active", "mfa_enabled", "last_login",
                  "roles", "role_ids", "password", "locked", "must_change_password"]
        read_only_fields = ["mfa_enabled", "last_login"]

    def get_roles(self, obj):
        m = obj.membership(self.context["company"].pk)
        return [{"id": r.pk, "name": r.name, "level": r.level} for r in m.roles.all()] if m else []

    def get_locked(self, obj):
        return obj.is_locked()

    def validate_role_ids(self, ids):
        company = self.context["company"]
        roles = list(Role.objects.filter(company=company, pk__in=ids))
        if len(roles) != len(set(ids)):
            raise serializers.ValidationError("Unknown role for this company.")
        req = self.context["request"]
        if not req.user.is_superuser:
            mine = req.user.permissions_for(company.pk)
            my_level = req.user.level_for(company.pk)
            for r in roles:
                if not set(r.permissions) <= mine:
                    raise serializers.ValidationError(f"You cannot assign role '{r.name}': it grants rights you lack.")
                if r.level > my_level:
                    raise serializers.ValidationError(f"You cannot assign role '{r.name}': its level is above yours.")
            if self.instance and self.instance.pk == req.user.pk:
                raise serializers.ValidationError("You cannot change your own roles.")
        return roles

    def validate(self, attrs):
        pwd = attrs.get("password")
        if pwd:
            password_validation.validate_password(pwd, self.instance or User(username=attrs.get("username", ""),
                                                                              email=attrs.get("email", "")))
        elif not self.instance:
            raise serializers.ValidationError({"password": "An initial password is required."})
        return attrs

    def _apply_roles(self, user, roles):
        company = self.context["company"]
        m, _ = Membership.objects.get_or_create(user=user, company=company)
        if roles is not None:
            m.roles.set(roles)

    def create(self, validated):
        roles = validated.pop("role_ids", [])
        pwd = validated.pop("password")
        user = User(**validated)
        user.set_password(pwd)
        user.must_change_password = True
        user.save()
        self._apply_roles(user, roles)
        return user

    def update(self, instance, validated):
        roles = validated.pop("role_ids", None)
        pwd = validated.pop("password", None)
        req = self.context["request"]
        if instance.is_superuser and not req.user.is_superuser:
            raise serializers.ValidationError("Only a superuser can modify a superuser.")
        for k, v in validated.items():
            setattr(instance, k, v)
        if pwd:
            instance.set_password(pwd)
            instance.must_change_password = True
        instance.save()
        self._apply_roles(instance, roles)
        return instance
