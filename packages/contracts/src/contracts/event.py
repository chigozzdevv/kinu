from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.state import RunStatus, Stage


class EventKind(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    LEASE_RENEWED = "run.lease-renewed"
    STAGE_COMPLETED = "run.stage-completed"
    RUN_PAUSED = "run.paused"
    RUN_RESUMED = "run.resumed"
    CLEANUP_REQUIRED = "run.cleanup-required"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RECOVERY_STARTED = "run.recovery-started"
    RECOVERY_COMPLETED = "run.recovery-completed"
    RUN_COMPLETED = "run.completed"


class RunEvent(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    credential_id: Identifier
    kind: EventKind
    revision: int = Field(ge=0)
    stage: Stage
    status: RunStatus
    actor_id: Identifier
    occurred_at: AwareDatetime
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class OutboxEvent(Contract):
    event: RunEvent
    available_at: AwareDatetime
    attempts: int = Field(default=0, ge=0)
    lease_owner: Identifier | None = None
    lease_expires_at: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
    publisher_message_id: str | None = Field(default=None, min_length=1, max_length=256)
    dead_lettered_at: AwareDatetime | None = None
    dead_letter_reason: str | None = Field(default=None, min_length=1, max_length=1024)
    last_error: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def validate_delivery(self) -> "OutboxEvent":
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("outbox lease owner and expiry must be set together")
        if self.published_at is not None and self.lease_owner is not None:
            raise ValueError("a published event cannot remain leased")
        if (self.published_at is None) != (self.publisher_message_id is None):
            raise ValueError("published time and publisher message ID must be set together")
        if (self.dead_lettered_at is None) != (self.dead_letter_reason is None):
            raise ValueError("dead-letter time and reason must be set together")
        if self.published_at is not None and self.dead_lettered_at is not None:
            raise ValueError("an outbox event cannot be published and dead-lettered")
        if self.dead_lettered_at is not None and self.lease_owner is not None:
            raise ValueError("a dead-lettered event cannot remain leased")
        return self
