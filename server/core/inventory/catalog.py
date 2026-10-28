from collections.abc import Callable
from datetime import UTC, datetime

from connectors.base.errors import ConnectorError
from contracts import (
    Application,
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlVersion,
    Environment,
    ManagedCredential,
    ProviderCredentialMetadata,
    RuntimeResourceMetadata,
    SecretResourceMetadata,
    SecretVersionMetadata,
)

from core.errors import ResourceConflictError
from core.inventory.repository import (
    CredentialBindingVerifier,
    InventoryRepository,
    ProviderMetadataLister,
    RuntimeMetadataLister,
    SecretMetadataLister,
)
from core.inventory.validation import (
    _active,
    _domain_covered,
    _domain_pattern,
    _required_text,
    _resource_covered,
)

_BROWSER_CAPABILITIES = frozenset({"browser.execute", "browser.authenticate"})


class InventoryCatalog:
    @property
    def provider_available(self) -> bool:
        return self.provider_metadata is not None and self.credential_verifier is not None

    def __init__(
        self,
        repository: InventoryRepository,
        clock: Callable[[], datetime] | None = None,
        provider_metadata: ProviderMetadataLister | None = None,
        credential_verifier: CredentialBindingVerifier | None = None,
        secret_metadata: SecretMetadataLister | None = None,
        runtime_metadata: RuntimeMetadataLister | None = None,
    ) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(UTC))
        self.provider_metadata = provider_metadata
        self.credential_verifier = credential_verifier
        self.secret_metadata = secret_metadata
        self.runtime_metadata = runtime_metadata

    async def validate_service_connections(self, service: ConsumerService) -> None:
        runtime = await self.repository.get_connection(
            service.organisation_id,
            service.runtime_connection_id,
        )
        if (
            ConnectionRole.RUNTIME not in runtime.roles
            or runtime.interface is not ConnectionInterface.API
        ):
            raise ResourceConflictError("service requires an API runtime connection")
        if not _resource_covered(service.runtime_resource, runtime.allowed_resources):
            raise ResourceConflictError("service runtime resource escapes the connection boundary")
        for connection_id in service.telemetry_connection_ids:
            telemetry = await self.repository.get_connection(
                service.organisation_id,
                connection_id,
            )
            if (
                ConnectionRole.TELEMETRY not in telemetry.roles
                or telemetry.interface is not ConnectionInterface.API
            ):
                raise ResourceConflictError("service requires API telemetry connections")

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection:
        return await self.repository.get_connection(organisation_id, resource_id)

    async def list_provider_credentials(
        self, organisation_id: str, connection_id: str
    ) -> tuple[ProviderCredentialMetadata, ...]:
        connection = await self.get_connection(organisation_id, connection_id)
        if connection.archived_at is not None:
            raise ResourceConflictError("archived connection cannot discover credentials")
        if ConnectionRole.PROVIDER not in connection.roles:
            raise ResourceConflictError("credential discovery requires a provider connection")
        if connection.status is not ConnectionStatus.READY:
            raise ResourceConflictError("provider connection is not ready")
        if "provider.listCredentialMetadata" not in connection.capabilities:
            raise ResourceConflictError("provider connection cannot list credential metadata")
        if self.provider_metadata is None:
            raise ResourceConflictError("provider credential discovery is unavailable")
        metadata = await self.provider_metadata.metadata(connection)
        return tuple(ProviderCredentialMetadata.model_validate(item) for item in metadata)

    async def list_secret_resources(
        self, organisation_id: str, connection_id: str
    ) -> tuple[SecretResourceMetadata, ...]:
        connection = await self.secret_connection(organisation_id, connection_id)
        if self.secret_metadata is None:
            raise ResourceConflictError("secret metadata discovery is unavailable")
        try:
            resources = await self.secret_metadata.resources_for(connection)
        except ConnectorError as error:
            raise ResourceConflictError(f"Secret discovery unavailable: {error}") from None
        return tuple(
            SecretResourceMetadata(
                reference=_required_text(item.get("name"), "secret resource"),
                display_name=_required_text(item.get("name"), "secret resource").rsplit("/", 1)[-1],
            )
            for item in resources
        )

    async def list_runtime_resources(
        self, organisation_id: str, connection_id: str
    ) -> tuple[RuntimeResourceMetadata, ...]:
        connection = await self.get_connection(organisation_id, connection_id)
        if (
            connection.archived_at is not None
            or connection.status is not ConnectionStatus.READY
            or connection.interface is not ConnectionInterface.API
            or ConnectionRole.RUNTIME not in connection.roles
        ):
            raise ResourceConflictError("runtime discovery requires a ready API runtime connection")
        if "runtime.listServices" not in connection.capabilities:
            raise ResourceConflictError("runtime connection cannot list services")
        if self.runtime_metadata is None:
            raise ResourceConflictError("runtime resource discovery is unavailable")
        try:
            resources = await self.runtime_metadata.resources_for(connection)
        except ConnectorError as error:
            raise ResourceConflictError(f"Runtime discovery unavailable: {error}") from None
        metadata = tuple(RuntimeResourceMetadata.model_validate(item) for item in resources)
        if any(
            not _resource_covered(resource.reference, connection.allowed_resources)
            for resource in metadata
        ):
            raise ResourceConflictError("runtime discovery escaped the connection boundary")
        return tuple(sorted(metadata, key=lambda item: item.display_name.lower()))

    async def list_secret_versions(
        self, organisation_id: str, connection_id: str, secret: str
    ) -> tuple[SecretVersionMetadata, ...]:
        connection = await self.secret_connection(organisation_id, connection_id)
        if not _resource_covered(secret, connection.allowed_resources):
            raise ResourceConflictError("secret resource escapes the connection boundary")
        if self.secret_metadata is None:
            raise ResourceConflictError("secret metadata discovery is unavailable")
        try:
            versions = await self.secret_metadata.versions_for(connection, secret)
        except ConnectorError as error:
            raise ResourceConflictError(f"Secret discovery unavailable: {error}") from None
        return tuple(
            SecretVersionMetadata.model_validate(
                {
                    "reference": _required_text(item.get("name"), "secret version"),
                    "state": _required_text(item.get("state"), "secret version state"),
                    "created_at": item.get("createTime"),
                }
            )
            for item in versions
            if item.get("state") == "ENABLED"
        )

    async def secret_connection(self, organisation_id: str, connection_id: str) -> Connection:
        connection = await self.get_connection(organisation_id, connection_id)
        if (
            connection.archived_at is not None
            or connection.status is not ConnectionStatus.READY
            or connection.interface is not ConnectionInterface.API
            or ConnectionRole.SECRET_STORE not in connection.roles
        ):
            raise ResourceConflictError("secret discovery requires a ready secret-store connection")
        return connection

    async def get_application(self, organisation_id: str, resource_id: str) -> Application:
        return await self.repository.get_application(organisation_id, resource_id)

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment:
        return await self.repository.get_environment(organisation_id, resource_id)

    async def get_service(self, organisation_id: str, resource_id: str) -> ConsumerService:
        return await self.repository.get_service(organisation_id, resource_id)

    async def get_credential(self, organisation_id: str, resource_id: str) -> ManagedCredential:
        return await self.repository.get_credential(organisation_id, resource_id)

    async def get_controls(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        credential = await self.get_credential(organisation_id, credential_id)
        controls = await self.repository.get_control_version(
            organisation_id, credential_id, version_id
        )
        if controls.credential_id != credential.id or controls.organisation_id != organisation_id:
            raise ResourceConflictError("controls do not belong to the credential")
        return controls

    async def list_connections(self, organisation_id: str) -> tuple[Connection, ...]:
        return _active(await self.repository.connections(organisation_id))

    async def list_applications(self, organisation_id: str) -> tuple[Application, ...]:
        return _active(await self.repository.applications(organisation_id))

    async def list_environments(self, organisation_id: str) -> tuple[Environment, ...]:
        return _active(await self.repository.environments(organisation_id))

    async def list_services(self, organisation_id: str) -> tuple[ConsumerService, ...]:
        return _active(await self.repository.services(organisation_id))

    async def list_credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        return _active(await self.repository.credentials(organisation_id))

    async def graph(
        self,
        organisation_id: str,
    ) -> tuple[
        tuple[ManagedCredential, ...],
        tuple[ConsumerService, ...],
        tuple[ConsumerBinding, ...],
    ]:
        credentials = _active(await self.repository.credentials(organisation_id))
        consumer_services = _active(await self.repository.services(organisation_id))
        credential_ids = {credential.id for credential in credentials}
        service_ids = {service.id for service in consumer_services}
        bindings = tuple(
            binding
            for binding in await self.repository.bindings(organisation_id)
            if binding.credential_id in credential_ids and binding.service_id in service_ids
        )
        return (
            credentials,
            consumer_services,
            bindings,
        )

    async def validate_connection(self, connection: Connection) -> None:
        if connection.interface is ConnectionInterface.BROWSER:
            if not connection.allowed_resources or any(
                not _domain_pattern(value) for value in connection.allowed_resources
            ):
                raise ResourceConflictError("browser connection must declare allowed domains")
            if not connection.capabilities.intersection(_BROWSER_CAPABILITIES):
                raise ResourceConflictError("browser connection must declare a browser capability")

    async def validate_connection_usage(self, connection: Connection) -> None:
        credentials = _active(await self.repository.credentials(connection.organisation_id))
        consumer_services = _active(await self.repository.services(connection.organisation_id))
        if ConnectionRole.SECRET_STORE in connection.roles and any(
            credential.secret_store_connection_id == connection.id
            and not _resource_covered(credential.secret_reference, connection.allowed_resources)
            for credential in credentials
        ):
            raise ResourceConflictError("connection scope no longer covers an active secret")
        if ConnectionRole.RUNTIME in connection.roles and any(
            service.runtime_connection_id == connection.id
            and not _resource_covered(service.runtime_resource, connection.allowed_resources)
            for service in consumer_services
        ):
            raise ResourceConflictError("connection scope no longer covers an active runtime")
        if (
            connection.interface is ConnectionInterface.BROWSER
            and connection.playbook_id is not None
        ):
            assert connection.playbook_version_id is not None
            version = await self.repository.get_playbook_version(
                connection.organisation_id,
                connection.playbook_id,
                connection.playbook_version_id,
            )
            if any(
                not _domain_covered(domain, connection.allowed_resources)
                for domain in version.definition.allowed_domains
            ):
                raise ResourceConflictError("connection domains no longer cover its playbook")
