import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.auth.models import User

PASSWORD_HASH = PasswordHash.recommended()
# Unknown accounts still perform a password verification.
DUMMY_HASH = PASSWORD_HASH.hash("unused-demo-account")
TOKEN_LIFETIME_SECONDS = 1800
TOKEN_ISSUER = "tracerisk"
TOKEN_AUDIENCE = "tracerisk-api"


async def authenticate(
    session: AsyncSession, username: str, password: str
) -> User | None:
    user = await session.scalar(select(User).where(User.username == username))
    valid = await asyncio.to_thread(
        PASSWORD_HASH.verify, password, user.password_hash if user else DUMMY_HASH
    )
    return user if valid else None


def create_access_token(user_id: UUID, secret: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(seconds=TOKEN_LIFETIME_SECONDS),
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
        },
        secret,
        algorithm="HS256",
    )


def decode_user_id(token: str, secret: str) -> UUID:
    claims = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        issuer=TOKEN_ISSUER,
        audience=TOKEN_AUDIENCE,
        options={"require": ["sub", "iat", "exp", "iss", "aud"]},
    )
    return UUID(claims["sub"])
