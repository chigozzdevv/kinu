from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class ToolRequest(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    agent_id: Identifier
    tool: str = Field(min_length=1, max_length=128)
    connection_id: Identifier
    payload: dict[str, Any] = Field(default_factory=dict)
    fencing_token: int = Field(gt=0)


class ToolResult(Contract):
    request_id: Identifier
    succeeded: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=96)
    evidence_ids: tuple[Identifier, ...] = ()


class ToolAttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolAttempt(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool: str = Field(min_length=1, max_length=128)
    status: ToolAttemptStatus
    result: ToolResult | None = None
    checkpoint: ToolResult | None = None
    reconciliation: dict[str, str | int | bool | tuple[str, ...]] = Field(default_factory=dict)
    started_at: AwareDatetime
    lease_expires_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_terminal_result(self) -> "ToolAttempt":
        terminal = self.status is not ToolAttemptStatus.RUNNING
        if terminal != (self.result is not None and self.completed_at is not None):
            raise ValueError("terminal tool attempts require a result and completion time")
        if terminal and self.checkpoint is not None:
            raise ValueError("terminal tool attempts cannot retain a checkpoint")
        return self
