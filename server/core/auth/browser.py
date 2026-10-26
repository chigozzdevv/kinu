import base64
import hashlib
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode, urlsplit

import httpx

from core.auth.identity import AuthenticatedIdentity
from core.auth.options import BrowserOptions
from core.auth.session import BrowserSession, LoginAttempt, SessionRepository
from core.errors import AuthenticationError, AuthorizationError, IdentityUnavailableError


class LoginVerifier(Protocol):
    async def verify_login(
        self, token: str, nonce: str
    ) -> tuple[AuthenticatedIdentity, datetime]: ...


class BrowserAuth:
    def __init__(
        self,
        options: BrowserOptions,
        store: SessionRepository,
        verifier: LoginVerifier,
        http: httpx.AsyncClient,
        clock: Callable[[], datetime],
    ) -> None:
        self.options = options
        self.store = store
        self._verifier = verifier
        self._http = http
        self._clock = clock

    async def begin(self, return_path: str) -> tuple[str, str]:
        state = secrets.token_urlsafe(32)
        browser = secrets.token_urlsafe(32)
        attempt = LoginAttempt(
            verifier=secrets.token_urlsafe(48),
            nonce=secrets.token_urlsafe(32),
            return_path=_return_path(return_path),
            expires_at=self._clock() + timedelta(minutes=10),
        )
        await self.store.begin(state, browser, attempt)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest())
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.options.cognito.client_id,
                "redirect_uri": self.options.callback_url,
                "scope": "openid email profile",
                "state": state,
                "nonce": attempt.nonce,
                "code_challenge": challenge.decode().rstrip("="),
                "code_challenge_method": "S256",
            }
        )
        return f"{self.options.domain.rstrip('/')}/oauth2/authorize?{query}", browser

    async def complete(
        self, code: str, state: str, browser: str, previous_token: str | None
    ) -> tuple[str, BrowserSession, str]:
        if not code or not state or not browser or max(len(code), len(state), len(browser)) > 4096:
            raise AuthenticationError("sign-in response is invalid")
        attempt = await self.store.consume(state, browser)
        token = await self._exchange(code, attempt.verifier)
        identity, identity_expiry = await self._verifier.verify_login(token, attempt.nonce)
        expires_at = min(identity_expiry, self._clock() + timedelta(hours=1))
        if expires_at <= self._clock():
            raise AuthenticationError("sign-in identity has expired")
        session = BrowserSession(identity, secrets.token_urlsafe(32), expires_at)
        session_token = secrets.token_urlsafe(32)
        await self.store.save(session_token, session)
        if previous_token:
            await self.store.delete(previous_token)
        return session_token, session, attempt.return_path

    def require_csrf(self, session: BrowserSession, origin: str | None, token: str | None) -> None:
        if (
            origin != self.options.origin
            or not token
            or not token.isascii()
            or len(token) > 128
            or not secrets.compare_digest(session.csrf_token, token)
        ):
            raise AuthorizationError("request origin or CSRF token is invalid")

    @property
    def logout_url(self) -> str:
        query = urlencode(
            {
                "client_id": self.options.cognito.client_id,
                "logout_uri": f"{self.options.origin}/auth",
            }
        )
        return f"{self.options.domain.rstrip('/')}/logout?{query}"

    async def _exchange(self, code: str, verifier: str) -> str:
        try:
            response = await self._http.post(
                f"{self.options.domain.rstrip('/')}/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.options.cognito.client_id,
                    "redirect_uri": self.options.callback_url,
                    "code": code,
                    "code_verifier": verifier,
                },
                timeout=10,
                follow_redirects=False,
            )
            if response.status_code == 400:
                raise AuthenticationError("sign-in code was rejected")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("id_token"), str):
                raise ValueError("identity token is missing")
            return str(payload["id_token"])
        except (httpx.HTTPError, ValueError) as error:
            raise IdentityUnavailableError("sign-in provider is unavailable") from error


def _return_path(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme
        or parts.netloc
        or parts.fragment
        or any(character in value for character in "\\\r\n\x00")
        or not (parts.path == "/dashboard" or parts.path.startswith("/dashboard/"))
    ):
        raise AuthenticationError("sign-in return path is invalid")
    return value
