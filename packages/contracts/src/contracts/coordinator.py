from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.command import StageBindings
from contracts.recovery import RecoveryMode
from contracts.state import Stage


class StageExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PAUSED = "paused"
    FAILED = "failed"
    RECOVERED = "recovered"


class StageExecutionRequest(Contract):
    organisation_id: Identifier
    run_id: Identifier
    stage: Stage
    expected_revision: int = Field(ge=0)
    fencing_token: int = Field(gt=0)


class StageExecutionResult(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    stage: Stage
    status: StageExecutionStatus
    checks: frozenset[str] = frozenset()
    evidence_ids: tuple[Identifier, ...] = ()
    bindings: StageBindings = StageBindings()
    recovery_mode: RecoveryMode | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=1024)
    retryable: bool = False
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_result(self) -> "StageExecutionResult":
        completed = self.status in {
            StageExecutionStatus.SUCCEEDED,
            StageExecutionStatus.RECOVERED,
        }
        if completed and (not self.checks or not self.evidence_ids or self.reason is not None):
            raise ValueError("completed stage execution requires checks and evidence")
        if not completed and not self.reason:
            raise ValueError("paused and failed stage execution requires a reason")
        if (self.status is StageExecutionStatus.RECOVERED) != (self.recovery_mode is not None):
            raise ValueError("recovered execution requires exactly one recovery mode")
        return self
