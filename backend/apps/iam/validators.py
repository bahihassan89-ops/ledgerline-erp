import re

from django.core.exceptions import ValidationError


class ComplexityValidator:
    """Require upper, lower, digit and symbol."""

    def validate(self, password, user=None):
        checks = [r"[A-Z]", r"[a-z]", r"\d", r"[^A-Za-z0-9]"]
        if not all(re.search(c, password) for c in checks):
            raise ValidationError(
                "Password must contain an uppercase letter, a lowercase letter, a digit and a symbol.",
                code="password_too_simple",
            )

    def get_help_text(self):
        return "Your password must contain upper and lower case letters, a digit and a symbol."
