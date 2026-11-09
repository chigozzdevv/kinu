from collections.abc import Callable
from datetime import datetime

from contracts import (
    Application,
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
    Environment,
    ManagedCredential,
    ProviderCredentialMetadata,
    RuntimeConsumerSetup,
)

from core.errors import ResourceConflictError
from core.inventory.catalog import InventoryCatalog
from core.inventory.changes import InventoryChanges
from core.inventory.credentials import CredentialService
from core.inventory.repository import (
    CredentialBindingVerifier,
    InventoryRepository,
    ProviderMetadataLister,
    RuntimeMetadataLister,
    SecretMetadataLister,
)
from core.inventory.validation import (
    _domain_pattern,
    _required_text,
    _tenant,
)

_BROWSER_CAPABILITIES = frozenset({"browser.execute", "browser.authenticate"})


class InventoryService(InventoryCatalog):
    def __init__(
        self,
        repository: InventoryRepository,
        clock: Callable[[], datetime] | None = None,
        provider_metadata: ProviderMetadataLister | None = None,
        credential_verifier: CredentialBindingVerifier | None = None,
        secret_metadata: SecretMetadataLister | None = None,
        runtime_metadata: RuntimeMetadataLister | None = None,
    ) -> None:
        super().__init__(
            repository,
            clock,
            provider_metadata,
            credential_verifier,
            secret_metadata,
            runtime_metadata,
        )
        self._credentials = CredentialService(self)
        self._changes = InventoryChanges(self)

    async def add_connection(self, connection: Connection) -> Connection:
        if (connection.authorization_reference or "").startswith("hosting://") or (
            connection.platform in {"vercel", "railway", "render"}
            and ConnectionRole.RUNTIME in connection.roles
        ):
            raise ResourceConflictError("Connect hosting accounts through Connections")
        if connection.interface is ConnectionInterface.BROWSER:
            if not connection.allowed_resources or any(
                not _domain_pattern(value) for value in connection.allowed_resources
            ):
                raise ResourceConflictError("browser connection must declare allowed domains")
            if not connection.capabilities.intersection(_BROWSER_CAPABILITIES):
                raise ResourceConflictError("browser connection must declare a browser capability")
            if connection.authorization is not ConnectionAuthorization.BROWSER_SESSION:
                raise ResourceConflictError(
                    "browser connection requires browser-session authorization"
                )
        if (
            ConnectionRole.PROVIDER in connection.roles
            and connection.interface is ConnectionInterface.API
        ):
            api = connection.http
            legacy_resend = connection.platform == "resend" and connection.api_playbook_id is None
            if (
                api is None
                or api.test_credential is None
                or api.credential_auth is None
                or (
                    api.credential_identity_mode == "response"
                    and api.test_credential.provider_id_field is None
                    and not legacy_resend
                )
                or (
                    api.credential_identity_mode == "creation-receipt"
                    and not api.test_credential.response_matches
                )
                or "provider.testCredential" not in connection.capabilities
            ):
                raise ResourceConflictError(
                    "provider connection requires list, create, revoke, "
                    "and credential identity operations"
                )
            if self.provider_metadata is None:
                raise ResourceConflictError("provider connection validation is unavailable")
            await self.provider_metadata.metadata(connection)
            now = self.clock()
            connection = connection.model_copy(
                update={
                    "status": ConnectionStatus.READY,
                    "authenticated_at": now,
                    "last_validated_at": now,
                    "updated_at": now,
                }
            )
        if (
            ConnectionRole.RUNTIME in connection.roles
            and connection.interface is ConnectionInterface.API
        ):
            if self.runtime_metadata is None:
                raise ResourceConflictError("runtime connection validation is unavailable")
            await self.runtime_metadata.resources_for(connection)
            now = self.clock()
            connection = connection.model_copy(
                update={
                    "status": ConnectionStatus.READY,
                    "authenticated_at": now,
                    "last_validated_at": now,
                    "updated_at": now,
                }
            )
        if (
            ConnectionRole.SECRET_STORE in connection.roles
            and connection.interface is ConnectionInterface.API
        ):
            if self.secret_metadata is None:
                raise ResourceConflictError("secret-store connection validation is unavailable")
            await self.secret_metadata.resources_for(connection)
            now = self.clock()
            connection = connection.model_copy(
                update={
                    "status": ConnectionStatus.READY,
                    "authenticated_at": now,
                    "last_validated_at": now,
                    "updated_at": now,
                }
            )
        return await self.repository.add_connection(connection)

    async def add_application(self, application: Application) -> Application:
        return await self.repository.add_application(application)

    async def add_environment(self, environment: Environment) -> Environment:
        application = await self.repository.get_application(
            environment.organisation_id,
            environment.application_id,
        )
        _tenant(application.organisation_id, environment.organisation_id)
        return await self.repository.add_environment(environment)

    async def add_service(self, service: ConsumerService) -> ConsumerService:
        environment = await self.repository.get_environment(
            service.organisation_id,
            service.environment_id,
        )
        if environment.application_id != service.application_id:
            raise ResourceConflictError("service application does not match its environment")
        await self.validate_service_connections(service)
        return await self.repository.add_service(service)

    async def add_discovered_service(
        self,
        organisation_id: str,
        service_id: str,
        application_id: str,
        environment_id: str,
        runtime_connection_id: str,
        runtime_resource: str,
    ) -> ConsumerService:
        environment = await self.get_environment(organisation_id, environment_id)
        if environment.application_id != application_id:
            raise ResourceConflictError("service application does not match its environment")
        resources = await self.list_runtime_resources(organisation_id, runtime_connection_id)
        selected = tuple(item for item in resources if item.reference == runtime_resource)
        if len(selected) != 1:
            raise ResourceConflictError("runtime service is no longer available")
        resource = selected[0]
        now = self.clock()
        return await self.add_service(
            ConsumerService(
                id=service_id,
                organisation_id=organisation_id,
                application_id=application_id,
                environment_id=environment_id,
                runtime_connection_id=runtime_connection_id,
                runtime_resource=resource.reference,
                display_name=resource.display_name,
                endpoint=resource.endpoint,
                identity=resource.identity,
                created_at=now,
                updated_at=now,
            )
        )

    async def add_application_setup(
        self,
        application: Application,
        environment: Environment,
        service: ConsumerService,
    ) -> tuple[Application, Environment, ConsumerService]:
        _tenant(application.organisation_id, environment.organisation_id)
        _tenant(application.organisation_id, service.organisation_id)
        if environment.application_id != application.id:
            raise ResourceConflictError("environment does not belong to the application")
        if service.application_id != application.id or service.environment_id != environment.id:
            raise ResourceConflictError("service does not belong to the application environment")
        await self.validate_service_connections(service)
        return await self.repository.add_application_setup(application, environment, service)

    async def add_discovered_application_setup(
        self,
        organisation_id: str,
        application_id: str,
        environment_id: str,
        service_id: str,
        runtime_connection_id: str,
        runtime_resource: str,
        environment_name: str | None,
    ) -> tuple[Application, Environment, ConsumerService]:
        resources = await self.list_runtime_resources(organisation_id, runtime_connection_id)
        selected = tuple(item for item in resources if item.reference == runtime_resource)
        if len(selected) != 1:
            raise ResourceConflictError("runtime service is no longer available")
        resource = selected[0]
        resolved_environment = resource.environment_name or environment_name
        if not resolved_environment:
            raise ResourceConflictError("runtime service environment is unavailable")
        now = self.clock()
        application = Application(
            id=application_id,
            organisation_id=organisation_id,
            display_name=resource.display_name,
            created_at=now,
            updated_at=now,
        )
        environment = Environment(
            id=environment_id,
            organisation_id=organisation_id,
            application_id=application_id,
            display_name=resolved_environment,
            production=(
                resource.production
                if resource.production is not None
                else resolved_environment.lower() == "production"
            ),
            region=_required_text(resource.region, "runtime region"),
            created_at=now,
            updated_at=now,
        )
        service = ConsumerService(
            id=service_id,
            organisation_id=organisation_id,
            application_id=application_id,
            environment_id=environment_id,
            runtime_connection_id=runtime_connection_id,
            runtime_resource=resource.reference,
            display_name=resource.display_name,
            endpoint=resource.endpoint,
            identity=resource.identity,
            created_at=now,
            updated_at=now,
        )
        return await self.add_application_setup(application, environment, service)

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
        preferences: ControlPreferences,
        actor_id: str,
        service_setup: tuple[Application, Environment, ConsumerService] | None = None,
    ) -> ManagedCredential:
        return await self._credentials.import_credential(
            credential, generation, bindings, preferences, actor_id, service_setup
        )

    async def import_discovered_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        consumer: RuntimeConsumerSetup,
        preferences: ControlPreferences,
        actor_id: str,
    ) -> ManagedCredential:
        return await self._credentials.import_discovered_credential(
            credential, generation, consumer, preferences, actor_id
        )

    async def resolve_credential(
        self,
        organisation_id: str,
        connection_id: str,
        secret_store_connection_id: str,
        secret_reference: str,
    ) -> ProviderCredentialMetadata:
        return await self._credentials.resolve_credential(
            organisation_id, connection_id, secret_store_connection_id, secret_reference
        )

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
        return await self._changes.update_connection(
            organisation_id,
            resource_id,
            expected_revision,
            display_name=display_name,
            capabilities=capabilities,
            allowed_resources=allowed_resources,
            region=region,
        )

    async def update_application(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
        repository_ids: tuple[str, ...] | None = None,
    ) -> Application:
        return await self._changes.update_application(
            organisation_id,
            resource_id,
            expected_revision,
            display_name=display_name,
            repository_ids=repository_ids,
        )

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
        return await self._changes.update_environment(
            organisation_id,
            resource_id,
            expected_revision,
            display_name=display_name,
            production=production,
            region=region,
        )

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
        return await self._changes.update_service(
            organisation_id,
            resource_id,
            expected_revision,
            display_name=display_name,
            runtime_connection_id=runtime_connection_id,
            telemetry_connection_ids=telemetry_connection_ids,
            runtime_resource=runtime_resource,
            endpoint=endpoint,
            repository=repository,
            identity=identity,
        )

    async def update_credential(
        self,
        organisation_id: str,
        resource_id: str,
        expected_revision: int,
        *,
        display_name: str | None = None,
    ) -> ManagedCredential:
        return await self._changes.update_credential(
            organisation_id, resource_id, expected_revision, display_name=display_name
        )

    async def update_controls(
        self,
        organisation_id: str,
        credential_id: str,
        expected_revision: int,
        version_id: str,
        preferences: ControlPreferences,
        actor_id: str,
    ) -> tuple[ManagedCredential, ControlVersion]:
        return await self._changes.update_controls(
            organisation_id, credential_id, expected_revision, version_id, preferences, actor_id
        )

    async def archive_connection(
        self, organisation_id: str, resource_id: str, expected_revision: int, cascade: bool = False
    ) -> Connection:
        return await self._changes.archive_connection(
            organisation_id, resource_id, expected_revision, cascade
        )

    async def archive_application(
        self, organisation_id: str, resource_id: str, expected_revision: int, cascade: bool = False
    ) -> Application:
        return await self._changes.archive_application(
            organisation_id, resource_id, expected_revision, cascade
        )

    async def archive_environment(
        self, organisation_id: str, resource_id: str, expected_revision: int, cascade: bool = False
    ) -> Environment:
        return await self._changes.archive_environment(
            organisation_id, resource_id, expected_revision, cascade
        )

    async def archive_service(
        self, organisation_id: str, resource_id: str, expected_revision: int, cascade: bool = False
    ) -> ConsumerService:
        return await self._changes.archive_service(
            organisation_id, resource_id, expected_revision, cascade
        )

    async def archive_credential(
        self, organisation_id: str, resource_id: str, expected_revision: int, cascade: bool = False
    ) -> ManagedCredential:
        return await self._changes.archive_credential(
            organisation_id, resource_id, expected_revision, cascade
        )
