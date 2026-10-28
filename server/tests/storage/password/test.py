import secrets
from datetime import UTC, datetime, timedelta

import pytest
from core.auth.forms import FormError
from core.auth.password import PasswordClient
from core.auth.session import BrowserSession
from core.auth.store import SessionStore
from core.errors import AuthenticationError
from core.storage.database import Database
from pydantic import SecretStr

pytestmark = pytest.mark.anyio
PASSWORD = SecretStr("A-long-private-password-123!")


class Mailer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    async def code(self, email: str, code: str, purpose: str, message_id: str) -> None:
        self.messages.append((email, code, purpose))


async def test_password_lifecycle_keeps_hashes_and_revokes_stale_sessions(
    database: Database,
) -> None:
    now, mailer = datetime.now(UTC), Mailer()
    client = PasswordClient(
        database, mailer, "https://kinu.example/auth", SecretStr("a" * 32), lambda: now
    )
    email = "owner@example.com"
    await client.signup(email, "Owner", PASSWORD)
    user = await database.collection("users").find_one({"email": email})
    assert user and user["password_hash"].startswith("$argon2id$")
    assert PASSWORD.get_secret_value() not in str(user)
    assert (await client.signin(email, PASSWORD)).next == "verify"
    code = mailer.messages[-1][1]
    stored = await database.collection("auth_codes").find_one()
    assert stored and code not in str(stored)
    with pytest.raises(FormError):
        await client.verify(email, SecretStr("000000" if code != "000000" else "000001"))
    await client.verify(email, SecretStr(code))
    with pytest.raises(FormError):
        await client.verify(email, SecretStr(code))
    login = await client.signin(email, PASSWORD)
    assert login.identity and login.identity.email_verified
    sessions = SessionStore(database, lambda: now)
    token = secrets.token_urlsafe(32)
    session = BrowserSession(
        login.identity, "csrf", now + timedelta(hours=12), login.identity.subject, login.version
    )
    await sessions.save(token, session)
    assert await sessions.get(token) == session
    await client.forgot(email)
    reset_code = mailer.messages[-1][1]
    with pytest.raises(FormError):
        await client.reset(email, SecretStr(reset_code), SecretStr("short"))
    replacement = SecretStr("Another-private-password-456!")
    await client.reset(email, SecretStr(reset_code), replacement)
    with pytest.raises(AuthenticationError):
        await sessions.get(token)
    await sessions.save(token, session)
    with pytest.raises(AuthenticationError):
        await sessions.get(token)
    with pytest.raises(FormError):
        await client.signin(email, PASSWORD)
    assert (await client.signin(email, replacement)).version == 1
    with pytest.raises(FormError):
        await client.reset(email, SecretStr(reset_code), replacement)


async def test_recovery_hides_unknown_accounts_and_codes_expire(database: Database) -> None:
    now, mailer = datetime.now(UTC), Mailer()
    client = PasswordClient(
        database, mailer, "https://kinu.example/auth", SecretStr("a" * 32), lambda: now
    )
    await client.forgot("missing@example.com")
    await client.resend("missing@example.com")
    assert not mailer.messages
    await client.signup("owner@example.com", "Owner", PASSWORD)
    code = mailer.messages[-1][1]
    now += timedelta(minutes=11)
    with pytest.raises(FormError):
        await client.verify("owner@example.com", SecretStr(code))
    with pytest.raises(FormError, match="Email or password"):
        await client.signin("missing@example.com", PASSWORD)
