from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier
from contracts.evidence import StageProof
from contracts.plan import RuntimeDeployment
from contracts.recovery import RecoveryMode
from contracts.state import RunStatus, Stage


class Trigger(Contract):
    source: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="rotation-requested", min_length=1, max_length=96)
    event_id: str = Field(min_length=1, max_length=256)
    actor_id: Identifier
    reason: str = Field(min_length=1, max_length=1024)
    urgency: str = Field(min_length=1, max_length=32)
    received_at: AwareDatetime


class Lease(Contract):
    owner_id: Identifier
    fencing_token: int = Field(gt=0)
    expires_at: AwareDatetime


class Failure(Contract):
    code: str = Field(min_length=1, max_length=96)
    message: str = Field(min_length=1, max_length=1024)
    retryable: bool
    evidence_ids: tuple[Identifier, ...] = ()


class RunStep(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    operation: str = Field(min_length=1, max_length=96)
    command_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor_id: Identifier
    before_stage: Stage | None = None
    after_stage: Stage
    before_status: RunStatus | None = None
    after_status: RunStatus
    revision: int = Field(ge=0)
    proof: StageProof | None = None
    recorded_at: AwareDatetime


class RotationRun(Contract):
    id: Identifier
    organisation_id: Identifier
    credential_id: Identifier
    trigger: Trigger
    control_version: Identifier
    stage: Stage = Stage.TRIGGER
    status: RunStatus = RunStatus.PENDING
    lease: Lease | None = None
    fencing_token: int = Field(default=0, ge=0)
    browser_playbook_version: Identifier | None = None
    plan_id: Identifier | None = None
    plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    current_generation_id: Identifier | None = None
    target_generation_id: Identifier | None = None
    deployments: tuple[RuntimeDeployment, ...] = ()
    failure: Failure | None = None
    recovery_id: Identifier | None = None
    recovery_stage: Stage | None = None
    recovery_mode: RecoveryMode | None = None
    recovery_failure: Failure | None = None
    recovery_evidence_ids: tuple[Identifier, ...] = ()
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> "RotationRun":
        if self.status is RunStatus.COMPLETED and self.stage is not Stage.COMPLETE:
            raise ValueError("a completed run must be in the complete stage")
        if self.lease is not None and self.lease.fencing_token != self.fencing_token:
            raise ValueError("the active lease must use the run fencing token")
        problem_states = {RunStatus.FAILED, RunStatus.CLEANUP}
        if self.status in problem_states and self.failure is None:
            raise ValueError("failed and cleanup-required runs require failure details")
        if self.status not in problem_states and self.failure is not None:
            raise ValueError("failure details require a failed or cleanup-required run")
        recovery_bound = self.status in {RunStatus.RECOVERING, RunStatus.COMPENSATED}
        if recovery_bound != (self.recovery_stage is not None):
            raise ValueError("recovery states require the failed stage binding")
        if self.status is RunStatus.COMPENSATED and (
            self.recovery_stage is None
            or self.recovery_mode is None
            or not self.recovery_evidence_ids
        ):
            raise ValueError("compensated runs require a complete recovery outcome")
        return self
