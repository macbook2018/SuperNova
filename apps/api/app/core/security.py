from datetime import UTC, datetime, timedelta
from hashlib import sha256
import secrets
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return _password_hasher.verify(encoded_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def hash_secret(secret: str) -> str:
    return sha256(secret.encode("utf-8")).hexdigest()


def new_refresh_token() -> str:
    return "snr_" + secrets.token_urlsafe(48)


def new_api_key() -> str:
    return "sn_live_" + secrets.token_urlsafe(32)


def create_access_token(user_id: UUID, tenant_id: UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "iss": "supernova",
        "sub": str(user_id),
        "tid": str(tenant_id),
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer="supernova")
    if claims.get("typ") != "access":
        raise jwt.InvalidTokenError("Wrong token type")
    return claims
