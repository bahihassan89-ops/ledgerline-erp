"""Authentication: password → (MFA) → short-lived JWT access + rotating refresh token."""
import hashlib
from datetime import timedelta

import pyotp
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.hashers import make_password
from django.core import signing
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core import audit, context

from .models import User

MFA_SALT = "erp.mfa"
MFA_TTL = 300


def _pwd_fingerprint(user):
    return hashlib.sha256(user.password.encode()).hexdigest()[:16]


def _mfa_token(user, purpose):
    return signing.dumps({"uid": user.pk, "p": purpose, "f": _pwd_fingerprint(user)}, salt=MFA_SALT)


def _read_mfa_token(token, purposes):
    try:
        data = signing.loads(token, salt=MFA_SALT, max_age=MFA_TTL)
    except signing.BadSignature:
        return None
    if data.get("p") not in purposes:
        return None
    user = User.objects.filter(pk=data.get("uid"), is_active=True).first()
    if not user or _pwd_fingerprint(user) != data.get("f") or user.is_locked():
        return None
    return user


def _tokens(user, request):
    refresh = RefreshToken.for_user(user)
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    _ctx_user(user)
    audit.record("login", user, {"method": "password+mfa" if user.mfa_enabled else "password"})
    expired = bool(user.password_changed_at and
                   user.password_changed_at < timezone.now() - timedelta(days=settings.ERP_PASSWORD_MAX_AGE_DAYS))
    return {"access": str(refresh.access_token), "refresh": str(refresh),
            "must_change_password": user.must_change_password or expired}


def _ctx_user(user):
    ctx = context.get()
    ctx.user = user
    ctx.actor_label = user.get_username()


def mfa_required(user):
    if user.is_superuser:
        return True
    lvl = settings.ERP_MFA_REQUIRED_LEVEL
    return user.memberships.filter(active=True, roles__level__gte=lvl, roles__active=True).exists()


def _verify_totp(user, code):
    """Valid TOTP code, accepting ±1 step for clock drift, never the same step twice (replay)."""
    if not user.mfa_secret or not code or not str(code).isdigit():
        return False
    totp = pyotp.TOTP(user.mfa_secret)
    now = timezone.now()
    for drift in (0, -1, 1):
        t = now + timedelta(seconds=30 * drift)
        counter = totp.timecode(t)
        if counter <= user.mfa_last_counter:
            continue
        if pyotp.utils.strings_equal(totp.at(t), str(code)):
            user.mfa_last_counter = counter
            user.save(update_fields=["mfa_last_counter"])
            return True
    return False


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=256, trim_whitespace=False)


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        ident, pwd = s.validated_data["username"], s.validated_data["password"]
        user = User.objects.filter(Q(username__iexact=ident) | Q(email__iexact=ident)).first()
        generic = Response({"error": "invalid_credentials", "detail": "Invalid username or password."},
                           status=status.HTTP_401_UNAUTHORIZED)
        if user is None:
            make_password(pwd)  # equalise timing so usernames cannot be enumerated
            audit.record("login_failed", object_type="iam.user", repr_=ident[:100], changes={"reason": "unknown"})
            return generic
        if user.is_locked():
            audit.record("login_blocked", user, {"locked_until": user.locked_until})
            return Response({"error": "locked", "detail": "Account temporarily locked after repeated failures. "
                             "Try again later or contact an administrator."}, status=status.HTTP_423_LOCKED)
        if not user.is_active or not user.check_password(pwd):
            user.failed_login_count += 1
            if user.failed_login_count >= settings.ERP_MAX_FAILED_LOGINS:
                user.locked_until = timezone.now() + timedelta(minutes=settings.ERP_LOCKOUT_MINUTES)
                user.failed_login_count = 0
            user.save(update_fields=["failed_login_count", "locked_until"])
            audit.record("login_failed", user, {"reason": "bad_password" if user.is_active else "inactive"})
            return generic
        user.failed_login_count = 0
        user.save(update_fields=["failed_login_count"])
        if user.mfa_enabled:
            return Response({"mfa_required": True, "mfa_token": _mfa_token(user, "verify")})
        if mfa_required(user):
            return Response({"mfa_setup_required": True, "mfa_token": _mfa_token(user, "setup")})
        return Response(_tokens(user, request))


class MfaVerifyView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        user = _read_mfa_token(request.data.get("mfa_token", ""), {"verify"})
        if not user:
            return Response({"error": "invalid_token", "detail": "Session expired. Log in again."}, status=401)
        if not _verify_totp(user, request.data.get("code")):
            user.failed_login_count += 1
            if user.failed_login_count >= settings.ERP_MAX_FAILED_LOGINS:
                user.locked_until = timezone.now() + timedelta(minutes=settings.ERP_LOCKOUT_MINUTES)
                user.failed_login_count = 0
            user.save(update_fields=["failed_login_count", "locked_until"])
            audit.record("mfa_failed", user)
            return Response({"error": "invalid_code", "detail": "Invalid authentication code."}, status=401)
        return Response(_tokens(user, request))


