from contracts import (
    Application,
    Connection,
    ConsumerBinding,
    ConsumerService,
    Contract,
    ControlPreferences,
    ControlVersion,
    CredentialGeneration,
    Environment,
    Identifier,
    ManagedCredential,
    RuntimeConsumerSetup,
    SetupSession,
)
from pydantic import AwareDatetime, Field, model_validator


class ImportCredentialRequest(Contract):
    credential: ManagedCredential
    generation: CredentialGeneration
    consumer: RuntimeConsumerSetup
    controls: ControlPreferences


class ResolveCredentialRequest(Contract):
    secret_store_connection_id: Identifier
    secret_reference: str = Field(min_length=1, max_length=1024)


class InventoryGraph(Contract):
    credentials: tuple[ManagedCredential, ...]
    services: tuple[ConsumerService, ...]
    bindings: tuple[ConsumerBinding, ...]


class ApplicationSetupRequest(Contract):
    application_id: Identifier
    environment_id: Identifier
    service_id: Identifier
    runtime_connection_id: Identifier
    runtime_resource: str = Field(min_length=1, max_length=512)
    environment_name: str | None = Field(default=None, min_length=1, max_length=160)


class ApplicationSetupResponse(Contract):
    application: Application
    environment: Environment
    service: ConsumerService


class ServiceSetupRequest(Contract):
    id: Identifier
    application_id: Identifier
    environment_id: Identifier
    runtime_connection_id: Identifier
    runtime_resource: str = Field(min_length=1, max_length=512)


class BeginSetupRequest(Contract):
    extra_domains: tuple[str, ...] = Field(default=(), max_length=20)


class BeginSetupResponse(Contract):
    session: SetupSession
    token: str = Field(min_length=32)
    setup_url: str = Field(min_length=12)
    gateway_url: str = Field(min_length=12)
    initial_url: str = Field(min_length=12, max_length=2048)
    expires_at: AwareDatetime


class CompleteSetupRequest(Contract):
    expected_revision: int = Field(ge=0)
    token: str = Field(min_length=32, max_length=256)


class CompleteSetupResponse(Contract):
    session: SetupSession
    connection: Connection
    resumed_run_ids: tuple[Identifier, ...] = ()


class AbortSetupRequest(Contract):
    expected_revision: int = Field(ge=0)


class RevisionRequest(Contract):
    expected_revision: int = Field(ge=0)


class ArchiveRequest(RevisionRequest):
    cascade: bool = False


class ConnectionUpdateRequest(RevisionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    capabilities: frozenset[str] | None = Field(default=None, min_length=1)
    allowed_resources: tuple[str, ...] | None = Field(default=None, min_length=1)
    region: str | None = Field(default=None, min_length=3, max_length=32)

    @model_validator(mode="after")
    def require_change(self) -> "ConnectionUpdateRequest":
        return _changed(self)


class ApplicationUpdateRequest(RevisionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    repository_ids: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "ApplicationUpdateRequest":
        return _changed(self)


class EnvironmentUpdateRequest(RevisionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    production: bool | None = None
    region: str | None = Field(default=None, min_length=3, max_length=32)

    @model_validator(mode="after")
    def require_change(self) -> "EnvironmentUpdateRequest":
        return _changed(self)


class ServiceUpdateRequest(RevisionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    runtime_connection_id: Identifier | None = None
    telemetry_connection_ids: tuple[Identifier, ...] | None = None
    runtime_resource: str | None = Field(default=None, min_length=1, max_length=512)
    endpoint: str | None = Field(default=None, max_length=2048)
    repository: str | None = Field(default=None, min_length=1, max_length=256)
    identity: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_change(self) -> "ServiceUpdateRequest":
        return _changed(self)


class CredentialUpdateRequest(RevisionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_change(self) -> "CredentialUpdateRequest":
        return _changed(self)


class ControlsUpdateRequest(RevisionRequest):
    version_id: Identifier
    controls: ControlPreferences


class CredentialControlsResponse(Contract):
    credential: ManagedCredential
    controls: ControlVersion


def _changed[T: RevisionRequest](value: T) -> T:
    if value.model_fields_set == {"expected_revision"}:
        raise ValueError("at least one editable field is required")
    return value
