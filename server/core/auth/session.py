from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from core.auth.identity import AuthenticatedIdentity


@dataclass(frozen=True, slots=True)
class BrowserSession:
    identity: AuthenticatedIdentity
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginAttempt:
    verifier: str
    nonce: str
    return_path: str
    expires_at: datetime


class SessionRepository(Protocol):
    async def begin(self, state: str, browser: str, attempt: LoginAttempt) -> None: ...

    async def consume(self, state: str, browser: str) -> LoginAttempt: ...

    async def save(self, token: str, session: BrowserSession) -> None: ...

    async def get(self, token: str) -> BrowserSession: ...

    async def delete(self, token: str) -> None: ...
