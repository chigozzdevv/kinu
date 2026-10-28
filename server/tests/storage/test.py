import asyncio
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from contracts import MemberRole
from core.account import AccountService
from core.account.service import principal_id
from core.account.store import AccountStore
from core.auth import AccessControl, AuthenticatedIdentity, Permission, Role
from core.auth.forms import AttemptLimitError, AuthForm, FormStore
from core.auth.session import BrowserSession
from core.auth.store import SessionStore
from core.errors import AuthenticationError, AuthorizationError, ResourceConflictError
from core.storage.database import Database

pytestmark = pytest.mark.anyio


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
    row = await database.collection("sessions").find_one({"identity.subject": identity.subject})
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


async def test_forms_are_browser_bound_expiring_and_single_use(database: Database) -> None:
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    store = FormStore(database, lambda: now)
    form = AuthForm(secrets.token_urlsafe(32), now + timedelta(minutes=10))
    await store.save(token, form)
    assert await store.get(token) == form
    with pytest.raises(AuthenticationError):
        await store.consume(token, "another-browser")
    await store.consume(token, form.csrf_token)
    with pytest.raises(AuthenticationError):
        await store.consume(token, form.csrf_token)
    await store.save(token, form)
    now += timedelta(minutes=11)
    with pytest.raises(AuthenticationError):
        await store.get(token)


async def test_attempt_limits_are_atomic_shared_and_expire(database: Database) -> None:
    now = datetime.now(UTC)
    store = FormStore(database, lambda: now)
    email = secrets.token_hex(8) + "@example.com"
    results = await asyncio.gather(
        *(store.limit(email, "127.0.0.1", "signin") for _ in range(15)), return_exceptions=True
    )
    assert sum(item is None for item in results) == 10
    assert sum(isinstance(item, AttemptLimitError) for item in results) == 5
    now += timedelta(minutes=16)
    await FormStore(database, lambda: now).limit(email, "127.0.0.1", "signin")
    row = await database.collection("auth_limits").find_one()
    assert row and email not in row["key"]


async def test_password_reset_revokes_only_matching_account_sessions(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    now = datetime.now(UTC)
    store = SessionStore(database, lambda: now)
    assert identity.email
    own, other = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    await store.save(own, BrowserSession(identity, "csrf", now + timedelta(hours=1)))
    another = AuthenticatedIdentity(
        subject="other#user",
        issuer="https://other.example",
        email=identity.email,
        email_verified=True,
    )
    await store.save(other, BrowserSession(another, "csrf", now + timedelta(hours=1)))
    await store.revoke(identity.email.upper(), identity.issuer)
    with pytest.raises(AuthenticationError):
        await store.get(own)
    assert (await store.get(other)).identity == another


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
