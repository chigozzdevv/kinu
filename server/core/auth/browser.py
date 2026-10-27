import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from core.auth.forms import Attempt, AuthForm, FormRepository
from core.auth.identity import AuthenticatedIdentity
from core.auth.options import BrowserOptions
from core.auth.password import PasswordClient
from core.auth.session import BrowserSession, SessionRepository
from core.errors import AuthenticationError, AuthorizationError


class LoginVerifier(Protocol):
    async def verify_login(self, token: str) -> tuple[AuthenticatedIdentity, datetime]: ...


class BrowserAuth:
    def __init__(
        self,
        options: BrowserOptions,
        store: SessionRepository,
        forms: FormRepository,
        verifier: LoginVerifier,
        http: httpx.AsyncClient,
        clock: Callable[[], datetime],
    ) -> None:
        self.options, self.store, self.forms = options, store, forms
        self.verifier, self.clock = verifier, clock
        self.passwords = PasswordClient(options.cognito, http)

    async def prepare(self, token: str | None) -> tuple[str, AuthForm]:
        if token:
            try:
                return token, await self.forms.get(token)
            except AuthenticationError:
                pass
        token = secrets.token_urlsafe(32)
        form = AuthForm(secrets.token_urlsafe(32), self.clock() + timedelta(minutes=10))
        await self.forms.save(token, form)
        return token, form

    async def guard(
        self,
        token: str,
        origin: str | None,
        csrf: str | None,
        email: str,
        address: str,
        action: Attempt,
    ) -> None:
        self.require_origin(origin)
        form = await self.forms.get(token)
        self._csrf(form.csrf_token, csrf)
        await self.forms.limit(email, address, action)

    async def signin(
        self, email: str, password: SecretStr, form: str, csrf: str, previous: str | None
    ) -> tuple[str | None, BrowserSession | None, str]:
        result = await self.passwords.signin(email, password)
        if result.token is None:
            return None, None, result.next
        identity, expiry = await self.verifier.verify_login(result.token.get_secret_value())
        if not identity.email or identity.email.casefold() != email.casefold():
            raise AuthenticationError("Sign-in identity does not match the requested account.")
        expires_at = min(expiry, self.clock() + timedelta(hours=12))
        if expires_at <= self.clock():
            raise AuthenticationError("Sign-in identity has expired.")
        await self.forms.consume(form, csrf)
        session = BrowserSession(identity, secrets.token_urlsafe(32), expires_at)
        token = secrets.token_urlsafe(32)
        await self.store.save(token, session)
        if previous:
            await self.store.delete(previous)
        return token, session, "complete"

    def require_origin(self, origin: str | None) -> None:
        if origin != self.options.origin:
            raise AuthorizationError("Request origin is invalid.")

    def require_csrf(self, session: BrowserSession, origin: str | None, token: str | None) -> None:
        self.require_origin(origin)
        self._csrf(session.csrf_token, token)

    @staticmethod
    def _csrf(expected: str, token: str | None) -> None:
        if (
            not token
            or not token.isascii()
            or len(token) > 128
            or not secrets.compare_digest(expected, token)
        ):
            raise AuthorizationError("Form verification failed. Please try again.")


def return_path(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme
        or parts.netloc
        or parts.fragment
        or any(char in value for char in "\\\r\n\x00")
        or not (parts.path == "/dashboard" or parts.path.startswith("/dashboard/"))
    ):
        raise AuthenticationError("Sign-in return path is invalid.")
    return value
