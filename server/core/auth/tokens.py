from typing import Protocol

from core.auth.identity import AuthenticatedIdentity


class IdentityTokenVerifier(Protocol):
    async def verify(self, token: str) -> AuthenticatedIdentity: ...
