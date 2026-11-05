from asyncio import sleep
from datetime import UTC, datetime
from typing import Any

from connectors.aws import AwsApi
from connectors.aws.runtime import AwsRuntime
from connectors.aws.secrets import AwsSecrets
from connectors.base import ConnectorContext
from connectors.base.errors import ConnectorError
from connectors.http import HttpProviderConnector
from connectors.secrets.store import SecretReader
from contracts import RotationRun, Stage
from core.connection.roles import ConnectionRoles
from core.connection.secrets import ConnectionSecrets
from core.errors import ResourceNotFoundError
from core.inventory.store import InventoryStore
from core.storage.database import Database
from core.storage.runs import RunStore

from rotation.journal import Journal
from rotation.ports import Progress
from rotation.stages import stable
from rotation.state import Snapshot


class Effects:
    def __init__(
        self, aws: AwsApi, database: Database, authorizations: SecretReader | None = None
    ) -> None:
        self.database = database
        roles = ConnectionRoles(database, managed=True)
        self.secrets = AwsSecrets(aws, roles)
        self.runtime = AwsRuntime(aws, roles)
        self.provider = HttpProviderConnector(
            authorizations or ConnectionSecrets(InventoryStore(database), self.secrets)
        )

    async def metadata(self, state: Snapshot, identity: str) -> dict[str, Any]:
        matches = [
            item
            for item in await self.provider.metadata(state.provider)
            if item["provider_id"] == identity
        ]
        if len(matches) != 1:
            raise ConnectorError("provider-identity", "provider credential is missing or ambiguous")
        return matches[0]

    async def initial(self, state: Snapshot) -> dict[str, Any]:
        async def noop(_: str) -> None:
            pass

        return await self.initial_with_progress(state, noop)

    async def initial_with_progress(self, state: Snapshot, progress: Progress) -> dict[str, Any]:
        metadata = await self.provider.metadata(state.provider)
        await progress("provider-checked")
        current = await self.metadata(state, state.current.provider_id or "")
        _active(current, state)
        alias = await self.runtime.current(
            state.single.runtime, state.single.binding.runtime_resource
        )
        _healthy(
            await self.health(state, "live"),
            state.current.provider_id,
            state.credential.secret_reference,
        )
        await self.identity(
            state, state.current.provider_id or "", state.credential.secret_reference
        )
        await self.current_secret(state, state.credential.secret_reference)
        await progress("credential-checked")
        await progress("apps-checked")
        return {
            "deployments": {state.single.binding.id: alias},
            "provider_ids": [item["provider_id"] for item in metadata],
        }

    async def issue(self, state: Snapshot, run_id: str) -> dict[str, Any]:
        run = await RunStore(self.database).get(state.credential.organisation_id, run_id)
        response = await self.provider.execute(
            "provider.createCredential",
            {"name": stable("kinu", run_id), "scopes": sorted(state.credential.scopes)},
            _context(state, run),
        )
        if response.secret is None:
            raise ConnectorError("provider-create", "provider returned no replacement credential")
        with response.secret as value:
            identity = response.result["provider_id"]
            initial = await Journal(self.database).get(
                state.credential.organisation_id, run_id, Stage.PREFLIGHT
            )
            if initial is None or identity in initial.output["provider_ids"]:
                raise ConnectorError(
                    "provider-create", "provider did not create a separate credential"
                )
            _active(await self.metadata(state, identity), state)
            stored = await self.secrets.add_version_for(
                state.store, state.credential.secret_resource, value
            )
        return {"provider_id": identity, "secret_reference": stored["secret_reference"]}

    async def health(
        self, state: Snapshot, qualifier: str, candidate: str | None = None
    ) -> dict[str, Any]:
        return await self.runtime.health(
            state.single.runtime, state.single.binding.runtime_resource, qualifier, candidate
        )

    async def identity(self, state: Snapshot, identity: str, reference: str) -> None:
        actual = await self.provider.credential_identity(state.provider, state.store, reference)
        if actual != identity:
            raise ConnectorError("provider-identity", "stored key identifies another credential")

    async def current_secret(self, state: Snapshot, reference: str) -> None:
        versions = await self.secrets.versions_for(state.store, state.credential.secret_resource)
        if not any(
            item["name"] == reference and "AWSCURRENT" in item["stages"] for item in versions
        ):
            raise ConnectorError("secret-current", "active secret version changed")

    async def access(self, state: Snapshot, created: dict[str, Any]) -> dict[str, Any]:
        await self.identity(
            state, state.current.provider_id or "", state.credential.secret_reference
        )
        await self.identity(state, created["provider_id"], created["secret_reference"])
        result = await self.health(state, "live", created["secret_reference"])
        _healthy(result, created["provider_id"], created["secret_reference"])
        return result

    async def deploy(self, state: Snapshot, created: dict[str, Any], run_id: str) -> dict[str, Any]:
        return {
            "deployments": {
                state.single.binding.id: await self.runtime.candidate(
                    state.single.runtime,
                    state.single.binding.runtime_resource,
                    created["secret_reference"],
                    run_id,
                    state.single.binding.runtime_secret_name,
                    state.credential.secret_reference,
                )
            }
        }

    async def verify(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> dict[str, Any]:
        _active(await self.metadata(state, created["provider_id"]), state)
        await self.identity(state, created["provider_id"], created["secret_reference"])
        result = await self.health(
            state, deployment["deployments"][state.single.binding.id]["candidate_revision"]
        )
        _healthy(result, created["provider_id"], created["secret_reference"])
        return result

    async def promote(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> dict[str, Any]:
        await self.runtime.promote(
            state.single.runtime,
            state.single.binding.runtime_resource,
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

    async def verify_current(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> None:
        await self.verify(state, created, deployment)
        await self.current_secret(state, created["secret_reference"])
        current = await self.runtime.current(
            state.single.runtime, state.single.binding.runtime_resource
        )
        if (
            current["version"]
            != deployment["deployments"][state.single.binding.id]["candidate_revision"]
        ):
            raise ConnectorError("runtime-current", "The active Lambda deployment changed")
        _healthy(
            await self.health(state, "live"), created["provider_id"], created["secret_reference"]
        )

    async def observe(self, state: Snapshot, created: dict[str, Any]) -> dict[str, Any]:
        seconds = state.controls.definition.maximum_observation_seconds
        samples = 3
        for _ in range(samples):
            await sleep(seconds / samples)
            _healthy(
                await self.health(state, "live"),
                created["provider_id"],
                created["secret_reference"],
            )
            await self.current_secret(state, created["secret_reference"])
        return {"observation_seconds": seconds, "samples": samples, "consumer_healthy": True}

    async def revoke(
        self,
        state: Snapshot,
        created: dict[str, Any],
        deployment: dict[str, Any],
        observed: dict[str, Any],
    ) -> dict[str, Any]:
        await self.verify(state, created, deployment)
        await self.current_secret(state, created["secret_reference"])
        alias = await self.runtime.current(
            state.single.runtime, state.single.binding.runtime_resource
        )
        if (
            alias["version"]
            != deployment["deployments"][state.single.binding.id]["candidate_revision"]
            or observed.get("consumer_healthy") is not True
        ):
            raise ConnectorError("runtime-current", "consumer changed before retirement")
        _healthy(
            await self.health(state, "live"), created["provider_id"], created["secret_reference"]
        )
        await self._revoke(state, state.current.provider_id or "")
        with await self.secrets.access_for(state.store, state.credential.secret_reference) as value:
            status = await self.provider.test_credential(state.provider, value)
        if status not in {401, 403}:
            raise ConnectorError(
                "provider-retirement", "provider did not reject the retired credential"
            )
        old = await self.health(
            state, deployment["deployments"][state.single.binding.id]["rollback_revision"]
        )
        if old.get("ok") is not False or old.get("state") != "provider-rejected":
            raise ConnectorError(
                "runtime-retirement", "old consumer still accepts the retired credential"
            )
        _healthy(
            await self.health(state, "live"), created["provider_id"], created["secret_reference"]
        )
        await self.secrets.disable_for(state.store, state.credential.secret_reference)
        return {"old_rejected": True, "replacement_valid": True, "old_secret_retired": True}

    async def _revoke(self, state: Snapshot, identity: str) -> None:
        await self.provider.revoke_credential(state.provider, identity)

    async def cleanup_unrecorded(self, state: Snapshot, run_id: str) -> bool:
        initial = await Journal(self.database).get(
            state.credential.organisation_id, run_id, Stage.PREFLIGHT
        )
        if initial is None:
            return False
        run = await RunStore(self.database).get(state.credential.organisation_id, run_id)
        _healthy(
            await self.health(state, "live"),
            state.current.provider_id,
            state.credential.secret_reference,
        )
        await self.current_secret(state, state.credential.secret_reference)
        await self.provider.reconcile(
            "provider.createCredential",
            {"name": stable("kinu", run_id)},
            {"name": stable("kinu", run_id), "before_ids": tuple(initial.output["provider_ids"])},
            _context(state, run),
        )
        return True

    async def rollback(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any] | None
    ) -> bool:
        old = await self.metadata(state, state.current.provider_id or "")
        _active(old, state)
        current = await self.runtime.current(
            state.single.runtime, state.single.binding.runtime_resource
        )
        if (
            deployment
            and current["version"]
            != deployment["deployments"][state.single.binding.id]["rollback_revision"]
        ):
            if (
                current["version"]
                != deployment["deployments"][state.single.binding.id]["candidate_revision"]
            ):
                return False
            await self.runtime.promote(
                state.single.runtime,
                state.single.binding.runtime_resource,
                deployment["deployments"][state.single.binding.id]["rollback_revision"],
                current["version"],
                current["revision"],
            )
        versions = await self.secrets.versions_for(state.store, state.credential.secret_resource)
        active = next(item["name"] for item in versions if "AWSCURRENT" in item["stages"])
        if active == created["secret_reference"]:
            await self.secrets.promote(state.store, state.credential.secret_reference, active)
        elif active != state.credential.secret_reference:
            return False
        _healthy(
            await self.health(state, "live"),
            state.current.provider_id,
            state.credential.secret_reference,
        )
        await self.runtime.restore_draft(
            state.single.runtime,
            state.single.binding.runtime_resource,
            state.single.binding.runtime_secret_name,
            created["secret_reference"],
            state.credential.secret_reference,
        )
        await retire_replacement(self.provider, self.secrets, state, created)
        await self.secrets.disable_for(state.store, created["secret_reference"])
        return True


async def retire_replacement(
    provider: HttpProviderConnector,
    secrets: SecretReader,
    state: Snapshot,
    created: dict[str, Any],
) -> None:
    matches = [
        item
        for item in await provider.metadata(state.provider)
        if item["provider_id"] == created["provider_id"]
    ]
    if len(matches) > 1:
        raise ConnectorError("provider-identity", "Replacement identity is ambiguous")
    if (
        matches
        and matches[0].get("disabled") is not True
        and matches[0].get("status") not in {"disabled", "revoked", "inactive", "deleted"}
    ):
        _active(matches[0], state)
        await provider.revoke_credential(state.provider, created["provider_id"])
    try:
        value = await secrets.access_for(state.store, created["secret_reference"])
    except ResourceNotFoundError:
        if any(
            item["provider_id"] == created["provider_id"]
            and item.get("disabled") is not True
            and item.get("status") not in {"disabled", "revoked", "inactive", "deleted"}
            for item in await provider.metadata(state.provider)
        ):
            raise ConnectorError(
                "provider-retirement", "Replacement retirement is unverified"
            ) from None
        return
    with value:
        if await provider.test_credential(state.provider, value) not in {401, 403}:
            raise ConnectorError("provider-retirement", "Replacement key is still accepted")


def _context(state: Snapshot, run: RotationRun) -> ConnectorContext:
    return ConnectorContext(
        run.id, "worker_executor", state.provider, run, datetime.now(UTC), run.id
    )


def _active(metadata: dict[str, Any], state: Snapshot) -> None:
    if (
        metadata.get("disabled") is True
        or metadata.get("status") not in {"active", "enabled"}
        or frozenset(metadata.get("scopes", ())) != state.credential.scopes
        or metadata.get("kind") != state.credential.kind
    ):
        raise ConnectorError("provider-state", "provider credential state, kind or scopes differ")


def _healthy(value: dict[str, Any], identity: str | None, reference: str) -> None:
    if (
        value.get("ok") is not True
        or value.get("provider_id") != identity
        or value.get("secret_reference") != reference
    ):
        raise ConnectorError(
            "runtime-health", "consumer did not verify the selected credential version"
        )
