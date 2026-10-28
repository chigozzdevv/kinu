from asyncio import sleep
from datetime import UTC, datetime
from typing import Any

from connectors.base import ConnectorContext
from connectors.base.errors import ConnectorError
from connectors.http import HttpProviderConnector
from contracts import Stage
from executor.versions import Versions
from hosting.locks import acquire
from hosting.proof import Proof, fingerprint
from hosting.rollout import Rollout
from hosting.service import Service

from rotation.effects import _active
from rotation.guard import validate
from rotation.journal import Journal
from rotation.stages import stable
from rotation.state import Snapshot


class Effects:
    def __init__(
        self,
        hosts: Service,
        versions: Versions,
        provider: HttpProviderConnector,
        proof: Proof | None = None,
    ) -> None:
        self.hosts, self.versions, self.provider = hosts, versions, provider
        self.proof = proof or Proof()
        self.rollout = Rollout(hosts, versions, self.proof)
        self.journal = Journal(hosts.database)

    async def metadata(self, state: Snapshot, identity: str) -> dict[str, Any]:
        matches = [
            item
            for item in await self.provider.metadata(state.provider)
            if item["provider_id"] == identity
        ]
        if len(matches) != 1:
            raise ConnectorError("provider-identity", "Provider credential is missing or ambiguous")
        return matches[0]

    async def identity(self, state: Snapshot, created: dict[str, Any]) -> None:
        if (
            await self.provider.credential_identity(
                state.provider, state.store, created["secret_reference"]
            )
            != created["provider_id"]
        ):
            raise ConnectorError("provider-identity", "Stored replacement identifies another key")

    async def initial(self, state: Snapshot) -> dict[str, Any]:
        from core.storage.runs import RunStore

        lock = await self.hosts.database.collection("run_locks").find_one(
            {
                "organisation_id": state.credential.organisation_id,
                "credential_id": state.credential.id,
            }
        )
        if not lock:
            raise ConnectorError("host-lease", "Rotation does not hold its credential lock")
        run = await RunStore(self.hosts.database).get(
            state.credential.organisation_id, lock["run_id"]
        )
        await validate(
            self.hosts.database, state, {"run_id": run.id, "fencing_token": run.fencing_token}
        )
        await acquire(self.hosts.database, state, run.id)
        await self.provider.preflight(state.provider, run)
        if (
            await self.versions.current(state.store, state.credential.secret_resource)
            != state.credential.secret_reference
        ):
            raise ConnectorError("secret-current", "Current secret generation changed")
        expected = None
        readable = await self.versions.has_value(state.store, state.credential.secret_reference)
        if state.playbook is not None and not readable:
            raise ConnectorError(
                "browser-probe", "Browser rotation requires a readable predecessor"
            )
        if readable:
            with await self.versions.access_for(
                state.store, state.credential.secret_reference
            ) as value:
                expected = fingerprint(value.bytes())
            await self.identity(
                state,
                {
                    "provider_id": state.current.provider_id,
                    "secret_reference": state.credential.secret_reference,
                },
            )
        metadata = await self.provider.metadata(state.provider)
        _active(await self.metadata(state, state.current.provider_id or ""), state)
        management_fingerprint = await self.provider.auth_fingerprint(state.provider)
        deployments = {}
        for target in state.targets:
            assert target.variable is not None
            adapter = await self.rollout.adapter(target)
            available = await adapter.variables(target.variable.project_id)
            if not any(
                item.reference == target.variable.reference and item.name == target.variable.name
                for item in available
            ):
                raise ConnectorError(
                    "host-variable", "Selected variable changed; review its connection mapping"
                )
            current = await adapter.current(target.variable)
            if current.state != "ready" or (not readable and not current.url):
                raise ConnectorError(
                    "host-proof", "Current deployment has no retirement verification path"
                )
            actual = await self.proof.check(
                target.service,
                target.variable.name,
                expected,
                provider_id=state.current.provider_id if not readable else None,
            )
            if actual == management_fingerprint:
                raise ConnectorError(
                    "provider-management", "Use a separate provider management credential"
                )
            expected = actual
            deployments[target.binding.id] = {
                "version": current.id,
                "url": current.url,
                "fingerprint": actual,
            }
        return {
            "deployments": deployments,
            "provider_ids": [item["provider_id"] for item in metadata],
        }

    async def issue(self, state: Snapshot, run_id: str) -> dict[str, Any]:
        from core.storage.runs import RunStore

        run = await RunStore(self.hosts.database).get(state.credential.organisation_id, run_id)
        await validate(
            self.hosts.database, state, {"run_id": run_id, "fencing_token": run.fencing_token}
        )
        response = await self.provider.execute(
            "provider.createCredential",
            {"name": stable("kinu", run_id), "scopes": sorted(state.credential.scopes)},
            ConnectorContext(
                run_id, "worker_executor", state.provider, run, datetime.now(UTC), run_id
            ),
        )
        if response.secret is None:
            raise ConnectorError("provider-create", "Provider returned no replacement key")
        with response.secret as value:
            initial = await self.journal.get(run.organisation_id, run.id, Stage.PREFLIGHT)
            if initial is None or response.result["provider_id"] in initial.output["provider_ids"]:
                raise ConnectorError("provider-create", "Provider did not create a separate key")
            if any(
                item["fingerprint"] == fingerprint(value.bytes())
                for item in initial.output["deployments"].values()
            ):
                raise ConnectorError("provider-create", "Replacement reused the previous secret")
            _active(await self.metadata(state, response.result["provider_id"]), state)
            reference = await self.versions.record(
                state.store, state.credential.secret_resource, stable("version", run_id), value
            )
            return {
                "provider_id": response.result["provider_id"],
                "secret_reference": reference,
                "fingerprint": fingerprint(value.bytes()),
                "run_id": run_id,
                "fencing_token": run.fencing_token,
            }

    async def access(self, state: Snapshot, created: dict[str, Any]) -> dict[str, Any]:
        await self.identity(state, created)
        return {"stored": True, "plaintext_isolated": True}

    async def deploy(self, state: Snapshot, created: dict[str, Any], run_id: str) -> dict[str, Any]:
        initial = await self.journal.get(state.credential.organisation_id, run_id, Stage.PREFLIGHT)
        if not initial:
            raise ConnectorError("host-state", "Preflight evidence is unavailable")
        return await self.rollout.deploy(state, created, run_id, initial.output)

    async def verify(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> dict[str, Any]:
        await self.identity(state, created)
        _active(await self.metadata(state, created["provider_id"]), state)
        await self.rollout.verify(state, created, deployment)
        return {"consumer_healthy": True, "consumers": len(state.targets)}

    async def promote(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> dict[str, Any]:
        await self.verify(state, created, deployment)
        await self.versions.promote(
            state.store, created["secret_reference"], state.credential.secret_reference
        )
        return {"promoted": True}

    async def observe(self, state: Snapshot, created: dict[str, Any]) -> dict[str, Any]:
        deployed = await self.journal.get(
            state.credential.organisation_id, created["run_id"], Stage.DEPLOY
        )
        if not deployed:
            raise ConnectorError("host-state", "Deployment evidence is unavailable")
        for _ in range(3):
            await sleep(state.controls.definition.maximum_observation_seconds / 3)
            await self.verify(state, created, deployed.output)
        return {"consumer_healthy": True, "samples": 3, "consumers": len(state.targets)}

    async def revoke(
        self,
        state: Snapshot,
        created: dict[str, Any],
        deployment: dict[str, Any],
        observed: dict[str, Any],
    ) -> dict[str, Any]:
        await self.verify(state, created, deployment)
        if (
            observed.get("consumer_healthy") is not True
            or await self.versions.current(state.store, state.credential.secret_resource)
            != created["secret_reference"]
        ):
            raise ConnectorError("host-state", "Current generation or observation changed")
        _active(await self.metadata(state, state.current.provider_id or ""), state)
        await validate(self.hosts.database, state, created)
        await self.provider.revoke_credential(state.provider, state.current.provider_id or "")
        if await self.versions.has_value(state.store, state.credential.secret_reference):
            with await self.versions.access_for(
                state.store, state.credential.secret_reference
            ) as value:
                if await self.provider.test_credential(state.provider, value) not in {401, 403}:
                    raise ConnectorError("provider-retirement", "Retired key was not rejected")
        else:
            initial = await self.journal.get(
                state.credential.organisation_id, created["run_id"], Stage.PREFLIGHT
            )
            assert initial is not None
            for target in state.targets:
                old = initial.output["deployments"][target.binding.id]
                await self.proof.check(
                    target.service,
                    target.binding.runtime_secret_name,
                    old["fingerprint"],
                    origin=old["url"],
                    rejected=True,
                )
        await self.verify(state, created, deployment)
        await self.versions.disable_for(state.store, state.credential.secret_reference)
        return {"old_rejected": True, "replacement_valid": True, "old_secret_retired": True}

    async def rollback(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any] | None
    ) -> bool:
        _active(await self.metadata(state, state.current.provider_id or ""), state)
        if not await self.rollout.rollback(state, created, created["run_id"]):
            return False
        current = await self.versions.current(state.store, state.credential.secret_resource)
        if current == created["secret_reference"]:
            await self.versions.promote(state.store, state.credential.secret_reference, current)
        elif current != state.credential.secret_reference:
            return False
        await validate(self.hosts.database, state, created)
        await self.provider.revoke_credential(state.provider, created["provider_id"])
        await self.versions.disable_for(state.store, created["secret_reference"])
        return True

    async def cleanup_unrecorded(self, state: Snapshot, run_id: str) -> bool:
        if state.playbook is not None:
            run = await self.hosts.database.collection("runs").find_one(
                {"organisation_id": state.credential.organisation_id, "id": run_id}
            )
            if not run:
                return False
            await validate(
                self.hosts.database,
                state,
                {"run_id": run_id, "fencing_token": run["data"]["fencing_token"]},
            )
            return (
                await self.hosts.database.collection("browser_actions").find_one(
                    {
                        "organisation_id": state.credential.organisation_id,
                        "session_id": stable("browser", run_id + ":create:"),
                        "action.protected": True,
                    }
                )
                is None
            )
        # An unknown issuer outcome needs review before another mutation.
        return False
