import asyncio
import hashlib
from typing import Any

from connectors.aws import AwsApi
from connectors.aws.refs import SecretRef
from connectors.aws.secrets import AwsSecrets
from connectors.base.errors import ConnectorError
from contracts import Connection
from core.connection.roles import ConnectionRoles
from core.storage.database import Database
from core.storage.records import Records
from rotation.state import Snapshot

from demo.config import DemoConfig
from demo.rpc import invoke
from demo.runtime import DemoRuntime


class Effects:
    def __init__(self, aws: AwsApi, database: Database, config: DemoConfig) -> None:
        self.aws, self.config, self.database = aws, config, database
        roles = ConnectionRoles(database)
        self.runtime = DemoRuntime(aws, roles)
        self.secrets = AwsSecrets(aws, roles)

    async def metadata(self, provider_id: str) -> dict[str, Any]:
        response = await self.provider("metadata", provider_id=provider_id)
        value = response.get("key")
        if not isinstance(value, dict) or value.get("id") != provider_id:
            raise ConnectorError("demo-key-missing", "The demo provider key is unavailable")
        return value

    async def cleanup_unrecorded(self, state: Snapshot, run_id: str) -> bool:
        org = self.config.organisation_id
        token = hashlib.sha256(f"{org}\0{run_id}".encode()).hexdigest()
        result = await self.provider("metadata", provider_id=token[:24])
        if result.get("key") is None:
            return True
        _healthy(
            await self.health(state, "live"),
            state.current.provider_id,
            state.credential.secret_reference,
        )
        versions = await self.secrets.versions_for(state.store, state.credential.secret_resource)
        current = next(item["name"] for item in versions if "AWSCURRENT" in item["stages"])
        if current != state.credential.secret_reference:
            return False
        await self.provider("revoke", provider_id=token[:24])
        reference = state.credential.secret_resource + "/versions/" + token
        if any(item["name"] == reference for item in versions):
            await self.secrets.disable_for(state.store, reference)
        return True

    async def provider(self, operation: str, **parameters: Any) -> dict[str, Any]:
        async with Records(self.database).read() as rows:
            connection = await rows.get(
                Connection, self.config.organisation_id, "connection_demo_provider"
            )
        if (
            connection.rpc is None
            or connection.rpc.function_arn != self.config.provider_function
            or operation not in connection.rpc.operations.values()
        ):
            raise ConnectorError(
                "demo-provider-scope", "Provider operation is outside the declared API"
            )
        return await invoke(
            self.aws,
            self.config.region,
            self.config.provider_function,
            {
                "operation": operation,
                "organisation_id": self.config.organisation_id,
                **parameters,
            },
            role=await ConnectionRoles(self.database).resolve(connection),
        )

    async def initial(self, state: Snapshot) -> dict[str, Any]:
        required = {
            "provider.createCredential",
            "provider.revokeCredential",
            "runtime.updateConfiguration",
            "runtime.health",
            "secretStore.disableVersion",
        }
        if (
            not required <= state.controls.definition.allowed_tools
            or state.controls.definition.maximum_observation_seconds != 60
        ):
            raise ConnectorError("demo-controls", "demo controls do not permit this execution")
        metadata = await self.metadata(state.current.provider_id or "")
        alias = await self.runtime.current(state.single.runtime, self.config.consumer_function)
        health = await self.health(state, "live")
        _healthy(health, state.current.provider_id, state.credential.secret_reference)
        versions = await self.secrets.versions_for(state.store, state.credential.secret_resource)
        if metadata["state"] != "active" or not any(
            item["name"] == state.credential.secret_reference and "AWSCURRENT" in item["stages"]
            for item in versions
        ):
            raise ConnectorError(
                "demo-state-mismatch", "The current provider and secret versions differ"
            )
        return {
            "deployments": {state.single.binding.id: alias},
            "old_uses": metadata["uses"],
            "health": health,
        }

    async def issue(self, state: Snapshot, run_id: str) -> dict[str, Any]:
        result = await self.provider("create", request_id=run_id)
        ref = SecretRef.parse(str(result.get("secret_reference", "")), version=True)
        expected = hashlib.sha256(f"{self.config.organisation_id}\0{run_id}".encode()).hexdigest()[
            :24
        ]
        if (
            ref.arn != state.credential.secret_resource
            or result.get("provider_id") != expected
            or result.get("scopes") != ["demo:read"]
        ):
            raise ConnectorError(
                "demo-creation-mismatch", "Created key is outside the approved mapping"
            )
        return {"provider_id": expected, "secret_reference": ref.reference(ref.version or "")}

    async def health(
        self, state: Snapshot, qualifier: str, candidate: str | None = None
    ) -> dict[str, Any]:
        if (
            candidate
            and SecretRef.parse(candidate, version=True).arn != state.credential.secret_resource
        ):
            raise ConnectorError(
                "demo-secret-boundary", "Consumer probe escaped the assigned secret"
            )
        return await self.runtime.health(
            state.single.runtime, self.config.consumer_function, qualifier, candidate
        )

    async def access(self, state: Snapshot, created: dict[str, Any]) -> dict[str, Any]:
        result = await self.health(state, "live", created["secret_reference"])
        _healthy(result, created["provider_id"], created["secret_reference"])
        return result

    async def deploy(self, state: Snapshot, created: dict[str, Any], run_id: str) -> dict[str, Any]:
        return {
            "deployments": {
                state.single.binding.id: await self.runtime.candidate(
                    state.single.runtime,
                    self.config.consumer_function,
                    created["secret_reference"],
                    run_id,
                )
            }
        }

    async def verify(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> dict[str, Any]:
        result = await self.health(
            state, deployment["deployments"][state.single.binding.id]["candidate_revision"]
        )
        _healthy(result, created["provider_id"], created["secret_reference"])
        metadata = await self.metadata(created["provider_id"])
        if metadata["state"] != "active" or metadata["uses"] < 1:
            raise ConnectorError(
                "demo-candidate-invalid", "The replacement has no successful provider usage"
            )
        return result

    async def promote(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> dict[str, Any]:
        await self.runtime.promote(
            state.single.runtime,
            self.config.consumer_function,
            deployment["deployments"][state.single.binding.id]["candidate_revision"],
            deployment["deployments"][state.single.binding.id]["rollback_revision"],
            deployment["deployments"][state.single.binding.id]["alias_revision"],
        )
        await self.secrets.promote(
            state.store, created["secret_reference"], state.credential.secret_reference
        )
        result = await self.health(state, "live")
        _healthy(result, created["provider_id"], created["secret_reference"])
        return result

    async def observe(self, state: Snapshot, created: dict[str, Any]) -> dict[str, Any]:
        old = await self.metadata(state.current.provider_id or "")
        for _ in range(3):
            await asyncio.sleep(20)
            result = await self.health(state, "live")
            _healthy(result, created["provider_id"], created["secret_reference"])
            if (await self.metadata(state.current.provider_id or ""))["uses"] != old["uses"]:
                raise ConnectorError(
                    "demo-old-key-in-use", "Old credential activity continued after rollout"
                )
        return {"observation_seconds": 60, "samples": 3, "old_uses": old["uses"]}

    async def revoke(
        self,
        state: Snapshot,
        created: dict[str, Any],
        deployment: dict[str, Any],
        observed: dict[str, Any],
    ) -> dict[str, Any]:
        await self.verify(state, created, deployment)
        _healthy(
            await self.health(state, "live"), created["provider_id"], created["secret_reference"]
        )
        alias = await self.runtime.current(state.single.runtime, self.config.consumer_function)
        versions = await self.secrets.versions_for(state.store, state.credential.secret_resource)
        if alias["version"] != deployment["deployments"][state.single.binding.id][
            "candidate_revision"
        ] or not any(
            item["name"] == created["secret_reference"] and "AWSCURRENT" in item["stages"]
            for item in versions
        ):
            raise ConnectorError(
                "demo-rollout-changed", "The consumer or secret changed before revocation"
            )
        await self.provider(
            "revoke", provider_id=state.current.provider_id, expected_uses=observed["old_uses"]
        )
        old = await self.health(
            state, deployment["deployments"][state.single.binding.id]["rollback_revision"]
        )
        if old.get("ok") is not False or old.get("state") != "provider-rejected":
            raise ConnectorError(
                "demo-old-key-accepted", "Provider did not reject the retired credential"
            )
        result = await self.health(state, "live")
        _healthy(result, created["provider_id"], created["secret_reference"])
        retired = await self.secrets.disable_for(state.store, state.credential.secret_reference)
        if retired.get("state") != "DEPRECATED":
            raise ConnectorError(
                "demo-secret-retirement", "The previous secret version is still staged"
            )
        return {"old_rejected": True, "replacement_valid": True, "old_secret_retired": True}

    async def rollback(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any] | None
    ) -> bool:
        if (await self.metadata(state.current.provider_id or ""))["state"] != "active":
            return False
        if deployment:
            current = await self.runtime.current(
                state.single.runtime, self.config.consumer_function
            )
            if (
                current["version"]
                != deployment["deployments"][state.single.binding.id]["rollback_revision"]
            ):
                if (
                    current["version"]
                    != deployment["deployments"][state.single.binding.id]["candidate_revision"]
                ):
                    return False
                await self.runtime.promote(
                    state.single.runtime,
                    self.config.consumer_function,
                    deployment["deployments"][state.single.binding.id]["rollback_revision"],
                    current["version"],
                    current["revision"],
                )
        versions = await self.secrets.versions_for(state.store, state.credential.secret_resource)
        current_secret = next(item["name"] for item in versions if "AWSCURRENT" in item["stages"])
        if current_secret == created["secret_reference"]:
            await self.secrets.promote(
                state.store, state.credential.secret_reference, current_secret
            )
        elif current_secret != state.credential.secret_reference:
            return False
        _healthy(
            await self.health(state, "live"),
            state.current.provider_id,
            state.credential.secret_reference,
        )
        await self.provider("revoke", provider_id=created["provider_id"])
        await self.secrets.disable_for(state.store, created["secret_reference"])
        return True


def _healthy(value: dict[str, Any], provider_id: str | None, reference: str) -> None:
    if value.get("ok") is not True:
        raise ConnectorError(
            "demo-consumer-" + str(value.get("state", "unhealthy")),
            "Consumer could not verify access to the provider",
            retryable=value.get("state") == "provider-unavailable",
        )
    if (
        value.get("provider_id") != provider_id
        or value.get("secret_version") != SecretRef.parse(reference, version=True).version
    ):
        raise ConnectorError(
            "demo-health-mismatch", "Consumer did not use the expected credential version"
        )
