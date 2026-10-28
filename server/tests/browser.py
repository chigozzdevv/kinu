from datetime import UTC, datetime, timedelta

from core.auth import AuthenticatedIdentity
from core.auth.browser import BrowserAuth
from core.auth.forms import Attempt, AuthForm
from core.auth.options import BrowserOptions
from core.auth.password import SignInResult
from core.auth.session import BrowserSession
from core.errors import AuthenticationError
from pydantic import SecretStr

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


class MemoryPasswords:
    def __init__(self, identity: AuthenticatedIdentity | None = None) -> None:
        self.identity = identity
        self.calls: list[str] = []
        self.next: str = "complete"

    async def signin(self, email: str, password: SecretStr) -> SignInResult:
        self.calls.append("signin")
        return (
            SignInResult("verify")
            if self.next == "verify"
            else SignInResult("complete", self.identity)
        )

    async def signup(self, email: str, name: str, password: SecretStr) -> bool:
        self.calls.append("signup")
        return False

    async def verify(self, email: str, code: SecretStr) -> None:
        self.calls.append("verify")

    async def resend(self, email: str) -> None:
        self.calls.append("resend")

    async def forgot(self, email: str) -> None:
        self.calls.append("forgot")

    async def reset(self, email: str, code: SecretStr, password: SecretStr) -> None:
        self.calls.append("reset")


def make_browser(identity: AuthenticatedIdentity) -> BrowserAuth:
    store = MemorySessions()
    store.sessions["valid-token"] = BrowserSession(
        identity, "test-csrf", datetime.now(UTC) + timedelta(hours=1)
    )
    return BrowserAuth(
        BrowserOptions("http://localhost:5173"),
        store,
        MemoryForms(),
        MemoryPasswords(identity),
        lambda: datetime.now(UTC),
    )
