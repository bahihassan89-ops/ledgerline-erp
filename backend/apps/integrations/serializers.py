import ipaddress
import socket
from urllib.parse import urlparse

from django.conf import settings
from rest_framework import serializers

from apps.core.serializers_base import CompanyScopedSerializer
from apps.iam.permissions_catalog import CATALOG

from .models import WEBHOOK_EVENTS, ApiKey, Webhook, WebhookDelivery


def resolve_validated_ip(url):
    """SSRF guard: webhooks may only target public HTTPS hosts. Returns the validated IP (or None
    when running in the DEBUG-only private-webhooks escape hatch) so the caller can connect
    directly to that address instead of re-resolving DNS — a second lookup between validation and
    connection is a DNS-rebinding TOCTOU window (the attacker's DNS answers public at check-time,
    private at connect-time)."""
    u = urlparse(url)
    if u.scheme != "https" and not (settings.DEBUG and u.scheme == "http"):
        raise serializers.ValidationError("Webhook URLs must use HTTPS.")
    if not u.hostname:
        raise serializers.ValidationError("Invalid URL.")
    if settings.DEBUG and getattr(settings, "ERP_ALLOW_PRIVATE_WEBHOOKS", False):
        return None
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443)
    except socket.gaierror:
        raise serializers.ValidationError("Host does not resolve.")
    validated_ip = None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise serializers.ValidationError("Webhook URL resolves to a private/internal address.")
        validated_ip = validated_ip or info[4][0]
    return validated_ip


def assert_public_url(url):
    """Validation-only form of resolve_validated_ip(), for serializer field checks."""
    resolve_validated_ip(url)


class ApiKeySerializer(CompanyScopedSerializer):
    owner_name = serializers.CharField(source="owner.username", read_only=True)

    class Meta:
        model = ApiKey
        fields = ["id", "name", "prefix", "owner", "owner_name", "scopes", "allowed_ips", "expires_at",
                  "last_used_at", "active", "created_at"]
        read_only_fields = ["prefix", "owner", "last_used_at", "created_at"]

    def validate_scopes(self, value):
        unknown = set(value) - set(CATALOG)
        if unknown:
            raise serializers.ValidationError(f"Unknown scopes: {', '.join(sorted(unknown))}")
        return sorted(set(value))

    def validate_allowed_ips(self, value):
        for ip in value:
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                raise serializers.ValidationError(f"Invalid IP: {ip}")
        return value


class WebhookSerializer(CompanyScopedSerializer):
    class Meta:
        model = Webhook
        fields = ["id", "name", "url", "events", "active", "created_at"]

    def validate_url(self, value):
        assert_public_url(value)
        return value

    def validate_events(self, value):
        unknown = set(value) - set(WEBHOOK_EVENTS)
        if unknown:
            raise serializers.ValidationError(f"Unknown events: {', '.join(sorted(unknown))}. "
                                              f"Available: {', '.join(WEBHOOK_EVENTS)}")
        return value


class WebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDelivery
        fields = ["id", "webhook", "event", "event_id", "status", "attempts", "last_status_code", "last_error",
                  "created_at", "next_attempt_at"]
