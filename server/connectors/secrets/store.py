from typing import Any, Protocol, runtime_checkable

from contracts import Connection

from connectors.base import ConnectorContext, ConnectorResponse, SecretValue


@runtime_checkable
class SecretStore(Protocol):
    tools: frozenset[str] = frozenset(
        {
            "secretStore.getVersion",
            "secretStore.testConsumerAccess",
            "secretStore.disableVersion",
            "secretStore.destroyVersion",
        }
    )

    async def execute(
        self, tool: str, payload: dict[str, Any], context: ConnectorContext
    ) -> ConnectorResponse: ...

    async def add_version(self, secret: str, value: SecretValue) -> dict[str, Any]: ...

    async def add_version_for(
        self,
        connection: "Connection",
        secret: str,
        value: SecretValue,
        access_token: SecretValue | None = None,
    ) -> dict[str, Any]: ...

    async def access(self, version: str) -> SecretValue: ...

    async def access_for(self, connection: "Connection", version: str) -> SecretValue: ...

    async def versions(self, secret: str) -> tuple[dict[str, Any], ...]: ...

    async def versions_for(
        self, connection: "Connection", secret: str
    ) -> tuple[dict[str, Any], ...]: ...

    async def resources_for(self, connection: "Connection") -> tuple[dict[str, Any], ...]: ...

    async def disable(self, version: str) -> dict[str, Any]: ...

    async def disable_for(
        self, connection: "Connection", version: str, access_token: SecretValue | None = None
    ) -> dict[str, Any]: ...
