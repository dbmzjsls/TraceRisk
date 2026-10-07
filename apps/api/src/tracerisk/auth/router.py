from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.auth.models import Role, User
from tracerisk.auth.schemas import LoginRequest, TokenResponse, UserResponse
from tracerisk.auth.service import (
    TOKEN_LIFETIME_SECONDS,
    authenticate,
    create_access_token,
    decode_user_id,
)
from tracerisk.config import Settings
from tracerisk.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)
Session = Annotated[AsyncSession, Depends(get_session)]


def get_signing_secret(request: Request) -> str:
    settings: Settings = request.app.state.settings
    if settings.jwt_secret is None:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    return settings.jwt_secret.get_secret_value()


SigningSecret = Annotated[str, Depends(get_signing_secret)]


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_bearer_user_id(
    credentials: HTTPAuthorizationCredentials | None, secret: str
) -> UUID:
    """Apply the API's shared Bearer credential semantics without a DB Session."""
    if credentials is None:
        raise unauthorized()
    try:
        return decode_user_id(credentials.credentials, secret)
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise unauthorized() from None


async def get_current_user(
    session: Session,
    secret: SigningSecret,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    user_id = decode_bearer_user_id(credentials, secret)
    user = await session.get(User, user_id)
    if user is None:
        raise unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[..., Awaitable[User]]:
    async def check_role(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return check_role


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, response: Response, session: Session, secret: SigningSecret
) -> TokenResponse:
    user = await authenticate(
        session, payload.username, payload.password.get_secret_value()
    )
    if user is None:
        raise unauthorized()
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(
        access_token=create_access_token(user.id, secret),
        expires_in=TOKEN_LIFETIME_SECONDS,
    )


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
