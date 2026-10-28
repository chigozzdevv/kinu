import asyncio
import json
import re
import secrets
from typing import Any

from contracts import Connection

from connectors.aws.lambdas import LambdaRuntime
from connectors.aws.refs import SecretRef
from connectors.base.errors import ConnectorError


class AwsRuntime(LambdaRuntime):
    async def restore_draft(
        self, connection: Connection, function: str, binding: str, candidate: str, previous: str
    ) -> None:
        config = await self._call(connection, function, "get_function_configuration")
        environment = config.get("Environment", {}).get("Variables", {})
        if environment.get(binding) == previous:
            return
        if environment.get(binding) != candidate:
            raise ConnectorError("runtime-draft", "consumer draft changed before recovery")
        await self._call(
            connection,
            function,
            "update_function_configuration",
            RevisionId=config["RevisionId"],
            Environment={"Variables": environment | {binding: previous}},
        )
        await self._ready(connection, function)

    async def resources_for(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        result: list[dict[str, Any]] = []
        for function in connection.allowed_resources:
            if not function.startswith("arn:aws:lambda:"):
                continue
            current = await self.current(connection, function)
            config = await self._call(
                connection, function, "get_function_configuration", Qualifier=current["version"]
            )
            bindings: list[dict[str, str]] = []
            for name, value in config.get("Environment", {}).get("Variables", {}).items():
                if not value.startswith("arn:aws:secretsmanager:") or "/versions/" not in value:
                    continue
                ref = SecretRef.parse(value, version=True)
                bindings.append({"name": name, "secret": ref.arn, "version": ref.version or ""})
            result.append(
                {
                    "reference": function,
                    "display_name": config["FunctionName"],
                    "identity": config["Role"],
                    "region": connection.region,
                    "secret_bindings": bindings,
                }
            )
        return tuple(result)

    async def candidate(
        self,
        connection: Connection,
        function: str,
        reference: str,
        request_id: str,
        binding: str,
        previous: str,
    ) -> dict[str, str]:
        ref = SecretRef.parse(reference, version=True)
        old = SecretRef.parse(previous, version=True)
        if ref.arn != old.arn or ref.region != connection.region:
            raise ConnectorError("runtime-binding", "candidate must use the selected secret")
        current = await self.current(connection, function)
        live = await self._call(
            connection, function, "get_function_configuration", Qualifier=current["version"]
        )
        if live.get("Environment", {}).get("Variables", {}).get(binding) != previous:
            raise ConnectorError("runtime-binding", "live consumer secret binding changed")
        config = await self._call(connection, function, "get_function_configuration")
        environment = config.get("Environment", {}).get("Variables", {})
        if environment.get(binding) not in {previous, reference}:
            raise ConnectorError("runtime-binding", "consumer draft secret binding changed")
        if config.get("CodeSha256") != live.get("CodeSha256"):
            raise ConnectorError("runtime-code", "publish the consumer's pending code first")
        if config.get("LastUpdateStatus") == "InProgress":
            raise ConnectorError("runtime-updating", "consumer is already updating")
        marker = f"kinu:{request_id}"
        await self._call(
            connection,
            function,
            "update_function_configuration",
            RevisionId=config["RevisionId"],
            Environment={"Variables": environment | {binding: reference}},
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
        candidate: str | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"live|[1-9][0-9]*", qualifier):
            raise ConnectorError("runtime-qualifier", "published version or live alias required")
        challenge = secrets.token_urlsafe(24)
        probe = {"operation": "health", "challenge": challenge}
        if candidate:
            ref = SecretRef.parse(candidate, version=True)
            probe["secret_reference"] = ref.reference(ref.version or "")
        result = await self._call(
            connection,
            function,
            "invoke",
            Qualifier=qualifier,
            InvocationType="RequestResponse",
            Payload=json.dumps({"kinu": probe}).encode(),
        )
        stream = result["Payload"]
        try:
            value = json.loads(await asyncio.to_thread(stream.read, 16_385))
        finally:
            stream.close()
        data = value.get("kinu") if isinstance(value, dict) else None
        if (
            result.get("FunctionError")
            or not isinstance(data, dict)
            or data.get("challenge") != challenge
            or not isinstance(data.get("healthy"), bool)
        ):
            raise ConnectorError(
                "runtime-health", "consumer must implement the Kinu health contract"
            )
        if data["healthy"]:
            reference, identity = data.get("secret_reference"), data.get("provider_id")
            if (
                not isinstance(reference, str)
                or not isinstance(identity, str)
                or not 1 <= len(identity) <= 256
            ):
                raise ConnectorError(
                    "runtime-health", "consumer returned invalid credential metadata"
                )
            ref = SecretRef.parse(reference, version=True)
            return {
                "ok": True,
                "provider_id": identity,
                "secret_reference": reference,
                "secret_version": ref.version,
            }
        if data.get("reason") != "credential-rejected":
            raise ConnectorError("runtime-health", "consumer could not verify provider access")
        return {"ok": False, "state": "provider-rejected"}
