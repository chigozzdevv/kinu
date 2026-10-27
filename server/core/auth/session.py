from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from core.auth.identity import AuthenticatedIdentity


@dataclass(frozen=True, slots=True)
class BrowserSession:
    identity: AuthenticatedIdentity
    csrf_token: str
    expires_at: datetime


class SessionRepository(Protocol):
    async def save(self, token: str, session: BrowserSession) -> None: ...

    async def get(self, token: str) -> BrowserSession: ...

    async def delete(self, token: str) -> None: ...

    async def revoke(self, email: str, issuer: str) -> None: ...
