from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERIFIED = "verified"


class IncidentStatus(StrEnum):
    NEW = "new"
    CORRELATING = "correlating"
    ACTION = "action-required"
    ROTATING = "rotation-started"
    CONTAINED = "contained"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class SourceResource(Contract):
    connection_id: Identifier | None = None
    credential_id: Identifier | None = None
    repository: str | None = Field(default=None, max_length=256)
    project: str | None = Field(default=None, max_length=256)
    service: str | None = Field(default=None, max_length=256)
    environment: str | None = Field(default=None, max_length=128)
    provider: str | None = Field(default=None, max_length=64)
    provider_id: str | None = Field(default=None, max_length=256)


class IngestionEvent(Contract):
    id: Identifier
    organisation_id: Identifier
    source: str = Field(min_length=1, max_length=64)
    source_event_id: str = Field(min_length=1, max_length=256)
    kind: str = Field(min_length=1, max_length=96)
    observed_at: AwareDatetime
    severity: Severity
    confidence: Confidence
    resource: SourceResource
    source_reference: str = Field(min_length=1, max_length=1024)
    received_at: AwareDatetime

    def stable_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"received_at"})


class CorrelationCandidate(Contract):
    credential_id: Identifier
    confidence: Confidence
    reasons: tuple[str, ...] = Field(min_length=1)
    consumer_ids: tuple[Identifier, ...] = ()


class Incident(Contract):
    id: Identifier
    organisation_id: Identifier
    event_id: Identifier
    source: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="security-signal", min_length=1, max_length=96)
    source_event_id: str = Field(min_length=1, max_length=256)
    severity: Severity
    confidence: Confidence
    status: IncidentStatus
    resource: SourceResource
    candidates: tuple[CorrelationCandidate, ...] = ()
    credential_id: Identifier | None = None
    run_id: Identifier | None = None
    dismissal_reason: str | None = Field(default=None, max_length=1024)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_resolution(self) -> "Incident":
        if self.status is IncidentStatus.DISMISSED and not self.dismissal_reason:
            raise ValueError("a dismissed incident requires a reason")
        if self.status in {IncidentStatus.CONTAINED, IncidentStatus.RESOLVED} and not self.run_id:
            raise ValueError("a contained or resolved incident requires a run")
        return self
