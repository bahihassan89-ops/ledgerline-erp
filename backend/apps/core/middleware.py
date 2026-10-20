import uuid

from . import context


def client_ip(request):
    # Only trust X-Forwarded-For when explicitly behind a configured proxy.
    from django.conf import settings
    if getattr(settings, "ERP_TRUST_PROXY", False):
        fwd = request.META.get("HTTP_X_FORWARDED_FOR")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.META.get("HTTP_X_REQUEST_ID") or uuid.uuid4().hex
        rid = "".join(c for c in rid if c.isalnum() or c in "-_")[:64]
        token = context.set_context(context.RequestContext(
            ip=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
            request_id=rid,
        ))
        try:
            response = self.get_response(request)
        finally:
            context.reset(token)
        response["X-Request-ID"] = rid
        return response


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy",
                            "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'")
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.path.startswith("/api/"):
            response.setdefault("Cache-Control", "no-store")
        return response
