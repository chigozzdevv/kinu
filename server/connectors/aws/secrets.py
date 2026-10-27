import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from contracts import Connection, ConnectionRole, ConnectionStatus

from connectors.aws import AwsApi
from connectors.aws.refs import SecretRef, resource_allowed
from connectors.aws.roles import RoleResolver
from connectors.base import ConnectorContext, ConnectorResponse, SecretValue
from connectors.base.errors import ConnectorError

AccessProbe = Callable[[ConnectorContext, str], Awaitable[dict[str, Any]]]


class AwsSecrets:
    tools = frozenset(
        {"secretStore.getVersion", "secretStore.disableVersion", "secretStore.testConsumerAccess"}
    )

    def __init__(
        self, aws: AwsApi, roles: RoleResolver, access_probe: AccessProbe | None = None
    ) -> None:
        self._aws, self._roles, self._access_probe = aws, roles, access_probe

    async def add_version_for(
        self,
        connection: Connection,
        secret: str,
        value: SecretValue,
        access_token: SecretValue | None = None,
    ) -> dict[str, Any]:
        ref = _scope(connection, secret)
        content = value.bytes()
        token = hashlib.sha256(ref.arn.encode() + b"\0" + content).hexdigest()
        response = await self._aws.call(
            "secretsmanager",
            "put_secret_value",
            ref.region,
            role=await self._roles.resolve(connection),
            SecretId=ref.arn,
            SecretBinary=content,
            ClientRequestToken=token,
            VersionStages=[f"kinu-candidate-{token[:24]}"],
        )
        reference = ref.reference(str(response["VersionId"]))
        return {"name": reference, "secret_reference": reference, "state": "ENABLED"}

    async def access_for(self, connection: Connection, version: str) -> SecretValue:
        ref = _scope(connection, version, version=True)
        response = await self._aws.call(
            "secretsmanager",
            "get_secret_value",
            ref.region,
            role=await self._roles.resolve(connection),
            SecretId=ref.arn,
            VersionId=ref.version,
        )
        raw = response.get("SecretBinary")
        if raw is None:
            raw = str(response["SecretString"]).encode()
        return SecretValue(raw)

    async def resources_for(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        role = await self._roles.resolve(connection)
        values: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            response = await self._aws.call(
                "secretsmanager",
                "list_secrets",
                connection.region,
                role=role,
                MaxResults=100,
                **({"NextToken": token} if token else {}),
            )
            for item in response.get("SecretList", []):
                if resource_allowed(item["ARN"], connection.allowed_resources):
                    values.append({"name": item["ARN"], "display_name": item["Name"]})
            token = response.get("NextToken")
            if not token:
                return tuple(values)

    async def versions_for(self, connection: Connection, secret: str) -> tuple[dict[str, Any], ...]:
        ref = _scope(connection, secret)
        role = await self._roles.resolve(connection)
        values: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            response = await self._aws.call(
                "secretsmanager",
                "list_secret_version_ids",
                ref.region,
                role=role,
                SecretId=ref.arn,
                IncludeDeprecated=True,
                MaxResults=100,
                **({"NextToken": token} if token else {}),
            )
            for item in response.get("Versions", []):
                name = ref.reference(str(item["VersionId"]))
                values.append(
                    {
                        "name": name,
                        "secret_reference": name,
                        "state": "ENABLED" if item.get("VersionStages") else "DEPRECATED",
                        "createTime": _timestamp(item.get("CreatedDate")),
                        "stages": tuple(item.get("VersionStages", [])),
                    }
                )
            token = response.get("NextToken")
            if not token:
                return tuple(values)

    async def promote(self, connection: Connection, version: str, previous: str) -> dict[str, Any]:
        ref = _scope(connection, version, version=True)
        old = _scope(connection, previous, version=True)
        if old.arn != ref.arn:
            raise ConnectorError(
                "secret-mismatch", "secret promotion must stay within one resource"
            )
        versions = await self.versions_for(connection, ref.arn)
        current = next((item for item in versions if "AWSCURRENT" in item["stages"]), None)
        if current and current["name"] == version:
            return {"secret_reference": version, "promoted": True}
        if current is None or current["name"] != previous:
            raise ConnectorError(
                "secret-current-changed", "current secret version changed during rotation"
            )
        await self._aws.call(
            "secretsmanager",
            "update_secret_version_stage",
            ref.region,
            role=await self._roles.resolve(connection),
            SecretId=ref.arn,
            VersionStage="AWSCURRENT",
            MoveToVersionId=ref.version,
            RemoveFromVersionId=old.version,
        )
        return {"secret_reference": version, "promoted": True}

    async def disable_for(
        self,
        connection: Connection,
        version: str,
        access_token: SecretValue | None = None,
    ) -> dict[str, Any]:
        ref = _scope(connection, version, version=True)
        metadata = next(
            (
                item
                for item in await self.versions_for(connection, ref.arn)
                if item["name"] == version
            ),
            None,
        )
        if metadata is None:
            raise ConnectorError("secret-version-missing", "secret version is unavailable")
        if "AWSCURRENT" in metadata["stages"]:
            raise ConnectorError(
                "secret-version-current", "the current secret version cannot be deprecated"
            )
        role = await self._roles.resolve(connection)
        for stage in metadata["stages"]:
            await self._aws.call(
                "secretsmanager",
                "update_secret_version_stage",
                ref.region,
                role=role,
                SecretId=ref.arn,
                VersionStage=stage,
                RemoveFromVersionId=ref.version,
            )
        return {"name": version, "state": "DEPRECATED", "destroyed": False}

    async def execute(
        self, tool: str, payload: dict[str, Any], context: ConnectorContext
    ) -> ConnectorResponse:
        version = payload.get("version") or payload.get("secret_reference")
        if not isinstance(version, str):
            raise ConnectorError(
                "invalid-secret-reference", "a versioned secret reference is required"
            )
        if tool == "secretStore.getVersion":
            ref = _scope(context.connection, version, version=True)
            result = next(
                (
                    item
                    for item in await self.versions_for(context.connection, ref.arn)
                    if item["name"] == version
                ),
                None,
            )
            if result is None:
                raise ConnectorError("secret-version-missing", "secret version is unavailable")
            return ConnectorResponse(result=result)
        if tool == "secretStore.disableVersion":
            return ConnectorResponse(result=await self.disable_for(context.connection, version))
        if tool == "secretStore.testConsumerAccess" and self._access_probe:
            return ConnectorResponse(result=await self._access_probe(context, version))
        raise ConnectorError("unsupported-tool", "this operation requires a runtime access probe")

    async def access(self, version: str) -> SecretValue:
        raise _unscoped()

    async def add_version(self, secret: str, value: SecretValue) -> dict[str, Any]:
        raise _unscoped()

    async def versions(self, secret: str) -> tuple[dict[str, Any], ...]:
        raise _unscoped()

    async def disable(self, version: str) -> dict[str, Any]:
        raise _unscoped()


def _scope(connection: Connection, value: str, *, version: bool = False) -> SecretRef:
    ref = SecretRef.parse(value, version=version)
    if (
        ConnectionRole.SECRET_STORE not in connection.roles
        or connection.platform != "aws"
        or connection.region != ref.region
        or connection.archived_at
        or connection.status is not ConnectionStatus.READY
        or not resource_allowed(ref.arn, connection.allowed_resources)
    ):
        raise ConnectorError("secret-out-of-scope", "secret is outside this connection")
    return ref


def _unscoped() -> ConnectorError:
    return ConnectorError(
        "secret-context-required", "secret access requires a workspace connection"
    )


def _timestamp(value: object) -> str | None:
    return value.astimezone(UTC).isoformat() if isinstance(value, datetime) else None
