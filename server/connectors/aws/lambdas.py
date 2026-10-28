import asyncio
import re
from typing import Any

from contracts import Connection, ConnectionRole, ConnectionStatus

from connectors.aws import AwsApi
from connectors.aws.roles import RoleResolver
from connectors.base.errors import ConnectorError


class LambdaRuntime:
    def __init__(self, aws: AwsApi, roles: RoleResolver) -> None:
        self._aws, self._roles = aws, roles

    async def current(self, connection: Connection, function: str) -> dict[str, str]:
        result = await self._call(connection, function, "get_alias", Name="live")
        return {"version": str(result["FunctionVersion"]), "revision": str(result["RevisionId"])}

    async def promote(
        self,
        connection: Connection,
        function: str,
        target: str,
        predecessor: str,
        expected_revision: str,
    ) -> dict[str, str]:
        if not target.isdigit() or not predecessor.isdigit():
            raise ConnectorError("runtime-version", "published consumer versions are required")
        current = await self.current(connection, function)
        if current["version"] == target:
            return current
        if current != {"version": predecessor, "revision": expected_revision}:
            raise ConnectorError(
                "runtime-current-changed", "consumer alias changed during rotation"
            )
        result = await self._call(
            connection,
            function,
            "update_alias",
            Name="live",
            FunctionVersion=target,
            RevisionId=expected_revision,
        )
        return {"version": str(result["FunctionVersion"]), "revision": str(result["RevisionId"])}

    async def _ready(self, connection: Connection, function: str) -> dict[str, Any]:
        for _ in range(15):
            config = await self._call(connection, function, "get_function_configuration")
            if config.get("LastUpdateStatus") == "Successful":
                return config
            if config.get("LastUpdateStatus") == "Failed":
                raise ConnectorError(
                    "runtime-update-failed", "consumer configuration update failed"
                )
            await asyncio.sleep(1)
        raise ConnectorError(
            "runtime-update-timeout", "consumer update has not finished", retryable=True
        )

    async def _call(
        self, connection: Connection, function: str, operation: str, **parameters: Any
    ) -> dict[str, Any]:
        if (
            ConnectionRole.RUNTIME not in connection.roles
            or connection.platform != "aws"
            or connection.status is not ConnectionStatus.READY
            or connection.archived_at
            or function not in connection.allowed_resources
            or not re.fullmatch(
                rf"arn:aws:lambda:{re.escape(connection.region)}:[0-9]{{12}}:function:[A-Za-z0-9_-]{{1,64}}",
                function,
            )
        ):
            raise ConnectorError(
                "runtime-out-of-scope", "Lambda function is outside this connection"
            )
        return await self._aws.call(
            "lambda",
            operation,
            connection.region,
            role=await self._roles.resolve(connection),
            FunctionName=function,
            **parameters,
        )
