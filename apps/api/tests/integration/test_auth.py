from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select

from tracerisk.auth.models import Role, User
from tracerisk.auth.router import require_roles, router
from tracerisk.auth.seed import DEMO_ACCOUNTS, seed_accounts
from tracerisk.auth.service import PASSWORD_HASH, TOKEN_AUDIENCE, TOKEN_ISSUER
from tracerisk.config import Settings
from tracerisk.main import app, lifespan

SECRET = "integration-test-signing-secret-at-least-48-characters"


@pytest.fixture
async def client(
    database: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("JWT_SECRET", SECRET)
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            await seed_accounts(session)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as connection:
            yield connection


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["token_type"] == "bearer"
    assert response.json()["expires_in"] == 1800
    return str(response.json()["access_token"])


@pytest.mark.anyio
async def test_demo_accounts_and_idempotent_seed(client: httpx.AsyncClient) -> None:
    async with app.state.session_factory() as session:
        users = (await session.scalars(select(User))).all()
        before = {user.username: (user.id, user.password_hash) for user in users}
        assert len(users) == 3
        for username, password, role in DEMO_ACCOUNTS:
            token = await login(client, username, password)
            response = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 200
            assert response.json() == {
                "id": str(before[username][0]),
                "username": username,
                "role": role,
            }
            assert before[username][1].startswith("$argon2id$")
            assert PASSWORD_HASH.verify(password, before[username][1])
        # Seeding must not reset either a password or a role for an existing account.
        user = next(user for user in users if user.username == "analyst")
        user.password_hash = PASSWORD_HASH.hash("ChangedPassword!2026")
        user.role = Role.REVIEWER
        await session.commit()
        before[user.username] = (user.id, user.password_hash)
        await seed_accounts(session)
    async with app.state.session_factory() as session:
        users = (await session.scalars(select(User))).all()
        assert {u.username: (u.id, u.password_hash) for u in users} == before
        assert next(u for u in users if u.username == "analyst").role == "reviewer"


@pytest.mark.anyio
async def test_bad_login_and_missing_credentials(client: httpx.AsyncClient) -> None:
    for username in ("analyst", "unknown"):
        response = await client.post(
            "/auth/login", json={"username": username, "password": "wrong"}
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid authentication credentials"}
        assert response.headers["www-authenticate"] == "Bearer"
    for authorization in (None, "Basic abc", "Bearer", "Bearer malformed"):
        response = await client.get(
            "/auth/me",
            headers={"Authorization": authorization} if authorization else {},
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    response = await client.post(
        "/auth/login",
        json={"username": "analyst", "password": "AnalystDemo!2026", "role": "admin"},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_reject_invalid_tokens(client: httpx.AsyncClient) -> None:
    token = await login(client, "analyst", "AnalystDemo!2026")
    claims = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience=TOKEN_AUDIENCE,
        issuer=TOKEN_ISSUER,
    )
    now = datetime.now(UTC)
    invalid = [
        jwt.encode(claims, "wrong-signing-secret-32-characters", algorithm="HS256"),
        jwt.encode(claims, SECRET, algorithm="HS384"),
        jwt.encode(claims, "", algorithm="none"),
    ]
    for field, value in (
        ("exp", now - timedelta(seconds=1)),
        ("iat", now + timedelta(hours=1)),
        ("sub", str(uuid4())),
        ("sub", "invalid-uuid"),
        ("iss", "other-service"),
        ("aud", "other-api"),
    ):
        invalid.append(jwt.encode({**claims, field: value}, SECRET, algorithm="HS256"))
    for field in ("exp", "iat", "sub", "iss", "aud"):
        invalid.append(
            jwt.encode(
                {k: v for k, v in claims.items() if k != field},
                SECRET,
                algorithm="HS256",
            )
        )
    for token in invalid:
        response = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


@pytest.mark.anyio
async def test_role_dependencies_and_fresh_role(client: httpx.AsyncClient) -> None:
    protected = FastAPI(lifespan=lifespan)
    protected.include_router(router)

    @protected.get("/reviewer-only")
    async def reviewer_only(
        user: Annotated[User, Depends(require_roles(Role.REVIEWER))],
    ) -> dict[str, str]:
        return {"username": user.username}

    @protected.get("/admin-only")
    async def admin_only(
        user: Annotated[User, Depends(require_roles(Role.ADMIN))],
    ) -> dict[str, str]:
        return {"username": user.username}

    async with (
        protected.router.lifespan_context(protected),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=protected), base_url="http://test"
        ) as guarded,
    ):
        assert (await guarded.get("/reviewer-only")).status_code == 401
        for username, password, role in DEMO_ACCOUNTS:
            token = await login(client, username, password)
            headers = {"Authorization": f"Bearer {token}"}
            for path, allowed in (
                ("reviewer-only", "reviewer"),
                ("admin-only", "admin"),
            ):
                response = await guarded.get(f"/{path}", headers=headers)
                assert response.status_code == (200 if role == allowed else 403)
        token = await login(client, "reviewer", "ReviewerDemo!2026")
        async with app.state.session_factory() as session:
            user = await session.scalar(select(User).where(User.username == "reviewer"))
            assert user is not None
            user.role = Role.ANALYST
            await session.commit()
        response = await guarded.get(
            "/reviewer-only", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403


@pytest.mark.anyio
async def test_missing_signing_configuration(client: httpx.AsyncClient) -> None:
    app.state.settings.jwt_secret = None
    response = await client.post(
        "/auth/login", json={"username": "analyst", "password": "AnalystDemo!2026"}
    )
    assert response.status_code == 503
    assert (await client.get("/health")).status_code == 200
