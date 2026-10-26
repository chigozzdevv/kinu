from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class AuditEvent(Contract):
    id: Identifier
    organisation_id: Identifier
    sequence: int = Field(ge=0)
    kind: str = Field(min_length=1, max_length=128)
    actor_id: Identifier
    resource: str = Field(min_length=1, max_length=1024)
    run_id: Identifier | None = None
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    evidence_ids: tuple[Identifier, ...] = ()
    previous_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    occurred_at: AwareDatetime
    region: str = Field(min_length=3, max_length=32)

    @model_validator(mode="after")
    def validate_genesis(self) -> "AuditEvent":
        if self.sequence == 0 and self.previous_hash != "0" * 64:
            raise ValueError("the first audit event must reference the genesis hash")
        return self


class AuditOutbox(Contract):
    event: AuditEvent
    available_at: AwareDatetime
    attempts: int = Field(default=0, ge=0)
    lease_owner: Identifier | None = None
    lease_expires_at: AwareDatetime | None = None
    logged_at: AwareDatetime | None = None
    provider_receipt: str | None = Field(default=None, min_length=1, max_length=512)
    dead_lettered_at: AwareDatetime | None = None
    dead_letter_reason: str | None = Field(default=None, min_length=1, max_length=1024)
    last_error: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def validate_delivery(self) -> "AuditOutbox":
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("audit lease owner and expiry must be set together")
        if self.logged_at is not None and self.lease_owner is not None:
            raise ValueError("a logged audit event cannot remain leased")
        if (self.logged_at is None) != (self.provider_receipt is None):
            raise ValueError("audit log time and provider receipt must be set together")
        if (self.dead_lettered_at is None) != (self.dead_letter_reason is None):
            raise ValueError("audit dead-letter time and reason must be set together")
        if self.logged_at is not None and self.dead_lettered_at is not None:
            raise ValueError("an audit event cannot be logged and dead-lettered")
        if self.dead_lettered_at is not None and self.lease_owner is not None:
            raise ValueError("a dead-lettered audit event cannot remain leased")
        return self


class Evidence(Contract):
    id: Identifier
    organisation_id: Identifier
    kind: str = Field(min_length=1, max_length=96)
    resource: str = Field(min_length=1, max_length=1024)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str = Field(min_length=1, max_length=128)
    size: int = Field(ge=0)
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    region: str = Field(min_length=3, max_length=32)