def _setup_user(request):
    if request.user and request.user.is_authenticated:
        return request.user, False
    return _read_mfa_token(request.data.get("mfa_token", ""), {"setup", "verify"}), True


class MfaSetupView(APIView):
    """Start enrolment: returns a fresh secret + otpauth:// URI for an authenticator app."""
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        user, _ = _setup_user(request)
        if not user:
            return Response({"error": "invalid_token", "detail": "Session expired. Log in again."}, status=401)
        if user.mfa_enabled:
            return Response({"error": "already_enabled", "detail": "MFA is already enabled."}, status=400)
        secret = pyotp.random_base32()
        user.mfa_secret = secret
        user.save(update_fields=["mfa_secret"])
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email or user.username, issuer_name="Ledgerline ERP")
        return Response({"secret": secret, "otpauth_uri": uri})


class MfaConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        user, via_token = _setup_user(request)
        if not user:
            return Response({"error": "invalid_token", "detail": "Session expired. Log in again."}, status=401)
        if user.is_locked():
            return Response({"error": "locked", "detail": "Account temporarily locked after repeated failures. "
                             "Try again later or contact an administrator."}, status=status.HTTP_423_LOCKED)
        if not _verify_totp(user, request.data.get("code")):
            # Same brute-force protection as MfaVerifyView — otherwise MFA setup has no attempt limit.
            user.failed_login_count += 1
            if user.failed_login_count >= settings.ERP_MAX_FAILED_LOGINS:
                user.locked_until = timezone.now() + timedelta(minutes=settings.ERP_LOCKOUT_MINUTES)
                user.failed_login_count = 0
            user.save(update_fields=["failed_login_count", "locked_until"])
            audit.record("mfa_setup_failed", user)
            return Response({"error": "invalid_code", "detail": "Invalid authentication code."}, status=400)
        user.mfa_enabled = True
        user.save(update_fields=["mfa_enabled"])
        _ctx_user(user)
        audit.record("mfa_enabled", user)
        return Response(_tokens(user, request) if via_token else {"mfa_enabled": True})


class RefreshView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    # Every page load exchanges the refresh token, so it needs its own (higher) limit than anonymous calls.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "refresh"

    def post(self, request):
        try:
            old = RefreshToken(request.data.get("refresh", ""))
            user = User.objects.get(pk=old["user_id"], is_active=True)
            if user.is_locked():
                raise TokenError("locked")
            old.blacklist()
            new = RefreshToken.for_user(user)
        except (TokenError, User.DoesNotExist, KeyError):
            return Response({"error": "invalid_token", "detail": "Session expired."}, status=401)
        return Response({"access": str(new.access_token), "refresh": str(new)})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            RefreshToken(request.data.get("refresh", "")).blacklist()
        except TokenError:
            pass
        _ctx_user(request.user)
        audit.record("logout", request.user)
        return Response(status=204)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        old, new = request.data.get("old_password", ""), request.data.get("new_password", "")
        if not user.check_password(old):
            return Response({"error": "invalid_password", "detail": "Current password is incorrect."}, status=400)
        if old == new:
            return Response({"error": "same_password", "detail": "Choose a different password."}, status=400)
        password_validation.validate_password(new, user)
        user.set_password(new)
        user.must_change_password = False
        user.save()
        _ctx_user(user)
        audit.record("password_changed", user)
        return Response({"ok": True})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        u = request.user
        companies = []
        for m in u.memberships.filter(active=True, company__active=True).select_related("company").prefetch_related("roles"):
            companies.append({
                "id": m.company.pk, "name": m.company.name, "code": m.company.code,
                "base_currency": m.company.base_currency_id, "is_default": m.is_default,
                "roles": [{"id": r.pk, "name": r.name, "level": r.level} for r in m.roles.all() if r.active],
                "level": u.level_for(m.company.pk),
                "approval_limit": (lambda x: None if x is None else str(x))(u.approval_limit_for(m.company.pk)),
                "permissions": sorted(u.permissions_for(m.company.pk)),
            })
        if u.is_superuser and not companies:
            from apps.core.models import Company
            from .permissions_catalog import CATALOG
            companies = [{"id": c.pk, "name": c.name, "code": c.code, "base_currency": c.base_currency_id,
                          "is_default": False, "roles": [], "level": 5, "approval_limit": None,
                          "permissions": sorted(CATALOG)} for c in Company.objects.filter(active=True)]
        return Response({"id": u.pk, "username": u.username, "email": u.email,
                         "full_name": u.get_full_name() or u.username, "mfa_enabled": u.mfa_enabled,
                         "is_superuser": u.is_superuser, "must_change_password": u.must_change_password,
                         "trial": settings.ERP_TRIAL, "companies": companies})
