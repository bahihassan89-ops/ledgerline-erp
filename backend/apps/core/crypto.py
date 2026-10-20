"""Symmetric encryption for secrets stored in the DB (MFA seeds, webhook secrets)."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _fernet():
    key = hashlib.sha256(settings.FIELD_ENCRYPTION_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Cannot decrypt value (wrong key?)") from e


class EncryptedTextField(models.TextField):
    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        return decrypt(value)

    def get_prep_value(self, value):
        if value in (None, ""):
            return value
        return encrypt(str(value))
