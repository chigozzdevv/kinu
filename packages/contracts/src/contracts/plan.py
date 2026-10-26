from enum import StrEnum

from pydantic import Field, field_serializer, model_validator

from contracts.base import Contract, Identifier
from contracts.state import Stage


class RotationStrategy(StrEnum):
    PARALLEL = "parallel"
    DUAL = "dual-slot"
    IMMEDIATE = "immediate"
    MULTI = "multi-consumer"


class OperationStep(Contract):
    id: Identifier
    stage: Stage
    tool: str = Field(min_length=3, max_length=128)
    operation: str = Field(min_length=1, max_length=96)
    objective: str = Field(min_length=1, max_length=1024)
    parameters: dict[str, str | int | bool | tuple[str, ...]] = Field(default_factory=dict)
    protected: bool = False
    evidence_checks: frozenset[str] = Field(min_length=1)

    @field_serializer("evidence_checks")
    def serialize_evidence_checks(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


class RuntimeDeployment(Contract):
    binding_id: Identifier
    connection_id: Identifier
    service: str = Field(min_length=1, max_length=512)
    candidate_revision: str = Field(min_length=1, max_length=512)
    rollback_revision: str = Field(min_length=1, max_length=512)


class RotationPlan(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    credential_id: Identifier
    control_version: Identifier
    browser_playbook_version: Identifier | None = None
    strategy: RotationStrategy
    target_scopes: frozenset[str]
    consumer_ids: tuple[Identifier, ...] = Field(min_length=1)
    rollout: tuple[int, ...] = (5, 25, 50, 100)
    observation_seconds: int = Field(gt=0, le=604800)
    recovery_ids: dict[Stage, Identifier] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rollout(self) -> "RotationPlan":
        if tuple(sorted(set(self.rollout))) != self.rollout:
            raise ValueError("rollout percentages must be unique and increasing")
        if not self.rollout or self.rollout[-1] != 100:
            raise ValueError("rollout must end at 100 percent")
        if any(percent <= 0 or percent > 100 for percent in self.rollout):
            raise ValueError("rollout percentages must be between 1 and 100")
        return self
