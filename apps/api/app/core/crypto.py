"""Enkripsi rahasia pihak ketiga (mis. API key kurir) sebelum disimpan di database."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.errors import AppError


def _fernet() -> Fernet:
    s = get_settings()
    key = s.app_encryption_key or base64.urlsafe_b64encode(hashlib.sha256(("enc:" + s.jwt_secret).encode()).digest()).decode()
    return Fernet(key.encode())


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise AppError(500, "SECRET_UNREADABLE", "Kredensial tersimpan tidak bisa dibuka; atur ulang akun kurir") from None
