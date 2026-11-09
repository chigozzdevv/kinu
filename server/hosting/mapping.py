from datetime import UTC, datetime

from connectors.http import HttpProviderConnector
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlPreferences,
    ControlVersion,
    CredentialGeneration,
    GenerationState,
    ManagedCredential,
)
from core.errors import ActiveRunConflictError, ResourceConflictError
from core.inventory.controls import compile_controls
from core.inventory.relations import check_references
from core.storage.records import Records, RecordTransaction
from executor.versions import Versions, root, store_id
from policy import digest

from hosting.imports import Prepared, prepare
from hosting.models import Import
from hosting.resources import identity, workload
from hosting.service import Service


class Mappings:
    def __init__(self, hosts: Service, versions: Versions, provider: HttpProviderConnector) -> None:
        self.hosts, self.versions, self.provider = hosts, versions, provider

    async def save(
        self, org: str, connection_id: str, actor: str, body: Import, *, setup_id: str | None = None
    ) -> tuple[ManagedCredential, ...]:
        _, host = await self.hosts.account(org, connection_id)
        if not host or host.status is not ConnectionStatus.READY:
            raise ResourceConflictError("Choose a connected hosting account")
        store = self.store(org)
        prepared = await prepare(self.hosts, self.versions, self.provider, org, host, body, store)
        projects = await self.hosts.projects(org, connection_id, actor, "")
        project_name = next(
            item["name"] for item in projects["projects"] if item["id"] == body.project_id
        )
        groups: dict[str, list[Prepared]] = {}
        for item in prepared:
            groups.setdefault(item.credential_id, []).append(item)
        results = []
        async with Records(self.hosts.database).write(org) as rows:
            stores = await rows.match(Connection, org, {"id": store.id})
            if not stores:
                await rows.add(store, org)
            elif (
                stores[0].platform != "kinu"
                or stores[0].archived_at
                or stores[0].status is not ConnectionStatus.READY
                or stores[0].allowed_resources != store.allowed_resources
            ):
                raise ResourceConflictError("Workspace vault is unavailable")
            if await rows.get(Connection, org, host.id) != host:
                raise ResourceConflictError("Hosting connection changed during discovery")
            for items in groups.values():
                results.append(
                    await self.group(
                        rows,
                        host,
                        items,
                        store,
                        actor,
                        project_name,
                        setup_id,
                        body.initial_controls,
                    )
                )
        return tuple(results)

    async def group(
        self,
        rows: RecordTransaction,
        host: Connection,
        items: list[Prepared],
        store: Connection,
        actor: str,
        project_name: str,
        setup_id: str | None,
        initial_controls: ControlPreferences | None,
    ) -> ManagedCredential:
        org, now, first = host.organisation_id, datetime.now(UTC), items[0]
        key, provider, credential = first.credential_id, first.provider, first.existing
        if await rows.get(Connection, org, provider.id) != provider:
            raise ResourceConflictError("Provider connection changed during discovery")
        current = await rows.match(
            ManagedCredential,
            org,
            {
                "connection_id": provider.id,
                "provider_id": first.metadata.provider_id,
                "archived_at": None,
            },
        )
        if current != ((credential,) if credential else ()):
            raise ResourceConflictError("Credential mapping changed during discovery")
        if await rows.database.collection("run_locks").find_one(
            {"organisation_id": org, "credential_id": key}, session=rows.session
        ):
            raise ActiveRunConflictError("Wait for this credential's active rotation")
        generation_id = (
            credential.active_generation_id
            if credential
            else identity("generation", key, "initial")
        )
        if not generation_id:
            raise ResourceConflictError("Credential has no active generation")
        bindings = list(await rows.match(ConsumerBinding, org, {"credential_id": key}))
        added = []
        for item in items:
            service = await workload(
                rows, host, item.variable, project_name, item.choice.health_url
            )
            setup = await rows.database.collection("provision_locks").find_one(
                {
                    "organisation_id": org,
                    "connection_id": host.id,
                    "resource": service.runtime_resource,
                },
                session=rows.session,
            )
            if setup and setup["request_id"] != setup_id:
                raise ResourceConflictError("Finish this workload's key setup before importing")
            binding_id = identity("binding", host.id, item.variable.reference)
            if any(existing.id == binding_id for existing in bindings):
                continue
            if any(existing.service_id == service.id for existing in bindings):
                raise ResourceConflictError("Select this credential once per workload")
            if await rows.database.collection("host_bindings").find_one(
                {
                    "organisation_id": org,
                    "connection_id": host.id,
                    "reference": item.variable.reference,
                },
                session=rows.session,
            ):
                raise ResourceConflictError("Variable is already managed by another credential")
            binding = ConsumerBinding(
                id=binding_id,
                organisation_id=org,
                credential_id=key,
                service_id=service.id,
                environment_id=service.environment_id,
                runtime_connection_id=host.id,
                runtime_resource=service.runtime_resource,
                runtime_secret_name=item.variable.name,
                secret_reference=item.reference,
                current_generation_id=generation_id,
            )
            bindings.append(binding)
            added.append(binding)
            await rows.database.collection("host_bindings").insert_one(
                {
                    "organisation_id": org,
                    "binding_id": binding.id,
                    "credential_id": key,
                    "connection_id": host.id,
                    "reference": item.variable.reference,
                    "variable": item.variable.model_dump(mode="json"),
                },
                session=rows.session,
            )
        if credential and not added:
            return credential
        previous = (
            await rows.get(ControlVersion, org, credential.control_version) if credential else None
        )
        control_id = identity("controls", key, str(previous.number + 1 if previous else 1))
        changed = (
            credential
            or ManagedCredential(
                id=key,
                organisation_id=org,
                connection_id=provider.id,
                secret_store_connection_id=store.id,
                secret_resource=root(org) + "/" + key,
                secret_reference=first.reference,
                provider=provider.platform,
                kind=first.metadata.kind or "api-key",
                display_name=(first.metadata.name or first.metadata.provider_id)[:160],
                provider_id=first.metadata.provider_id,
                scopes=frozenset(first.metadata.scopes),
                expires_at=first.metadata.expires_at,
                active_generation_id=generation_id,
                control_version=control_id,
                created_at=now,
                updated_at=now,
            )
        ).model_copy(
            update={
                "consumer_ids": tuple(binding.service_id for binding in bindings),
                "control_version": control_id,
                "updated_at": now,
                "revision": credential.revision + 1 if credential else 0,
            }
        )
        preferences = self.preferences(previous, initial_controls)
        services = tuple(
            [await rows.get(ConsumerService, org, item.service_id) for item in bindings]
        )
        definition, probes = compile_controls(
            changed,
            tuple(bindings),
            services,
            await rows.list(Connection, org),
            preferences,
            actor,
            now,
        )
        controls = ControlVersion(
            id=control_id,
            organisation_id=org,
            credential_id=key,
            number=previous.number + 1 if previous else 1,
            definition=definition,
            digest=digest(definition),
            created_by=actor,
            created_at=now,
        )
        await rows.add(controls, org)
        if credential:
            await rows.replace(changed, credential.revision)
        else:
            await rows.add(changed, org)
            await rows.add(
                CredentialGeneration(
                    id=generation_id,
                    organisation_id=org,
                    credential_id=key,
                    provider_id=first.metadata.provider_id,
                    scopes=changed.scopes,
                    state=GenerationState.ACTIVE,
                    attempt_id=identity("import", key),
                    secret_reference=first.reference,
                    fingerprint=first.fingerprint,
                    created_at=now,
                ),
                org,
            )
        for binding in added:
            await rows.add(binding, org)
        for probe in probes:
            await rows.add(probe, org)
        for value in (*bindings, changed, controls, *probes, *services):
            await check_references(rows, value)
        return changed

    @staticmethod
    def preferences(
        previous: ControlVersion | None, initial: ControlPreferences | None = None
    ) -> ControlPreferences:
        if not previous:
            return initial or ControlPreferences(
                automatic_triggers=frozenset({"expiry"}),
                rotate_before_expiry_seconds=86400,
                maximum_observation_seconds=60,
            )
        definition = previous.definition
        return ControlPreferences(
            automatic_triggers=frozenset(
                name
                for name, event in {
                    "expiry": "credential-expiring",
                    "drift": "credential-provider-drift",
                    "verified-exposure": "credential-exposure-detected",
                }.items()
                if event in definition.automatic_triggers
            ),
            rotate_before_expiry_seconds=definition.rotate_before_expiry_seconds,
            maximum_observation_seconds=definition.maximum_observation_seconds,
            require_revoke_approval=definition.require_revoke_approval,
            exposure_sources=definition.exposure_sources,
        )

    @staticmethod
    def store(org: str) -> Connection:
        now = datetime.now(UTC)
        return Connection(
            id=store_id(org),
            organisation_id=org,
            platform="kinu",
            display_name="Kinu vault",
            roles=frozenset({ConnectionRole.SECRET_STORE}),
            interface=ConnectionInterface.API,
            authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
            authorization_reference="private://" + store_id(org),
            capabilities=frozenset(
                {
                    "secretStore.getVersion",
                    "secretStore.testConsumerAccess",
                    "secretStore.disableVersion",
                }
            ),
            allowed_resources=(root(org),),
            status=ConnectionStatus.READY,
            region="global",
            created_at=now,
            updated_at=now,
        )
