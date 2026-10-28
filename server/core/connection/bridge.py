from typing import Any

from contracts import Connection

from core.connection.models import AwsLink
from core.errors import ResourceConflictError
from core.private import PrivateClient


class ConnectionBridge:
    def __init__(self, client: PrivateClient) -> None:
        self.client = client

    async def call(self, operation: str, connection: Connection, **values: Any) -> Any:
        return await self.client.call(
            "/connections",
            {
                "operation": operation,
                "organisation_id": connection.organisation_id,
                "connection_id": connection.id,
                "revision": connection.revision,
                **values,
            },
        )

    async def verify(self, link: AwsLink, connection: Connection) -> None:
        result = await self.call("verify", connection, link_revision=link.revision)
        if result is not True:
            raise ResourceConflictError("AWS role verification failed")

    async def metadata(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        result = await self.call(
            "provider",
            connection,
            candidate=connection.model_dump(mode="json") if connection.revision == 0 else None,
        )
        return tuple(result)

    async def credential_identity(
        self, connection: Connection, secret_connection: Connection, secret_reference: str
    ) -> str:
        result = await self.call(
            "identity",
            connection,
            secret_connection_id=secret_connection.id,
            secret_reference=secret_reference,
        )
        if not isinstance(result, str):
            raise ResourceConflictError("provider returned no credential identity")
        return result


class SecretMetadata:
    def __init__(self, bridge: ConnectionBridge) -> None:
        self.bridge = bridge

    async def resources_for(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        return tuple(await self.bridge.call("secrets", connection))

    async def versions_for(self, connection: Connection, secret: str) -> tuple[dict[str, Any], ...]:
        return tuple(await self.bridge.call("versions", connection, secret_reference=secret))


class RuntimeMetadata:
    def __init__(self, bridge: ConnectionBridge) -> None:
        self.bridge = bridge

    async def resources_for(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        return tuple(await self.bridge.call("runtimes", connection))
