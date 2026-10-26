from typing import Protocol

from contracts import Connection, SetupSession


class BrowserSetupApi(Protocol):
    @property
    def setup_url(self) -> str: ...

    @property
    def gateway_url(self) -> str: ...

    async def begin(
        self,
        organisation_id: str,
        connection_id: str,
        subject: str,
        extra_domains: tuple[str, ...] = (),
    ) -> tuple[SetupSession, str]: ...

    async def complete(
        self,
        organisation_id: str,
        setup_id: str,
        expected_revision: int,
        token: str,
        subject: str,
        actor_id: str | None = None,
    ) -> tuple[SetupSession, Connection, tuple[str, ...]]: ...

    async def abort(
        self, organisation_id: str, setup_id: str, expected_revision: int, subject: str
    ) -> SetupSession: ...

    async def get(self, organisation_id: str, setup_id: str) -> SetupSession: ...

    async def reap_expired(self, organisation_id: str) -> tuple[SetupSession, ...]: ...
