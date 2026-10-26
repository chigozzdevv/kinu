from enum import StrEnum

from pydantic import Field, model_validator

from contracts.base import Contract, Identifier
from contracts.state import Stage


class RecoveryMode(StrEnum):
    RETRY = "retry"
    ROLLBACK = "rollback"
    ROLLFORWARD = "rollforward"
    CLEANUP = "cleanup"
    ESCALATE = "escalate"


class RecoveryAction(Contract):
    tool: str = Field(min_length=3, max_length=128)
    operation: str = Field(min_length=1, max_length=96)
    parameters: dict[str, str | int | bool | tuple[str, ...]] = Field(default_factory=dict)
    protected: bool = False


class RecoveryBranch(Contract):
    mode: RecoveryMode
    actions: tuple[RecoveryAction, ...] = Field(min_length=1)
    preserves_old_generation: bool

    @model_validator(mode="after")
    def validate_retry(self) -> "RecoveryBranch":
        if self.mode is RecoveryMode.RETRY and any(item.protected for item in self.actions):
            raise ValueError("retry recovery cannot introduce protected mutations")
        return self


class RecoveryPlan(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    failed_stage: Stage
    mode: RecoveryMode
    steps: tuple[RecoveryAction, ...] = Field(min_length=1)
    preserves_old_generation: bool
    requires_approval: bool


class RecoveryResult(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    recovery_id: Identifier
    failed_stage: Stage
    mode: RecoveryMode
    checks: frozenset[str] = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
