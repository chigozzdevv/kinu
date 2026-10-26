from typing import Protocol

from core.auth.identity import AuthenticatedIdentity
from core.errors import AuthenticationError


class IdentityTokenVerifier(Protocol):
    async def verify(self, token: str) -> AuthenticatedIdentity: ...


class CompositeTokenVerifier:
    def __init__(self, verifiers: tuple[IdentityTokenVerifier, ...]) -> None:
        if not verifiers:
            raise ValueError("composite verifier requires at least one verifier")
        self._verifiers = verifiers

    async def verify(self, token: str) -> AuthenticatedIdentity:
        last: AuthenticationError | None = None
        for verifier in self._verifiers:
            try:
                return await verifier.verify(token)
            except AuthenticationError as error:
                last = error
        raise AuthenticationError("identity token is invalid") from last
