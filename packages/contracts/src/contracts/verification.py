from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class ProbeKind(StrEnum):
    HTTP = "http"
    EMAIL = "email"
    PROVIDER = "provider"
    SECRET = "secret-store"
    RUNTIME = "runtime"
    TELEMETRY = "telemetry"
    GENERATION = "generation"
    CREDENTIAL = "credential-authentication"


class VerificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class ProbeState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class GenerationBinding(StrEnum):
    NONE = "none"
    TARGET = "target"
    CURRENT = "current"


class TargetBinding(StrEnum):
    STATIC = "static"
    PROVIDER_ID = "provider-id"
    SECRET_REFERENCE = "secret-reference"


class DownstreamConfirmation(Contract):
    target: str = Field(min_length=1, max_length=1024)
    method: str = Field(default="GET", min_length=1, max_length=16)
    headers: dict[str, str] = Field(default_factory=dict)
    expected_status: tuple[int, ...] = (200,)
    required_fields: dict[str, str | int | bool] = Field(min_length=1)
    correlation_field: str = Field(min_length=1, max_length=128)
    interval_seconds: int = Field(default=2, ge=1, le=30)


class TelemetryThresholds(Contract):
    minimum_count: int = Field(default=1, ge=1, le=1000)
    maximum_error_count: int = Field(default=0, ge=0, le=1000)
    maximum_auth_failure_count: int = Field(default=0, ge=0, le=1000)
    window_seconds: int = Field(default=300, ge=60, le=86400)


class ProbeDefinition(Contract):
    id: Identifier
    organisation_id: Identifier
    kind: ProbeKind
    connection_id: Identifier
    target: str = Field(min_length=1, max_length=1024)
    method: str = Field(default="GET", min_length=1, max_length=16)
    headers: dict[str, str] = Field(default_factory=dict)
    body_reference: str | None = Field(default=None, max_length=1024)
    secret_reference: str | None = Field(default=None, max_length=1024)
    secret_connection_id: Identifier | None = None
    expected_status: tuple[int, ...] = (200,)
    expected_generation_id: Identifier | None = None
    generation_binding: GenerationBinding = GenerationBinding.NONE
    target_binding: TargetBinding = TargetBinding.STATIC
    required_fields: dict[str, str | int | bool] = Field(default_factory=dict)
    confirmation: DownstreamConfirmation | None = None
    telemetry: TelemetryThresholds | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    negative: bool = False

    @model_validator(mode="after")
    def validate_probe(self) -> "ProbeDefinition":
        if self.kind is ProbeKind.EMAIL and self.confirmation is None:
            raise ValueError("email probes require downstream confirmation")
        if self.kind is not ProbeKind.EMAIL and self.confirmation is not None:
            raise ValueError("downstream confirmation is only valid for email probes")
        if self.kind is ProbeKind.TELEMETRY and self.telemetry is None:
            raise ValueError("telemetry probes require explicit thresholds")
        if self.kind is not ProbeKind.TELEMETRY and self.telemetry is not None:
            raise ValueError("telemetry thresholds are only valid for telemetry probes")
        if self.generation_binding is GenerationBinding.NONE and self.expected_generation_id:
            raise ValueError("an expected generation requires a generation binding")
        if self.target_binding is not TargetBinding.STATIC and self.kind not in {
            ProbeKind.CREDENTIAL,
            ProbeKind.PROVIDER,
            ProbeKind.SECRET,
        }:
            raise ValueError("dynamic targets are only valid for provider and secret probes")
        secret_bound = self.secret_reference is not None and self.secret_connection_id is not None
        if (self.secret_reference is None) != (self.secret_connection_id is None):
            raise ValueError("probe secret reference and connection must be set together")
        if (self.kind is ProbeKind.CREDENTIAL) != secret_bound:
            raise ValueError("credential authentication probes require one secret-store binding")
        return self


class ProbeVersion(Contract):
    id: Identifier
    organisation_id: Identifier
    probe_id: Identifier
    number: int = Field(gt=0)
    definition: ProbeDefinition
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: ProbeState
    created_by: Identifier
    created_at: AwareDatetime
    approved_by: Identifier | None = None
    approved_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_version(self) -> "ProbeVersion":
        if self.definition.id != self.id or self.definition.organisation_id != self.organisation_id:
            raise ValueError("probe definition identity must match its version")
        approved = self.approved_by is not None and self.approved_at is not None
        if (self.approved_by is None) != (self.approved_at is None):
            raise ValueError("probe approval identity and time must be set together")
        if self.state is ProbeState.ACTIVE and not approved:
            raise ValueError("active probe versions require approval")
        return self


class Probe(Contract):
    id: Identifier
    organisation_id: Identifier
    name: str = Field(min_length=1, max_length=160)
    latest_version: int = Field(default=0, ge=0)
    active_version_id: Identifier | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)


class ProbeResult(Contract):
    probe_id: Identifier
    status: VerificationStatus
    observed_status: int | None = None
    generation_id: Identifier | None = None
    checks: frozenset[str] = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    observations: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=1024)
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_error(self) -> "ProbeResult":
        if self.status is VerificationStatus.FAILED and not self.error:
            raise ValueError("failed probes require an error")
        return self


class VerificationReport(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    generation_id: Identifier
    status: VerificationStatus
    results: tuple[ProbeResult, ...] = Field(min_length=1)
    checks: frozenset[str] = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_summary(self) -> "VerificationReport":
        statuses = {result.status for result in self.results}
        if self.status is VerificationStatus.PASSED and statuses != {VerificationStatus.PASSED}:
            raise ValueError("a passed report cannot contain non-passing probes")
        return self
