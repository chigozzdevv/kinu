from datetime import UTC, datetime, timedelta

import httpx
from core.auth import AuthenticatedIdentity
from core.auth.browser import BrowserAuth
from core.auth.cognito import CognitoConfig
from core.auth.forms import Attempt, AuthForm
from core.auth.options import BrowserOptions
from core.auth.session import BrowserSession
from core.errors import AuthenticationError

HEADERS = {
    "Cookie": "kinu.session=valid-token",
    "Origin": "http://localhost:5173",
    "X-CSRF-Token": "test-csrf",
}


class MemorySessions:
    def __init__(self) -> None:
        self.sessions: dict[str, BrowserSession] = {}

    async def save(self, token: str, session: BrowserSession) -> None:
        self.sessions[token] = session

    async def get(self, token: str) -> BrowserSession:
        session = self.sessions.get(token)
        if session is None or session.expires_at <= datetime.now(UTC):
            raise AuthenticationError("session is invalid or expired")
        return session

    async def delete(self, token: str) -> None:
        self.sessions.pop(token, None)

    async def revoke(self, email: str, issuer: str) -> None:
        self.sessions = {
            token: session
            for token, session in self.sessions.items()
            if session.identity.issuer != issuer
            or (session.identity.email or "").casefold() != email.casefold()
        }


class MemoryForms:
    def __init__(self) -> None:
        self.values: dict[str, AuthForm] = {}

    async def save(self, token: str, form: AuthForm) -> None:
        self.values[token] = form

    async def get(self, token: str) -> AuthForm:
        form = self.values.get(token)
        if not form or form.expires_at <= datetime.now(UTC):
            raise AuthenticationError("Sign-in form expired. Please try again.")
        return form

    async def consume(self, token: str, csrf: str) -> None:
        form = await self.get(token)
        if form.csrf_token != csrf:
            raise AuthenticationError("Form does not match")
        del self.values[token]

    async def limit(self, email: str, address: str, action: Attempt) -> None:
        pass


class UnusedLogin:
    async def verify_login(self, token: str) -> tuple[AuthenticatedIdentity, datetime]:
        raise AssertionError("login is not part of this test")


def make_browser(identity: AuthenticatedIdentity) -> BrowserAuth:
    store = MemorySessions()
    store.sessions["valid-token"] = BrowserSession(
        identity, "test-csrf", datetime.now(UTC) + timedelta(hours=1)
    )
    return BrowserAuth(
        BrowserOptions(
            CognitoConfig("us-east-1_example", "kinuclient"),
            "http://localhost:5173",
        ),
        store,
        MemoryForms(),
        UnusedLogin(),
        httpx.AsyncClient(),
        lambda: datetime.now(UTC),
    )
