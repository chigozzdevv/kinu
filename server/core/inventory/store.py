from datetime import datetime

from contracts import (
    Application,
    Connection,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    Contract,
    ControlVersion,
    CredentialGeneration,
    Environment,
    Incident,
    IncidentStatus,
    ManagedCredential,
    PlaybookVersion,
    ProbeVersion,
)

from core.errors import ResourceConflictError
from core.inventory.relations import check_references
from core.inventory.repository import InventoryResource
from core.storage.database import Database
from core.storage.incidents import resolve_observed_incidents
from core.storage.records import Records


class InventoryStore:
    def __init__(self, database: Database) -> None:
        self._records = Records(database)

    async def add_connection(self, value: Connection) -> Connection:
        return await self._add(value)

    async def add_application(self, value: Application) -> Application:
        return await self._add(value)

    async def add_environment(self, value: Environment) -> Environment:
        return await self._add(value)

    async def add_service(self, value: ConsumerService) -> ConsumerService:
        return await self._add(value)

    async def add_application_setup(
        self, application: Application, environment: Environment, service: ConsumerService
    ) -> tuple[Application, Environment, ConsumerService]:
        org = application.organisation_id
        async with self._records.write(org) as rows:
            for value in (application, environment, service):
                await rows.add(value, org)
                await check_references(rows, value)
        return application, environment, service

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return await self._get(Connection, organisation_id, resource_id)

    async def get_application(self, organisation_id: str, resource_id: str) -> Application:
        return await self._get(Application, organisation_id, resource_id)

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment:
        return await self._get(Environment, organisation_id, resource_id)

    async def get_service(self, organisation_id: str, resource_id: str) -> ConsumerService:
        return await self._get(ConsumerService, organisation_id, resource_id)

    async def get_credential(self, organisation_id: str, resource_id: str) -> ManagedCredential:
        return await self._get(ManagedCredential, organisation_id, resource_id)

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        value = await self._get(ControlVersion, organisation_id, version_id)
        if value.credential_id != credential_id:
            raise ResourceConflictError("controls belong to another credential")
        return value

    async def get_playbook_version(
        self, organisation_id: str, playbook_id: str, version_id: str
    ) -> PlaybookVersion:
        value = await self._get(PlaybookVersion, organisation_id, version_id)
        if value.playbook_id != playbook_id:
            raise ResourceConflictError("version belongs to another playbook")
        return value

    async def replace_connection(self, value: Connection, expected_revision: int) -> Connection:
        return await self._replace(value, expected_revision)

    async def replace_application(self, value: Application, expected_revision: int) -> Application:
        return await self._replace(value, expected_revision)

    async def replace_environment(self, value: Environment, expected_revision: int) -> Environment:
        return await self._replace(value, expected_revision)

    async def replace_service(
        self, value: ConsumerService, expected_revision: int
    ) -> ConsumerService:
        return await self._replace(value, expected_revision)

    async def replace_credential(
        self, value: ManagedCredential, expected_revision: int
    ) -> ManagedCredential:
        return await self._replace(value, expected_revision)

    async def attach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        playbook_id: str,
        version_id: str,
        updated_at: datetime,
    ) -> Connection:
        async with self._records.write(organisation_id) as rows:
            version = await rows.get(PlaybookVersion, organisation_id, version_id)
            if version.playbook_id != playbook_id or version.state.value != "published":
                raise ResourceConflictError("connection requires a published playbook version")
            current = await rows.get(Connection, organisation_id, connection_id)
            changed = Connection.model_validate(
                current.model_dump()
                | {
                    "playbook_id": playbook_id,
                    "playbook_version_id": version_id,
                    "updated_at": updated_at,
                    "revision": expected_revision + 1,
                }
            )
            return await rows.replace(changed, expected_revision)

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        authorization_reference: str | None,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection:
        async with self._records.write(organisation_id) as rows:
            current = await rows.get(Connection, organisation_id, connection_id)
            if current.archived_at is not None:
                raise ResourceConflictError("archived connection cannot be authenticated")
            changed = Connection.model_validate(
                current.model_dump()
                | {
                    "authorization_reference": authorization_reference,
                    "status": status,
                    "authenticated_at": updated_at,
                    "updated_at": updated_at,
                    "revision": expected_revision + 1,
                }
            )
            return await rows.replace(changed, expected_revision)

    async def detach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        updated_at: datetime,
    ) -> Connection:
        async with self._records.write(organisation_id) as rows:
            current = await rows.get(Connection, organisation_id, connection_id)
            changed = Connection.model_validate(
                current.model_dump()
                | {
                    "playbook_id": None,
                    "playbook_version_id": None,
                    "status": ConnectionStatus.SETUP_REQUIRED,
                    "updated_at": updated_at,
                    "revision": expected_revision + 1,
                }
            )
            return await rows.replace(changed, expected_revision)

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
        controls: ControlVersion,
        probes: tuple[ProbeVersion, ...],
        service_setup: tuple[Application, Environment, ConsumerService] | None = None,
    ) -> ManagedCredential:
        org = credential.organisation_id
        async with self._records.write(org) as rows:
            values: tuple[Contract, ...] = (
                *(service_setup or ()),
                credential,
                generation,
                *bindings,
                controls,
                *probes,
            )
            for value in values:
                await rows.add(value, org)
            for value in values:
                await check_references(rows, value)
            if (
                credential.active_generation_id != generation.id
                or credential.control_version != controls.id
                or generation.credential_id != credential.id
                or controls.credential_id != credential.id
                or generation.secret_reference != credential.secret_reference
                or len(bindings) != len(credential.consumer_ids)
                or any(
                    binding.credential_id != credential.id
                    or binding.current_generation_id != generation.id
                    for binding in bindings
                )
                or set(credential.consumer_ids) != {binding.service_id for binding in bindings}
            ):
                raise ResourceConflictError("credential import lineage is inconsistent")
        return credential

    async def replace_controls(
        self, credential: ManagedCredential, expected_revision: int, controls: ControlVersion
    ) -> tuple[ManagedCredential, ControlVersion]:
        org = credential.organisation_id
        async with self._records.write(org) as rows:
            current = await rows.get(ManagedCredential, org, credential.id)
            previous = await rows.get(ControlVersion, org, current.control_version)
            if (
                controls.credential_id != credential.id
                or controls.number != previous.number + 1
                or credential.control_version != controls.id
            ):
                raise ResourceConflictError("control version lineage is inconsistent")
            await rows.add(controls, org)
            await check_references(rows, controls)
            await check_references(rows, credential)
            await rows.replace(credential, expected_revision)
        return credential, controls

    async def replace_provider(
        self,
        credential: ManagedCredential,
        revision: int,
        controls: ControlVersion,
        probes: tuple[ProbeVersion, ...],
        source: Connection,
        target: Connection,
    ) -> ManagedCredential:
        org = credential.organisation_id
        async with self._records.write(org) as rows:
            current = await rows.get(ManagedCredential, org, credential.id)
            previous = await rows.get(ControlVersion, org, current.control_version)
            if (
                current.connection_id != source.id
                or credential.connection_id != target.id
                or await rows.get(Connection, org, source.id) != source
                or await rows.get(Connection, org, target.id) != target
                or target.verification_connection_id != source.id
                or controls.credential_id != credential.id
                or controls.number != previous.number + 1
                or credential.control_version != controls.id
            ):
                raise ResourceConflictError("Provider selection changed")
            if await rows.database.collection("run_locks").find_one(
                {"organisation_id": org, "credential_id": credential.id}, session=rows.session
            ):
                raise ResourceConflictError("Wait for the active rotation to finish")
            for item in (*probes, controls):
                await rows.add(item, org)
                await check_references(rows, item)
            await check_references(rows, credential)
            await rows.replace(credential, revision)
        return credential

    async def archive_inventory(
        self, resources: tuple[InventoryResource, ...], bindings: tuple[ConsumerBinding, ...]
    ) -> None:
        if not resources:
            raise ResourceConflictError("archive requires inventory")
        org = resources[0].organisation_id
        async with self._records.write(org) as rows:
            credential_ids = {
                value.id for value in resources if isinstance(value, ManagedCredential)
            } | {binding.credential_id for binding in bindings}
            if await rows.database.collection("run_locks").find_one(
                {"organisation_id": org, "credential_id": {"$in": sorted(credential_ids)}},
                session=rows.session,
            ):
                raise ResourceConflictError("Finish active rotations before removing inventory")
            for value in resources:
                if value.organisation_id != org or value.archived_at is None:
                    raise ResourceConflictError("archive must stay within one workspace")
                await rows.replace(value, value.revision - 1)
            for binding in bindings:
                if binding.organisation_id != org:
                    raise ResourceConflictError("binding belongs to another workspace")
                await rows.remove(binding)
                await rows.database.collection("host_bindings").delete_one(
                    {"organisation_id": org, "binding_id": binding.id}, session=rows.session
                )
            for model in (
                Connection,
                Environment,
                ConsumerService,
                ManagedCredential,
                ConsumerBinding,
            ):
                for active in await rows.list(model, org):
                    if getattr(active, "archived_at", None) is None:
                        await check_references(rows, active)

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]:
        return await self._list(Connection, organisation_id)

    async def applications(self, organisation_id: str) -> tuple[Application, ...]:
        return await self._list(Application, organisation_id)

    async def environments(self, organisation_id: str) -> tuple[Environment, ...]:
        return await self._list(Environment, organisation_id)

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return await self._list(ConsumerService, organisation_id)

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return await self._list(ManagedCredential, organisation_id)

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]:
        return await self._list(ConsumerBinding, organisation_id)

    async def generations(self, organisation_id: str) -> tuple[CredentialGeneration, ...]:
        return await self._list(CredentialGeneration, organisation_id)

    async def record_observation(
        self,
        organisation_id: str,
        credential_id: str,
        generation_id: str,
        expected_revision: int,
        observed_at: datetime,
        expires_at: datetime | None,
        rotation_due_at: datetime | None,
        metadata_digest: str,
    ) -> ManagedCredential:
        async with self._records.write(organisation_id) as rows:
            current = await rows.get(ManagedCredential, organisation_id, credential_id)
            if (
                current.archived_at
                or current.revision != expected_revision
                or current.active_generation_id != generation_id
                or await rows.database.collection("run_locks").find_one(
                    {"organisation_id": organisation_id, "credential_id": credential_id},
                    session=rows.session,
                )
            ):
                raise ResourceConflictError("Credential changed during monitoring")
            observed = await rows.replace(
                current.model_copy(
                    update={
                        "last_observed_at": observed_at,
                        "expires_at": expires_at,
                        "rotation_due_at": rotation_due_at,
                        "metadata_digest": metadata_digest,
                        "updated_at": observed_at,
                        "revision": current.revision + 1,
                    }
                ),
                current.revision,
            )
            await resolve_observed_incidents(
                rows, observed, observed_at, kind="credential-metadata-stale"
            )
            return observed

    async def record_runtime_observation(
        self,
        credential: ManagedCredential,
        bindings: tuple[ConsumerBinding, ...],
        observed_at: datetime,
    ) -> None:
        org = credential.organisation_id
        async with self._records.write(org) as rows:
            current = await rows.get(ManagedCredential, org, credential.id)
            current_bindings = await rows.match(
                ConsumerBinding, org, {"credential_id": credential.id}
            )
            if (
                not bindings
                or current.archived_at
                or current.active_generation_id != credential.active_generation_id
                or current.secret_reference != credential.secret_reference
                or current.control_version != credential.control_version
                or {item.id: item for item in current_bindings}
                != {item.id: item for item in bindings}
                or await rows.database.collection("run_locks").find_one(
                    {"organisation_id": org, "credential_id": credential.id},
                    session=rows.session,
                )
            ):
                raise ResourceConflictError("Credential changed during runtime monitoring")
            await resolve_observed_incidents(
                rows, current, observed_at, kind="credential-runtime-drift"
            )

    async def runtime_recovery_at(
        self, organisation_id: str, credential_id: str, generation_id: str
    ) -> datetime | None:
        async with self._records.read() as rows:
            incidents = await rows.match(
                Incident,
                organisation_id,
                {"credential_id": credential_id, "kind": "credential-runtime-drift"},
            )
        return max(
            (
                incident.resolution_observed_at
                for incident in incidents
                if incident.status is IncidentStatus.RESOLVED
                and incident.resource.generation_id == generation_id
                and incident.resolution_observed_at is not None
            ),
            default=None,
        )

    async def _get[T: Contract](self, model: type[T], org: str, resource_id: str) -> T:
        async with self._records.read() as rows:
            return await rows.get(model, org, resource_id)

    async def _list[T: Contract](self, model: type[T], org: str) -> tuple[T, ...]:
        async with self._records.read() as rows:
            return await rows.list(model, org)

    async def _add[T: InventoryResource](self, value: T) -> T:
        async with self._records.write(value.organisation_id) as rows:
            await check_references(rows, value)
            return await rows.add(value, value.organisation_id)

    async def _replace[T: InventoryResource](self, value: T, revision: int) -> T:
        async with self._records.write(value.organisation_id) as rows:
            await check_references(rows, value)
            await rows.replace(value, revision)
            if isinstance(value, (Connection, ConsumerService)):
                for model in (Connection, ConsumerService, ManagedCredential, ConsumerBinding):
                    for dependent in await rows.list(model, value.organisation_id):
                        if getattr(dependent, "archived_at", None) is None:
                            await check_references(rows, dependent)
        return value
