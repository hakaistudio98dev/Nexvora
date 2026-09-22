import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_ph = PasswordHasher()  # Argon2id
_DUMMY_HASH = _ph.hash("timing-equalizer-not-a-real-password")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Selalu menjalankan verifikasi (juga saat user tidak ada) agar waktu respons setara."""
    try:
        ok = _ph.verify(password_hash or _DUMMY_HASH, password)
        return ok and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _ph.check_needs_rehash(password_hash)


def validate_password_strength(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password minimal 12 karakter")
    classes = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if classes < 3:
        raise ValueError("Password harus memuat minimal 3 dari: huruf kecil, huruf besar, angka, simbol")
    return password


def create_access_token(*, user_id: UUID, tenant_id: UUID, roles: list[str], token_version: int,
                        superadmin: bool) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    ttl = s.access_token_ttl_minutes * 60
    payload = {
        "sub": str(user_id), "tid": str(tenant_id), "roles": roles,
        "tv": token_version, "sa": superadmin,
        "iat": now, "nbf": now, "exp": now + timedelta(seconds=ttl),
        "iss": s.jwt_issuer, "aud": s.jwt_audience, "jti": uuid4().hex,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256"), ttl


def decode_access_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(
        token, s.jwt_secret, algorithms=["HS256"],
        audience=s.jwt_audience, issuer=s.jwt_issuer,
        options={"require": ["exp", "sub", "tid", "iss", "aud"]},
    )


def new_refresh_token(tenant_id: UUID) -> tuple[str, str]:
    """(token_plain, sha256_hash). Format <tenant_id>.<random>: prefix tenant dipakai
    untuk memasang konteks RLS sebelum lookup. DB hanya menyimpan hash."""
    raw = f"{tenant_id}.{secrets.token_urlsafe(48)}"
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
