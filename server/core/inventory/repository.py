from datetime import datetime
from typing import Protocol

from contracts import (
    Application,
    Connection,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlVersion,
    CredentialGeneration,
    Environment,
    ManagedCredential,
    PlaybookVersion,
    ProbeVersion,
)

_BROWSER_CAPABILITIES = frozenset({"browser.execute", "browser.authenticate"})
InventoryResource = Connection | Application | Environment | ConsumerService | ManagedCredential


class InventoryRepository(Protocol):
    async def add_connection(self, value: Connection) -> Connection: ...

    async def add_application(self, value: Application) -> Application: ...

    async def add_environment(self, value: Environment) -> Environment: ...

    async def add_service(self, value: ConsumerService) -> ConsumerService: ...

    async def add_application_setup(
        self,
        application: Application,
        environment: Environment,
        service: ConsumerService,
    ) -> tuple[Application, Environment, ConsumerService]: ...

    async def get_application(self, organisation_id: str, resource_id: str) -> Application: ...

    async def get_environment(self, organisation_id: str, resource_id: str) -> Environment: ...

    async def get_service(self, organisation_id: str, resource_id: str) -> ConsumerService: ...

    async def get_connection(self, organisation_id: str, resource_id: str) -> Connection: ...

    async def get_credential(self, organisation_id: str, resource_id: str) -> ManagedCredential: ...

    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion: ...

    async def get_playbook_version(
        self,
        organisation_id: str,
        playbook_id: str,
        version_id: str,
    ) -> PlaybookVersion: ...

    async def replace_connection(self, value: Connection, expected_revision: int) -> Connection: ...

    async def replace_application(
        self, value: Application, expected_revision: int
    ) -> Application: ...

    async def replace_environment(
        self, value: Environment, expected_revision: int
    ) -> Environment: ...

    async def replace_service(
        self, value: ConsumerService, expected_revision: int
    ) -> ConsumerService: ...

    async def replace_credential(
        self, value: ManagedCredential, expected_revision: int
    ) -> ManagedCredential: ...

    async def attach_playbook(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        playbook_id: str,
        version_id: str,
        updated_at: datetime,
    ) -> Connection: ...

    async def update_authentication(
        self,
        organisation_id: str,
        connection_id: str,
        expected_revision: int,
        authorization_reference: str | None,
        status: ConnectionStatus,
        updated_at: datetime,
    ) -> Connection: ...

    async def import_credential(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        bindings: tuple[ConsumerBinding, ...],
        controls: ControlVersion,
        probes: tuple[ProbeVersion, ...],
        service_setup: tuple[Application, Environment, ConsumerService] | None = None,
    ) -> ManagedCredential: ...

    async def replace_controls(
        self,
        credential: ManagedCredential,
        expected_revision: int,
        controls: ControlVersion,
    ) -> tuple[ManagedCredential, ControlVersion]: ...

    async def archive_inventory(
        self,
        resources: tuple[
            Connection | Application | Environment | ConsumerService | ManagedCredential, ...
        ],
        bindings: tuple[ConsumerBinding, ...],
    ) -> None: ...

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]: ...

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]: ...

    async def applications(self, organisation_id: str) -> tuple[Application, ...]: ...

    async def environments(self, organisation_id: str) -> tuple[Environment, ...]: ...

    async def services(self, organisation_id: str) -> tuple[ConsumerService, ...]: ...

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]: ...


class ProviderMetadataLister(Protocol):
    async def metadata(self, connection: Connection) -> tuple[dict[str, object], ...]: ...


class CredentialBindingVerifier(Protocol):
    async def credential_identity(
        self,
        connection: Connection,
        secret_connection: Connection,
        secret_reference: str,
    ) -> str: ...


class SecretMetadataLister(Protocol):
    async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]: ...

    async def versions_for(
        self, connection: Connection, secret: str
    ) -> tuple[dict[str, object], ...]: ...


class RuntimeMetadataLister(Protocol):
    async def resources_for(self, connection: Connection) -> tuple[dict[str, object], ...]: ...
