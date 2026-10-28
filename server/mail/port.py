from typing import Any, Protocol

from mail.models import Mailbox


class MailStore(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def pending(self, key: str, value: dict[str, Any]) -> None: ...
    async def consume(
        self, state: str, session: str, actor: str, org: str, now: int
    ) -> dict[str, Any]: ...
    async def mailbox(self, org: str, identity: str) -> tuple[Mailbox, dict[str, Any]]: ...
    async def list(self, org: str) -> tuple[Mailbox, ...]: ...
    async def save(
        self,
        mailbox: Mailbox,
        grant: str | None,
        previous: int | None,
        *,
        cursor: dict[str, Any] | None = None,
    ) -> None: ...
    async def due(self, now: int) -> tuple[dict[str, Any], ...]: ...
    async def claim(self, key: str, revision: int, owner: str, now: int) -> bool: ...
    async def checkpoint(
        self,
        box: Mailbox,
        owner: str,
        cursor: dict[str, Any],
        grant: str | None = None,
        *,
        release: bool = False,
    ) -> None: ...
    async def message(
        self, org: str, box: str, identity: str, value: dict[str, Any] | None = None
    ) -> dict[str, Any] | None: ...
