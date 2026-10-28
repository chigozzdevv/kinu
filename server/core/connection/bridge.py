import asyncio
import json
from typing import Any

from connectors.aws import AwsApi
from contracts import Connection

from core.connection.models import AwsLink
from core.errors import ResourceConflictError


class ConnectionBridge:
    def __init__(self, aws: AwsApi, region: str, function: str) -> None:
        self._aws, self._region, self._function = aws, region, function

    async def call(self, operation: str, connection: Connection, **values: Any) -> Any:
        result = await self._aws.call(
            "lambda",
            "invoke",
            self._region,
            FunctionName=self._function,
            InvocationType="RequestResponse",
            Payload=json.dumps(
                {
                    "operation": operation,
                    "organisation_id": connection.organisation_id,
                    "connection_id": connection.id,
                    "revision": connection.revision,
                    **values,
                }
            ).encode(),
        )
        stream = result["Payload"]
        try:
            payload = json.loads(await asyncio.to_thread(stream.read, 1_048_577))
        finally:
            stream.close()
        if result.get("FunctionError") or not isinstance(payload, dict) or "result" not in payload:
            raise ResourceConflictError(
                "Connection check failed; check the selected resources and access configuration."
            )
        return payload["result"]

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
