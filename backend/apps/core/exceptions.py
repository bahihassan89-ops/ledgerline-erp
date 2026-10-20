import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler

log = logging.getLogger("erp")


class BusinessError(Exception):
    """A rule of the business was violated (unbalanced entry, closed period, ...)."""

    def __init__(self, message, code="business_rule", details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


def exception_handler(exc, ctx):
    if isinstance(exc, BusinessError):
        return Response({"error": exc.code, "detail": exc.message, "details": exc.details},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "error_dict") else {"non_field_errors": exc.messages}
        return Response({"error": "validation", "detail": detail}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, DjangoPermissionDenied):
        return Response({"error": "forbidden", "detail": str(exc) or "Permission denied."},
                        status=status.HTTP_403_FORBIDDEN)
    response = drf_handler(exc, ctx)
    if response is None:
        log.exception("Unhandled API error")
        # Never leak internals to clients.
        return Response({"error": "server_error", "detail": "An unexpected error occurred."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return response
