from contracts import (
    Application,
    Connection,
    ConnectionStatus,
    ConsumerService,
    ControlPreferences,
    ControlVersion,
    Environment,
    ManagedCredential,
)
from policy import digest

from core.errors import ResourceConflictError
from core.inventory.catalog import InventoryCatalog
from core.inventory.controls import update_controls as compile_update
from core.inventory.controls import validate_exposure_sources
from core.inventory.repository import (
    InventoryResource,
)
from core.inventory.validation import (
    _active,
    _archive,
    _change,
    _editable,
)


class InventoryChanges:
    def __init__(self, catalog: InventoryCatalog) -> None:
        self.catalog = catalog

    async def update_connection(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
        capabilities: frozenset[str] | None = None,
        allowed_resources: tuple[str, ...] | None = None,
        region: str | None = None,
    ) -> Connection:
        current = await self.catalog.get_connection(organisation_id, resource_id)
        if current.archived_at is not None:
            raise ResourceConflictError("archived connections cannot be edited")
        changed = _change(
            current,
            expected_revision,
            self.catalog._clock(),
            display_name=display_name,
            capabilities=capabilities,
            allowed_resources=allowed_resources,
            region=region,
        )
        await self.catalog._validate_connection(changed)
        await self.catalog._validate_connection_usage(changed)
        return await self.catalog._repository.replace_connection(changed, expected_revision)

    async def update_application(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
        repository_ids: tuple[str, ...] | None = None,
    ) -> Application:
        current = await self.catalog.get_application(organisation_id, resource_id)
        _editable(current, expected_revision, "application")
        changed = _change(
            current,
            expected_revision,
            self.catalog._clock(),
            display_name=display_name,
            repository_ids=repository_ids,
        )
        return await self.catalog._repository.replace_application(changed, expected_revision)

    async def update_environment(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
        production: bool | None = None,
        region: str | None = None,
    ) -> Environment:
        current = await self.catalog.get_environment(organisation_id, resource_id)
        _editable(current, expected_revision, "environment")
        changed = _change(
            current,
            expected_revision,
            self.catalog._clock(),
            display_name=display_name,
            production=production,
            region=region,
        )
        return await self.catalog._repository.replace_environment(changed, expected_revision)

    async def update_service(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
        runtime_connection_id: str | None = None,
        telemetry_connection_ids: tuple[str, ...] | None = None,
        runtime_resource: str | None = None,
        endpoint: str | None = None,
        repository: str | None = None,
        identity: str | None = None,
    ) -> ConsumerService:
        current = await self.catalog.get_service(organisation_id, resource_id)
        _editable(current, expected_revision, "service")
        changed = _change(
            current,
            expected_revision,
            self.catalog._clock(),
            display_name=display_name,
            runtime_connection_id=runtime_connection_id,
            telemetry_connection_ids=telemetry_connection_ids,
            runtime_resource=runtime_resource,
            endpoint=endpoint,
            repository=repository,
            identity=identity,
        )
        await self.catalog._validate_service_connections(changed)
        return await self.catalog._repository.replace_service(changed, expected_revision)

    async def update_credential(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
    ) -> ManagedCredential:
        current = await self.catalog.get_credential(organisation_id, resource_id)
        _editable(current, expected_revision, "credential")
        changed = _change(
            current,
            expected_revision,
            self.catalog._clock(),
            display_name=display_name,
        )
        return await self.catalog._repository.replace_credential(changed, expected_revision)

    async def update_controls(
        self,
        organisation_id: str,
        credential_id: str,
        expected_revision: int,
        version_id: str,
        preferences: ControlPreferences,
        actor_id: str,
    ) -> tuple[ManagedCredential, ControlVersion]:
        current = await self.catalog.get_credential(organisation_id, credential_id)
        _editable(current, expected_revision, "credential")
        validate_exposure_sources(
            _active(await self.catalog._repository.connections(organisation_id)), preferences
        )
        previous = await self.catalog.get_controls(
            organisation_id, credential_id, current.control_version
        )
        definition = compile_update(previous.definition, preferences)
        controls = ControlVersion(
            id=version_id,
            organisation_id=organisation_id,
            credential_id=credential_id,
            number=previous.number + 1,
            definition=definition,
            digest=digest(definition),
            created_by=actor_id,
            created_at=self.catalog._clock(),
        )
        changed = _change(
            current,
            expected_revision,
            self.catalog._clock(),
            control_version=controls.id,
        )
        return await self.catalog._repository.replace_controls(changed, expected_revision, controls)

    async def archive_connection(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> Connection:
        current = await self.catalog.get_connection(organisation_id, resource_id)
        credentials = _active(await self.catalog._repository.credentials(organisation_id))
        services = _active(await self.catalog._repository.services(organisation_id))
        dependent_services = tuple(
            service
            for service in services
            if resource_id == service.runtime_connection_id
            or resource_id in service.telemetry_connection_ids
        )
        dependent_credentials = tuple(
            credential
            for credential in credentials
            if resource_id in {credential.connection_id, credential.secret_store_connection_id}
        )
        if (dependent_services or dependent_credentials) and not cascade:
            raise ResourceConflictError("connection is still used by active inventory")
        return await self._archive_inventory(
            _archive(
                current, expected_revision, self.catalog._clock(), status=ConnectionStatus.DISABLED
            ),
            services=dependent_services,
            credentials=dependent_credentials,
        )

    async def archive_application(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> Application:
        current = await self.catalog.get_application(organisation_id, resource_id)
        environments = tuple(
            item
            for item in _active(await self.catalog._repository.environments(organisation_id))
            if item.application_id == resource_id
        )
        services = tuple(
            item
            for item in _active(await self.catalog._repository.services(organisation_id))
            if item.application_id == resource_id
        )
        if (environments or services) and not cascade:
            raise ResourceConflictError(
                "application still contains active environments or services"
            )
        return await self._archive_inventory(
            _archive(current, expected_revision, self.catalog._clock()),
            environments=environments,
            services=services,
        )

    async def archive_environment(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> Environment:
        current = await self.catalog.get_environment(organisation_id, resource_id)
        services = tuple(
            service
            for service in _active(await self.catalog._repository.services(organisation_id))
            if service.environment_id == resource_id
        )
        if services and not cascade:
            raise ResourceConflictError("environment still contains active services")
        return await self._archive_inventory(
            _archive(current, expected_revision, self.catalog._clock()), services=services
        )

    async def archive_service(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> ConsumerService:
        current = await self.catalog.get_service(organisation_id, resource_id)
        bindings = tuple(
            binding
            for binding in await self.catalog._repository.bindings(organisation_id)
            if binding.service_id == resource_id
        )
        if bindings and not cascade:
            raise ResourceConflictError("service still has credential bindings")
        return await self._archive_inventory(
            _archive(current, expected_revision, self.catalog._clock()),
            credentials=tuple(
                credential
                for credential in _active(
                    await self.catalog._repository.credentials(organisation_id)
                )
                if credential.id in {binding.credential_id for binding in bindings}
            ),
        )

    async def archive_credential(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        cascade: bool = False,
    ) -> ManagedCredential:
        current = await self.catalog.get_credential(organisation_id, resource_id)
        if current.consumer_ids and not cascade:
            raise ResourceConflictError("credential must be disconnected before it can be archived")
        return await self._archive_inventory(
            _archive(current, expected_revision, self.catalog._clock())
        )

    async def _archive_inventory[
        T: (Connection, Application, Environment, ConsumerService, ManagedCredential)
    ](
        self,
        primary: T,
        *,
        environments: tuple[Environment, ...] = (),
        services: tuple[ConsumerService, ...] = (),
        credentials: tuple[ManagedCredential, ...] = (),
    ) -> T:
        organisation_id = primary.organisation_id
        all_bindings = await self.catalog._repository.bindings(organisation_id)
        service_ids = {service.id for service in services}
        credential_ids = {credential.id for credential in credentials}
        if isinstance(primary, ManagedCredential):
            credential_ids.add(primary.id)
        credential_ids.update(
            binding.credential_id for binding in all_bindings if binding.service_id in service_ids
        )
        active_credentials: tuple[ManagedCredential, ...] = _active(
            await self.catalog._repository.credentials(organisation_id)
        )
        related_credentials: tuple[ManagedCredential, ...] = tuple(
            credential for credential in active_credentials if credential.id in credential_ids
        )
        now = self.catalog._clock()
        resources: dict[tuple[type[object], str], InventoryResource] = {
            (type(primary), primary.id): primary,
        }
        for environment in environments:
            resources[(type(environment), environment.id)] = _archive(
                environment, environment.revision, now
            )
        for service in services:
            resources[(type(service), service.id)] = _archive(service, service.revision, now)
        for credential in related_credentials:
            resources[(type(credential), credential.id)] = _archive(
                credential, credential.revision, now
            )
        removed_bindings = tuple(
            binding for binding in all_bindings if binding.credential_id in credential_ids
        )
        await self.catalog._repository.archive_inventory(
            tuple(resources.values()),
            removed_bindings,
        )
        return primary
