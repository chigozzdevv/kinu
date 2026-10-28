import asyncio
import json
import re
from typing import Any

from connectors.aws.lambdas import LambdaRuntime
from connectors.aws.refs import SecretRef
from connectors.base.errors import ConnectorError
from contracts import Connection


class DemoRuntime(LambdaRuntime):
    async def candidate(
        self, connection: Connection, function: str, version: str, request_id: str
    ) -> dict[str, str]:
        ref = SecretRef.parse(version, version=True)
        if ref.region != connection.region:
            raise ConnectorError("runtime-region", "consumer and secret must use the same region")
        current = await self.current(connection, function)
        config = await self._call(connection, function, "get_function_configuration")
        if config.get("LastUpdateStatus") == "InProgress":
            raise ConnectorError("runtime-updating", "consumer is already updating", retryable=True)
        environment = config.get("Environment", {}).get("Variables", {})
        if (
            environment.get("KINU_DEMO_KEY") != ref.arn
            or environment.get("KINU_DEMO_ORGANISATION") != connection.organisation_id
        ):
            raise ConnectorError(
                "runtime-binding", "consumer does not match the assigned vault and workspace"
            )
        marker = f"kinu:{request_id}"
        existing = None
        next_marker: str | None = None
        for _ in range(20):
            versions = await self._call(
                connection,
                function,
                "list_versions_by_function",
                MaxItems=50,
                **({"Marker": next_marker} if next_marker else {}),
            )
            existing = next(
                (
                    item
                    for item in versions.get("Versions", [])
                    if item.get("Description") == marker
                ),
                None,
            )
            next_marker = versions.get("NextMarker")
            if existing or not next_marker:
                break
        if next_marker and not existing:
            raise ConnectorError(
                "runtime-version-limit", "consumer version history exceeds the rotation limit"
            )
        if existing:
            return {
                "candidate_revision": str(existing["Version"]),
                "rollback_revision": current["version"],
                "alias_revision": current["revision"],
            }
        await self._call(
            connection,
            function,
            "update_function_configuration",
            RevisionId=config["RevisionId"],
            Environment={"Variables": environment | {"KINU_SECRET_VERSION": ref.version or ""}},
            Description=marker,
        )
        ready = await self._ready(connection, function)
        published = await self._call(
            connection,
            function,
            "publish_version",
            RevisionId=ready["RevisionId"],
            CodeSha256=ready["CodeSha256"],
            Description=marker,
        )
        return {
            "candidate_revision": str(published["Version"]),
            "rollback_revision": current["version"],
            "alias_revision": current["revision"],
        }

    async def health(
        self,
        connection: Connection,
        function: str,
        qualifier: str = "live",
        candidate_version: str | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"live|[1-9][0-9]*", qualifier):
            raise ConnectorError(
                "runtime-qualifier", "select a published consumer version or live alias"
            )
        payload = (
            {"candidate_version": SecretRef.parse(candidate_version, version=True).version}
            if candidate_version
            else {}
        )
        result = await self._call(
            connection,
            function,
            "invoke",
            Qualifier=qualifier,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
        stream = result["Payload"]
        try:
            value = json.loads(await asyncio.to_thread(stream.read, 16_385))
        finally:
            stream.close()
        if (
            result.get("FunctionError")
            or not isinstance(value, dict)
            or value.get("demo") is not True
            or not isinstance(value.get("ok"), bool)
        ):
            raise ConnectorError("runtime-health", "consumer health invocation failed")
        if value["ok"]:
            if not re.fullmatch(
                r"[a-f0-9]{24}", str(value.get("provider_id", ""))
            ) or not re.fullmatch(r"[A-Za-z0-9-]{32,64}", str(value.get("secret_version", ""))):
                raise ConnectorError(
                    "runtime-health", "consumer health result has invalid identifiers"
                )
            return {key: value[key] for key in ("ok", "provider_id", "secret_version", "demo")}
        if value.get("state") not in {
            "setup-required",
            "provider-rejected",
            "provider-unavailable",
            "invalid-provider-result",
        }:
            raise ConnectorError("runtime-health", "consumer health result has an unknown state")
        return {key: value[key] for key in ("ok", "state", "demo")}
