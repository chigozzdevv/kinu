from asyncio import sleep
from typing import Any

from connectors.base.errors import ConnectorError
from connectors.hosting.base import Hosting
from connectors.hosting.models import Deployment
from executor.versions import Versions
from rotation.guard import validate
from rotation.state import Snapshot, Target

from hosting.proof import Proof, fingerprint
from hosting.service import Service


class Rollout:
    def __init__(self, hosts: Service, versions: Versions, proof: Proof) -> None:
        self.hosts, self.versions, self.proof = hosts, versions, proof
        self.rows = hosts.database.collection("host_rollouts")

    async def adapter(self, target: Target) -> Hosting:
        grant, connection = await self.hosts.account(
            target.runtime.organisation_id, target.runtime.id
        )
        if connection != target.runtime:
            raise ConnectorError("host-changed", "Hosting connection changed during rotation")
        return self.hosts.adapter(grant)

    async def wait(
        self,
        state: Snapshot,
        created: dict[str, Any],
        adapter: Hosting,
        target: Target,
        deployment: Deployment,
    ) -> Deployment:
        assert target.variable is not None
        for _ in range(60):
            await validate(self.hosts.database, state, created)
            current = await adapter.status(target.variable, deployment.id)
            if current.state == "ready":
                return current
            if current.state == "failed":
                raise ConnectorError("host-deployment", "Replacement deployment failed")
            await sleep(2)
        raise ConnectorError("host-deployment", "Replacement deployment did not become ready")

    async def deploy(
        self, state: Snapshot, created: dict[str, Any], run_id: str, initial: dict[str, Any]
    ) -> dict[str, Any]:
        outputs = {}
        for target in state.targets:
            assert target.variable is not None
            adapter = await self.adapter(target)
            current = await adapter.current(target.variable)
            expected = initial["deployments"][target.binding.id]
            if current.id != expected["version"]:
                raise ConnectorError("host-changed", "Active deployment changed before rotation")
            query = {
                "organisation_id": state.credential.organisation_id,
                "run_id": run_id,
                "binding_id": target.binding.id,
            }
            await validate(self.hosts.database, state, created)
            await self.rows.insert_one(
                {**query, "old": current.model_dump(mode="json"), "phase": "prepared"}
            )
            with await self.versions.access_for(state.store, created["secret_reference"]) as value:
                await adapter.write(target.variable, value)
            await self.rows.update_one(query, {"$set": {"phase": "written"}})
            await validate(self.hosts.database, state, created)
            candidate = await adapter.deploy(target.variable, current, run_id)
            await self.rows.update_one(
                query,
                {"$set": {"phase": "deploying", "candidate": candidate.model_dump(mode="json")}},
            )
            candidate = await self.wait(state, created, adapter, target, candidate)
            outputs[target.binding.id] = {
                "candidate_revision": candidate.id,
                "rollback_revision": current.id,
                "url": candidate.url,
                "rollback_url": current.url,
            }
            await self.rows.update_one(
                query, {"$set": {"phase": "ready", "output": outputs[target.binding.id]}}
            )
        return {"deployments": outputs}

    async def verify(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> None:
        for target in state.targets:
            assert target.variable is not None
            adapter = await self.adapter(target)
            output = deployment["deployments"][target.binding.id]
            candidate = await adapter.status(target.variable, output["candidate_revision"])
            current = await adapter.current(target.variable)
            if candidate.state != "ready" or current.id != candidate.id:
                raise ConnectorError("host-changed", "Replacement is not the active deployment")
            await self.proof.check(target.service, target.variable.name, created["fingerprint"])
            value = await adapter.read(target.variable)
            if value:
                with value:
                    if fingerprint(value.bytes()) != created["fingerprint"]:
                        raise ConnectorError(
                            "host-changed", "Selected variable changed after deployment"
                        )

    async def rollback(self, state: Snapshot, created: dict[str, Any], run_id: str) -> bool:
        restored = True
        for target in reversed(state.targets):
            row = await self.rows.find_one(
                {
                    "organisation_id": state.credential.organisation_id,
                    "run_id": run_id,
                    "binding_id": target.binding.id,
                }
            )
            if not row:
                continue
            assert target.variable is not None
            adapter = await self.adapter(target)
            await validate(self.hosts.database, state, created)
            current, old = (
                await adapter.current(target.variable),
                Deployment.model_validate(row["old"]),
            )
            candidate = row.get("candidate", {}).get("id")
            if current.id not in {old.id, candidate}:
                return False
            if not await self.versions.has_value(state.store, state.credential.secret_reference):
                if current.id != old.id:
                    await self.wait(
                        state,
                        created,
                        adapter,
                        target,
                        await adapter.restore(target.variable, old.id),
                    )
                restored = False
                continue
            current_value = await adapter.read(target.variable)
            if current_value is None:
                restored = False
                continue
            with current_value:
                if fingerprint(current_value.bytes()) not in {
                    created["fingerprint"],
                    row.get("old_fingerprint", ""),
                }:
                    with await self.versions.access_for(
                        state.store, state.credential.secret_reference
                    ) as old_value:
                        if current_value.bytes() != old_value.bytes():
                            return False
            with await self.versions.access_for(
                state.store, state.credential.secret_reference
            ) as old_value:
                await adapter.write(target.variable, old_value)
                expected = fingerprint(old_value.bytes())
            if current.id != old.id:
                await self.wait(
                    state, created, adapter, target, await adapter.restore(target.variable, old.id)
                )
            await self.proof.check(target.service, target.variable.name, expected)
        return restored
