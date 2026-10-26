from datetime import UTC, datetime, timedelta

import httpx
from core.auth import AuthenticatedIdentity
from core.auth.browser import BrowserAuth
from core.auth.cognito import CognitoConfig
from core.auth.options import BrowserOptions
from core.auth.session import BrowserSession, LoginAttempt
from core.errors import AuthenticationError

HEADERS = {
    "Cookie": "kinu.session=valid-token",
    "Origin": "http://localhost:5173",
    "X-CSRF-Token": "test-csrf",
}


class MemorySessions:
    def __init__(self) -> None:
        self.attempts: dict[tuple[str, str], LoginAttempt] = {}
        self.sessions: dict[str, BrowserSession] = {}

    async def begin(self, state: str, browser: str, attempt: LoginAttempt) -> None:
        self.attempts[state, browser] = attempt

    async def consume(self, state: str, browser: str) -> LoginAttempt:
        attempt = self.attempts.pop((state, browser), None)
        if attempt is None or attempt.expires_at <= datetime.now(UTC):
            raise AuthenticationError("sign-in attempt is invalid or expired")
        return attempt

    async def save(self, token: str, session: BrowserSession) -> None:
        self.sessions[token] = session

    async def get(self, token: str) -> BrowserSession:
        session = self.sessions.get(token)
        if session is None or session.expires_at <= datetime.now(UTC):
            raise AuthenticationError("session is invalid or expired")
        return session

    async def delete(self, token: str) -> None:
        self.sessions.pop(token, None)


class UnusedLogin:
    async def verify_login(self, token: str, nonce: str) -> tuple[AuthenticatedIdentity, datetime]:
        raise AssertionError("login is not part of this test")


def make_browser(identity: AuthenticatedIdentity) -> BrowserAuth:
    store = MemorySessions()
    store.sessions["valid-token"] = BrowserSession(
        identity, "test-csrf", datetime.now(UTC) + timedelta(hours=1)
    )
    return BrowserAuth(
        BrowserOptions(
            CognitoConfig("us-east-1_example", "kinuclient"),
            "https://login.example.com",
            "http://localhost:5173",
        ),
        store,
        UnusedLogin(),
        httpx.AsyncClient(),
        lambda: datetime.now(UTC),
    )
