from django.contrib.auth import password_validation
from rest_framework import serializers

from .models import DailyActivitySummary, GlobalRole, Membership, PresenceEvent, Role, User, UserPresence
from .permissions_catalog import CATALOG, GLOBAL_CATALOG
from .presence import today_minutes


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


class GlobalRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = GlobalRole
        fields = ["id", "name", "description", "level", "permissions", "active"]

    def validate_permissions(self, value):
        unknown = set(value) - set(GLOBAL_CATALOG)
        if unknown:
            raise serializers.ValidationError(f"Unknown global permissions: {', '.join(sorted(unknown))}")
        return sorted(set(value))

    def validate_level(self, value):
        if value > 5:
            raise serializers.ValidationError("Level must be between 0 and 5.")
        return value

    def validate(self, attrs):
        req = self.context["request"]
        if not req.user.is_superuser:
            mine = req.user.global_permissions()
            extra = set(attrs.get("permissions", [])) - mine
            if extra:
                raise serializers.ValidationError(
                    {"permissions": f"You cannot grant global permissions you don't have: {', '.join(sorted(extra))}"})
        return attrs


class UserPresenceSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    full_name = serializers.SerializerMethodField()
    company_name = serializers.CharField(source="company.name", read_only=True, default="")
    status = serializers.SerializerMethodField()
    active_today_minutes = serializers.SerializerMethodField()

    class Meta:
        model = UserPresence
        fields = ["user", "username", "full_name", "company", "company_name", "module_code", "section",
                  "status", "last_heartbeat", "active_today_minutes"]

    def get_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

    def get_status(self, obj):
        return obj.status()

    def get_active_today_minutes(self, obj):
        return str(today_minutes(obj.user, obj.company))


class DailyActivitySummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyActivitySummary
        fields = ["id", "company", "date", "active_minutes"]


class PresenceEventSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True, default="")

    class Meta:
        model = PresenceEvent
        fields = ["id", "at", "user", "username", "company", "company_name", "module_code", "section", "event"]


class UserSerializer(serializers.ModelSerializer):
    """Users as seen from the current company (roles are per company)."""
    roles = serializers.SerializerMethodField()
    role_ids = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)
    global_roles = serializers.SerializerMethodField()
    global_role_ids = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)
    locked = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "is_active", "mfa_enabled", "last_login",
                  "roles", "role_ids", "global_roles", "global_role_ids", "password", "locked",
                  "must_change_password"]
        read_only_fields = ["mfa_enabled", "last_login"]

    def get_global_roles(self, obj):
        return [{"id": r.pk, "name": r.name, "level": r.level} for r in obj.global_roles.filter(active=True)]

    def validate_global_role_ids(self, ids):
        req = self.context["request"]
        # Only the CEO (the one role bundle holding "company.activate") or a superuser may grant
        # cross-company authority -- GlobalRole assignment is strictly more sensitive than
        # assigning a per-company Role, so it does not fall under membership.grant_any_company.
        if not req.user.is_superuser and not req.user.has_global_perm("company.activate"):
            raise serializers.ValidationError("Only the CEO can assign global (cross-company) roles.")
        roles = list(GlobalRole.objects.filter(pk__in=ids))
        if len(roles) != len(set(ids)):
            raise serializers.ValidationError("Unknown global role.")
        return roles

    def _apply_global_roles(self, user, roles):
        if roles is not None:
            user.global_roles.set(roles)

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
        # A Central Accounting Manager (global "membership.grant_any_company") sets up roles in
        # companies they hold no personal Membership in -- same anti-escalation bypass as superuser.
        if not req.user.is_superuser and not req.user.has_global_perm("membership.grant_any_company"):
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
        global_roles = validated.pop("global_role_ids", None)
        pwd = validated.pop("password")
        user = User(**validated)
        user.set_password(pwd)
        user.must_change_password = True
        user.save()
        self._apply_roles(user, roles)
        self._apply_global_roles(user, global_roles)
        return user

    def update(self, instance, validated):
        roles = validated.pop("role_ids", None)
        global_roles = validated.pop("global_role_ids", None)
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
        self._apply_global_roles(instance, global_roles)
        return instance
