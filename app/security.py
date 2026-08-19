from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from app.config import settings


def hash_password(password: str) -> str:
    # bcrypt ignores/rejects bytes past 72; truncate explicitly so long
    # passphrases don't raise instead of just losing their tail entropy.
    raw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    raw = password.encode("utf-8")[:72]
    return bcrypt.checkpw(raw, password_hash.encode("utf-8"))


def create_access_token(subject: str, extra_claims: Optional[dict] = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def token_for_user(user) -> str:
    """The only way a login token should be minted.

    Kept in one place so the token_version claim cannot be forgotten at a call
    site -- without it, a password reset would not invalidate that session.
    """
    return create_access_token(
        subject=str(user.id),
        extra_claims={"role": user.role.value, "tv": int(user.token_version or 0)},
    )


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
