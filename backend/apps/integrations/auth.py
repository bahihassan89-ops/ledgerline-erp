import hmac

from django.utils import timezone
from rest_framework import authentication, exceptions

from apps.core.middleware import client_ip


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """`Authorization: Api-Key <prefix>.<secret>`"""
    keyword = "Api-Key"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith(self.keyword + " "):
            return None
        from .models import ApiKey
        raw = header[len(self.keyword) + 1:].strip()
        prefix, _, secret = raw.partition(".")
        if not prefix or not secret:
            raise exceptions.AuthenticationFailed("Malformed API key.")
        key = ApiKey.objects.select_related("owner", "company").filter(prefix=prefix).first()
        # constant-time comparison; same error for every failure to avoid oracle
        if not key or not hmac.compare_digest(key.key_hash, ApiKey.hash_secret(secret)) or not key.is_valid() \
                or not key.owner.is_active:
            raise exceptions.AuthenticationFailed("Invalid API key.")
        if key.allowed_ips and client_ip(request) not in key.allowed_ips:
            raise exceptions.AuthenticationFailed("API key not allowed from this address.")
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        request.api_key = key
        return key.owner, key

    def authenticate_header(self, request):
        return self.keyword
