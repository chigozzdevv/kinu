from datetime import UTC, datetime, timedelta
from typing import Any

from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.hosting.base import Hosting
from connectors.hosting.models import Deployment, Variable
from contracts import Connection, ConnectionInterface, ConnectionRole, ConnectionStatus, RunStatus
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.errors import IdempotencyConflictError, ResourceConflictError, ResourceNotFoundError
from core.inventory.controls import validate_exposure_sources
from jobs.queue import Jobs
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from rotation.stages import stable

from hosting.mapping import Mappings
from hosting.models import Import, Mapping, Provision
from hosting.proof import Proof, fingerprint
from hosting.service import project_ref

Row = dict[str, Any]
_WORKING = {"pending", "stored", "deploying", "registering", "recovering", "restoring"}


class ChangedError(Exception):
    pass


class Provisioning:
    def __init__(
        self, mappings: Mappings, queue: Jobs, proof: Proof | None = None, region: str = "global"
    ) -> None:
        self.mappings, self.queue, self.proof = mappings, queue, proof or Proof()
        self.hosts, self.vault = mappings.hosts, mappings.versions.vault
        self.database = self.hosts.database
        self.rows = self.vault.database.collection("provisioning")
        self.audit = AuditWriter(AuditStore(self.database), region, lambda: datetime.now(UTC))

    async def initialize(self) -> None:
        await self.rows.create_index([("organisation_id", 1), ("id", 1)], unique=True)
        await self.rows.create_index([("active", 1), ("phase", 1)])

    def context(self, row: Row) -> str:
        return f"provision:{row['organisation_id']}:{row['id']}"

    def public(self, row: Row) -> Row:
        return {
            key: row.get(key)
            for key in ("id", "phase", "reference", "project_id", "credential_id", "message")
        }

    async def list(self, org: str, host: str) -> list[Row]:
        return [
            self.public(row)
            async for row in self.rows.find({"organisation_id": org, "host_id": host})
            .sort([("active", -1), ("created_at", -1)])
            .limit(100)
        ]

    async def get(self, org: str, identity: str) -> Row:
        row = await self.rows.find_one({"organisation_id": org, "id": identity})
        if not row:
            raise ResourceNotFoundError("Key setup is unavailable")
        return row

    async def enqueue(self, row: Row) -> None:
        await self.queue.add(
            "provision",
            {"organisation_id": row["organisation_id"], "run_id": row["id"]},
            f"provision-{row['organisation_id']}-{row['id']}",
            recover=True,
        )

    async def poll(self) -> None:
        async for row in self.rows.find({"active": False, "finalized": {"$ne": True}}).limit(100):
            await self.finalize(row)
        async for row in self.rows.find(
            {
                "active": True,
                "phase": {"$in": ["creating", "writing"]},
                "updated_at": {"$lte": datetime.now(UTC) - timedelta(minutes=5)},
            }
        ).limit(100):
            try:
                await self.update(
                    row,
                    phase="review-required",
                    held_phase=row["phase"],
                    message=(
                        "Key setup was interrupted. "
                        "Check the provider and workload before recovery."
                    ),
                )
            except ChangedError:
                continue
        async for row in self.rows.find({"active": True, "phase": {"$in": sorted(_WORKING)}}).limit(
            100
        ):
            await self.enqueue(row)

    async def request(
        self, org: str, host_id: str, actor: str, identity: str, body: Provision
    ) -> Row:
        existing = await self.rows.find_one({"organisation_id": org, "id": identity})
        if existing:
            if (
                existing["host_id"] != host_id
                or Provision.model_validate(existing["request"]) != body
            ):
                raise IdempotencyConflictError(
                    "Key setup request already identifies another operation"
                )
            if existing["phase"] in _WORKING:
                await self.enqueue(existing)
            return self.public(existing)
        grant, host = await self.hosts.account(org, host_id)
        if not host or host.status is not ConnectionStatus.READY or host.archived_at:
            raise ResourceConflictError("Reconnect the hosting account before creating a key")
        provider = await self.hosts.inventory.get_connection(org, body.provider_connection_id)
        self.provider_ready(provider)
        if body.initial_controls and body.initial_controls.exposure_sources:
            validate_exposure_sources(
                await self.hosts.inventory.connections(org), body.initial_controls
            )
        scopes = self.mappings.provider.creation_scopes(provider, body.scopes)
        provider_ids = [
            item["provider_id"] for item in await self.mappings.provider.metadata(provider)
        ]
        variables = await self.hosts.variables(org, host_id, body.project_id)
        selected = next(
            (item for item in variables if item.reference == body.variable_reference), None
        )
        if not selected or not selected.readable:
            raise ResourceConflictError(
                "Choose a readable workload variable for the first managed key"
            )
        if await self.database.collection("host_bindings").find_one(
            {"organisation_id": org, "connection_id": host_id, "reference": selected.reference}
        ):
            raise ResourceConflictError("This variable is already managed; rotate its existing key")
        adapter = self.hosts.adapter(grant)
        previous = await adapter.current(selected)
        if previous.state != "ready":
            raise ResourceConflictError(
                "Wait for the workload's current deployment to become ready"
            )
        value = await adapter.read(selected)
        if value is None:
            raise ResourceConflictError("The current variable cannot be read safely")
        with value:
            expected = fingerprint(value.bytes())
            rejected = False
            try:
                await self.proof.endpoint(body.health_url, selected.name, expected)
            except ConnectorError:
                await self.proof.endpoint(body.health_url, selected.name, expected, rejected=True)
                rejected = True
            payload = {"previous": value.bytes().decode()}
        resource = project_ref(host.platform, selected.project_id) + "/" + selected.environment
        if selected.service_id:
            resource += "/" + selected.service_id
        now = datetime.now(UTC)
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)
        row = {
            "id": identity,
            "organisation_id": org,
            "host_id": host_id,
            "actor_id": actor,
            "provider_id": provider.id,
            "project_id": body.project_id,
            "reference": selected.reference,
            "resource": resource,
            "request": body.model_dump(mode="json"),
            "variable": selected.model_dump(mode="json"),
            "host_revision": host.revision,
            "provider_revision": provider.revision,
            "provider_ids": provider_ids,
            "scopes": sorted(scopes),
            "previous": previous.model_dump(mode="json"),
            "old_fingerprint": expected,
            "old_rejected": rejected,
            "phase": "pending",
            "active": True,
            "created_at": now,
            "updated_at": now,
            "revision": 0,
            "message": None,
        }
        row["value"] = self.vault.seal(payload, self.context(row))
        try:
            await self.rows.insert_one(row)
        except DuplicateKeyError:
            return await self.request(org, host_id, actor, identity, body)
        await self.record(row)
        await self.enqueue(row)
        return self.public(row)

    @staticmethod
    def provider_ready(provider: Connection) -> None:
        if (
            provider.archived_at
            or provider.status is not ConnectionStatus.READY
            or provider.interface is not ConnectionInterface.API
            or provider.roles != frozenset({ConnectionRole.PROVIDER})
            or not provider.http
            or not provider.http.supports_overlap
        ):
            raise ResourceConflictError("Choose an API provider with overlapping-key support")

    async def lock(self, row: Row) -> None:
        org = row["organisation_id"]
        query = {
            "organisation_id": org,
            "connection_id": row["host_id"],
            "resource": row["resource"],
        }
        async with self.database.transaction(org) as session:
            current = await self.database.collection("runtime_locks").find_one(
                query, session=session
            )
            if current:
                run = await self.database.collection("runs").find_one(
                    {"organisation_id": org, "id": current["run_id"]}, session=session
                )
                if not run or run["data"]["status"] not in {
                    RunStatus.COMPLETED,
                    RunStatus.CANCELLED,
                    RunStatus.FAILED,
                    RunStatus.COMPENSATED,
                }:
                    raise ResourceConflictError("Finish this workload's rotation before key setup")
            held = await self.database.collection("provision_locks").find_one(
                query, session=session
            )
            if held and held["request_id"] != row["id"]:
                raise ResourceConflictError("Another key setup uses this workload")
            await self.database.collection("provision_locks").update_one(
                query, {"$set": {"request_id": row["id"]}}, upsert=True, session=session
            )

    async def scope(
        self, row: Row, *, refresh: bool = False
    ) -> tuple[Hosting, Connection, Variable]:
        org = row["organisation_id"]
        grant, host = await self.hosts.account(org, row["host_id"])
        if not host or host.status is not ConnectionStatus.READY or host.archived_at:
            raise ResourceConflictError("Reconnect the hosting account before recovery")
        provider = await self.hosts.inventory.get_connection(org, row["provider_id"])
        self.provider_ready(provider)
        if not refresh and (
            host.revision != row["host_revision"] or provider.revision != row["provider_revision"]
        ):
            raise ResourceConflictError("Connection settings changed; review this key setup")
        selected = next(
            (
                item
                for item in await self.hosts.variables(org, host.id, row["project_id"])
                if item.reference == row["reference"]
            ),
            None,
        )
        if selected is None or selected != Variable.model_validate(row["variable"]):
            raise ResourceConflictError("The selected workload variable changed")
        await self.lock(row)
        return self.hosts.adapter(grant), provider, selected

    async def unchanged(self, row: Row, adapter: Hosting, variable: Variable) -> None:
        current = await adapter.current(variable)
        value = await adapter.read(variable)
        if value is None:
            raise ResourceConflictError("The current workload variable is unreadable")
        with value:
            if (
                fingerprint(value.bytes()) != row["old_fingerprint"]
                or current.id != row["previous"]["id"]
            ):
                raise ResourceConflictError(
                    "The workload changed before installing the managed key"
                )

    async def update(self, row: Row, **fields: Any) -> Row:
        saved = await self.rows.find_one_and_update(
            {
                "organisation_id": row["organisation_id"],
                "id": row["id"],
                "revision": row["revision"],
                "active": True,
            },
            {"$set": fields | {"updated_at": datetime.now(UTC)}, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if not saved:
            raise ChangedError
        if fields.get("phase") in {"creating", "writing", "review-required", "recovering"}:
            await self.record(saved)
        return saved

    async def record(self, row: Row) -> None:
        await self.audit.append(
            stable("key-setup", f"{row['id']}:{row['revision']}"),
            row["organisation_id"],
            "hosting.key-setup." + row["phase"],
            str(row.get("operator_id") or row["actor_id"]),
            row["reference"],
            {"setup_id": row["id"], "provider_connection_id": row["provider_id"]},
            occurred_at=row["updated_at"],
        )

    async def capture(self, row: Row, payload: Row, key_id: str) -> Row:
        current = await self.get(row["organisation_id"], row["id"])
        if current["phase"] != "creating" and not (
            current["phase"] == "review-required" and current.get("held_phase") == "creating"
        ):
            raise ChangedError
        return await self.update(
            current,
            phase="stored" if current["phase"] == "creating" else "review-required",
            key_id=key_id,
            value=self.vault.seal(payload, self.context(row)),
        )

    async def verify_created(self, row: Row, provider: Connection, payload: Row) -> str:
        if row["key_id"] in row["provider_ids"]:
            raise ResourceConflictError("The provider returned a pre-existing credential")
        with SecretValue(payload["token"].encode()) as secret:
            expected = fingerprint(secret.bytes())
            if expected in {
                row["old_fingerprint"],
                await self.mappings.provider.auth_fingerprint(provider),
            }:
                raise ResourceConflictError("The provider did not return a separate workload key")
            if await self.mappings.provider.identify(provider, secret) != row["key_id"]:
                raise ResourceConflictError("The new key does not match its issuer receipt")
        matches = [
            item
            for item in await self.mappings.provider.metadata(provider)
            if item["provider_id"] == row["key_id"]
        ]
        if (
            len(matches) != 1
            or matches[0].get("disabled") is True
            or matches[0].get("status") not in {"active", "enabled"}
            or sorted(matches[0].get("scopes", [])) != row["scopes"]
        ):
            raise ResourceConflictError("The created key does not match its requested permissions")
        return expected

    async def execute(self, org: str, identity: str) -> None:
        row = await self.get(org, identity)
        if not row["active"]:
            await self.finalize(row)
            return
        if row["phase"] not in _WORKING:
            return
        try:
            adapter, provider, variable = await self.scope(row)
            if row["phase"] in {"recovering", "restoring"}:
                await self.restore(row, adapter, provider, variable)
                return
            payload = self.vault.open(row["value"], self.context(row))
            body = Provision.model_validate(row["request"])
            if row["phase"] == "pending":
                await self.unchanged(row, adapter, variable)
                row = await self.update(row, phase="creating")
                scopes = self.mappings.provider.creation_scopes(provider, body.scopes)
                result = await self.mappings.provider.create_credential(
                    provider, stable("kinu", identity), scopes
                )
                if result.secret is None:
                    raise ResourceConflictError("Provider returned no managed key")
                with result.secret as secret:
                    payload["token"] = secret.bytes().decode()
                    key_id = str(result.result["provider_id"])
                    row = await self.capture(row, payload, key_id)
                if row["phase"] != "stored":
                    return
            if row["phase"] == "stored":
                await self.scope(row)
                await self.verify_created(row, provider, payload)
                await self.unchanged(row, adapter, variable)
                row = await self.update(row, phase="writing")
                with SecretValue(payload["token"].encode()) as secret:
                    await adapter.write(variable, secret)
                row = await self.update(row, phase="deploying")
                candidate = await adapter.deploy(
                    variable, Deployment.model_validate(row["previous"]), identity
                )
                row = await self.update(row, candidate=candidate.model_dump(mode="json"))
            if row["phase"] == "deploying":
                if not row.get("candidate"):
                    raise ResourceConflictError(
                        "Deployment outcome is unknown; review it before recovery"
                    )
                candidate = await adapter.status(variable, row["candidate"]["id"])
                if candidate.state == "pending":
                    return
                if (
                    candidate.state != "ready"
                    or (await adapter.current(variable)).id != candidate.id
                ):
                    raise ResourceConflictError(
                        "The new deployment is not live; recover this key setup"
                    )
                row = await self.update(row, phase="registering")
            if row["phase"] != "registering":
                return
            expected = await self.verify_created(row, provider, payload)
            current = await adapter.current(variable)
            loaded = await adapter.read(variable)
            if loaded is None:
                raise ResourceConflictError("The installed managed key is unreadable")
            with loaded:
                if (
                    current.id != row["candidate"]["id"]
                    or current.state != "ready"
                    or fingerprint(loaded.bytes()) != expected
                ):
                    raise ResourceConflictError("The workload changed before registration")
            await self.proof.endpoint(body.health_url, variable.name, expected)
            saved = await self.mappings.save(
                org,
                row["host_id"],
                row["actor_id"],
                Import(
                    project_id=row["project_id"],
                    initial_controls=body.initial_controls,
                    mappings=(
                        Mapping(
                            variable_reference=variable.reference,
                            provider_connection_id=provider.id,
                            provider_id=row["key_id"],
                            health_url=body.health_url,
                        ),
                    ),
                ),
                setup_id=row["id"],
            )
            await self.finish(row, "completed", saved[0].id)
        except ChangedError:
            return
        except Exception as error:
            row = await self.get(org, identity)
            if not row["active"] or row["phase"] == "review-required":
                return
            message = (
                str(error)
                if isinstance(error, (ResourceConflictError, ConnectorError))
                else "Key setup needs review. Check the provider and workload before recovery."
            )
            if row["phase"] == "pending":
                await self.finish(row, "cancelled", message=message)
                return
            await self.update(
                row,
                phase="review-required",
                held_phase=row.get("held_phase", row["phase"])
                if row["phase"] == "recovering"
                else row["phase"],
                message=message,
            )

    async def recover(self, org: str, identity: str, actor: str | None = None) -> Row:
        row = await self.get(org, identity)
        if row["phase"] == "review-required":
            adapter, provider, variable = await self.scope(row, refresh=True)
            _, host = await self.hosts.account(org, row["host_id"])
            assert host is not None
            held = row.get("held_phase")
            phase = (
                held
                if held in {"stored", "registering"}
                or (held == "deploying" and row.get("candidate"))
                or (held == "restoring" and row.get("restoration"))
                else "recovering"
            )
            if held == "deploying" and row.get("candidate"):
                candidate = await adapter.status(variable, row["candidate"]["id"])
                if candidate.state == "failed":
                    phase = "recovering"
            if held == "registering":
                payload = self.vault.open(row["value"], self.context(row))
                try:
                    await self.proof.endpoint(
                        row["request"]["health_url"],
                        variable.name,
                        fingerprint(payload["token"].encode()),
                    )
                except ConnectorError:
                    phase = "recovering"
            row = await self.update(
                row,
                phase=phase,
                message=None,
                host_revision=host.revision,
                provider_revision=provider.revision,
                operator_id=actor or row.get("operator_id", row["actor_id"]),
            )
            if phase != "recovering":
                await self.record(row)
        if row["phase"] in _WORKING:
            await self.enqueue(row)
        return self.public(row)

    async def restore(
        self, row: Row, adapter: Hosting, provider: Connection, variable: Variable
    ) -> None:
        payload = self.vault.open(row["value"], self.context(row))
        if await self.database.collection("host_bindings").find_one(
            {
                "organisation_id": row["organisation_id"],
                "connection_id": row["host_id"],
                "reference": row["reference"],
            }
        ):
            raise ResourceConflictError(
                "This key is already managed; finish its registration first"
            )
        if not payload.get("token") or not row.get("key_id"):
            raise ResourceConflictError(
                "Key creation has no verified receipt; review the provider account"
            )
        if row["key_id"] in row["provider_ids"]:
            raise ResourceConflictError("An existing provider key cannot be retired by key setup")
        if row.get("held_phase") == "deploying" and not row.get("candidate"):
            raise ResourceConflictError(
                "The deployment outcome is unknown; review the hosting account"
            )
        candidate = row.get("candidate")
        if candidate and (await adapter.status(variable, candidate["id"])).state == "pending":
            return
        if row["phase"] == "recovering":
            current = await adapter.current(variable)
            if current.id not in {row["previous"]["id"], candidate["id"] if candidate else None}:
                raise ResourceConflictError("The workload changed; preserve both keys for review")
            value = await adapter.read(variable)
            if value is None:
                raise ResourceConflictError("The installed variable cannot be verified")
            with value:
                if row.get("held_phase") == "writing" and not row.get("write_confirmed"):
                    if value.bytes().decode() != payload["token"]:
                        raise ResourceConflictError(
                            "The variable write is unresolved; "
                            "preserve its key until the outcome is known"
                        )
                    row = await self.update(row, write_confirmed=True)
                if value.bytes().decode() not in {payload["previous"], payload["token"]}:
                    raise ResourceConflictError(
                        "The variable changed; preserve both keys for review"
                    )
            with SecretValue(payload["previous"].encode()) as old:
                await adapter.write(variable, old)
            restored = (
                await adapter.restore(variable, row["previous"]["id"])
                if current.id != row["previous"]["id"]
                else current
            )
            row = await self.update(
                row, phase="restoring", restoration=restored.model_dump(mode="json")
            )
        restored = await adapter.status(variable, row["restoration"]["id"])
        if restored.state == "pending":
            return
        if restored.state != "ready" or (await adapter.current(variable)).id != restored.id:
            raise ResourceConflictError("The previous deployment could not be restored")
        await self.proof.endpoint(
            row["request"]["health_url"],
            variable.name,
            row["old_fingerprint"],
            rejected=row["old_rejected"],
        )
        with SecretValue(payload["token"].encode()) as secret:
            status = await self.mappings.provider.test_credential(provider, secret)
            if status not in {401, 403}:
                if await self.mappings.provider.identify(provider, secret) != row["key_id"]:
                    raise ResourceConflictError("The unused key changed identity")
                await self.mappings.provider.revoke_credential(provider, row["key_id"])
                if await self.mappings.provider.test_credential(provider, secret) not in {401, 403}:
                    raise ResourceConflictError(
                        "The provider did not confirm the unused key's retirement"
                    )
        await self.finish(row, "cancelled")

    async def finish(
        self, row: Row, phase: str, credential: str | None = None, message: str | None = None
    ) -> None:
        saved = await self.update(
            row, phase=phase, active=False, credential_id=credential, message=message
        )
        await self.finalize(saved)

    async def finalize(self, row: Row) -> None:
        await self.rows.update_one(
            {"organisation_id": row["organisation_id"], "id": row["id"]},
            {"$unset": {"value": ""}},
        )
        await self.record(row)
        await self.database.collection("provision_locks").delete_one(
            {"organisation_id": row["organisation_id"], "request_id": row["id"]}
        )
        await self.rows.update_one(
            {"organisation_id": row["organisation_id"], "id": row["id"]},
            {"$set": {"finalized": True}},
        )
