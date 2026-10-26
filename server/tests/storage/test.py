import os
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from contracts import MemberRole
from core.account import AccountService
from core.account.service import principal_id
from core.account.store import AccountStore
from core.auth import AccessControl, AuthenticatedIdentity, Permission, Role
from core.auth.session import BrowserSession, LoginAttempt
from core.auth.store import SessionStore
from core.errors import AuthenticationError, AuthorizationError, ResourceConflictError
from core.storage.database import Database

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    url = os.environ.get("KINU_TEST_DATABASE_URL")
    if not url:
        pytest.skip("run make storage to verify PostgreSQL persistence")
    database = Database(url)
    await database.open()
    try:
        await database.migrate()
        yield database
    finally:
        await database.close()


@pytest.fixture
def identity() -> AuthenticatedIdentity:
    return AuthenticatedIdentity(
        subject=f"issuer#{secrets.token_hex(8)}",
        issuer="https://identity.example",
        email=f"{secrets.token_hex(8)}@example.com",
        email_verified=True,
        display_name="Workspace owner",
    )


async def test_session_persistence_stores_only_token_digest(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    first = SessionStore(database, lambda: now)
    session = BrowserSession(identity, secrets.token_urlsafe(32), now + timedelta(hours=1))
    await first.save(token, session)
    second = SessionStore(database, lambda: now)
    assert await second.get(token) == session
    async with database.connection() as connection:
        cursor = await connection.execute(
            "SELECT token_hash FROM kinu.sessions WHERE identity->>'subject' = %s",
            (identity.subject,),
        )
        row = await cursor.fetchone()
        assert row and row["token_hash"] != token
    await second.delete(token)
    with pytest.raises(AuthenticationError):
        await first.get(token)


async def test_expired_session_is_rejected(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    store = SessionStore(database, lambda: now)
    await store.save(token, BrowserSession(identity, "csrf", now + timedelta(seconds=1)))
    now += timedelta(seconds=2)
    with pytest.raises(AuthenticationError):
        await store.get(token)


async def test_login_is_browser_bound_and_single_use(database: Database) -> None:
    now = datetime.now(UTC)
    state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    store = SessionStore(database, lambda: now)
    attempt = LoginAttempt("verifier", "nonce", "/dashboard", now + timedelta(minutes=10))
    await store.begin(state, binding, attempt)
    with pytest.raises(AuthenticationError):
        await store.consume(state, "another-browser")
    assert await store.consume(state, binding) == attempt
    with pytest.raises(AuthenticationError):
        await store.consume(state, binding)


async def test_workspace_owner_is_persisted_and_scoped(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    repository = AccountStore(database, lambda: datetime.now(UTC))
    service = AccountService(repository, lambda: datetime.now(UTC))
    membership = await service.create_organisation(identity, "Kinu workspace")
    second = AccountStore(database, lambda: datetime.now(UTC))
    session = await AccountService(second, lambda: datetime.now(UTC)).session(identity)
    assert membership in session.organisations
    await AccessControl(second).require(identity, membership.organisation.id, Permission.TEAM_WRITE)
    with pytest.raises(AuthorizationError):
        await AccessControl(second).require(identity, "org_other", Permission.TEAM_WRITE)


async def test_invitation_acceptance_is_idempotent_and_does_not_reenable_members(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    now = datetime.now(UTC)
    store = AccountStore(database, lambda: now)
    service = AccountService(store, lambda: now)
    membership = await service.create_organisation(identity, "Invitations")
    org = membership.organisation.id
    invited = AuthenticatedIdentity(
        subject=f"issuer#{secrets.token_hex(8)}",
        issuer=identity.issuer,
        email=f"{secrets.token_hex(8)}@example.com",
        email_verified=True,
    )
    assert invited.email is not None
    await service.invite(org, identity, invited.email, MemberRole.VIEWER)
    assert (await service.session(invited)).organisations[0].role is MemberRole.VIEWER
    await service.session(invited)
    assert len(await store.list_principals(org)) == 2
    await store.update_member(org, principal_id(invited.subject), 0, Role.VIEWER, False, now)
    assert not (await service.session(invited)).organisations


async def test_last_administrator_cannot_be_removed(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    now = datetime.now(UTC)
    store = AccountStore(database, lambda: now)
    membership = await AccountService(store, lambda: now).create_organisation(identity, "Owners")
    with pytest.raises(ResourceConflictError, match="administrator"):
        await store.update_member(
            membership.organisation.id, principal_id(identity.subject), 0, Role.VIEWER, False, now
        )
