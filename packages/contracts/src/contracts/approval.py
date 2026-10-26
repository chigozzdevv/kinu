from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    MORE_EVIDENCE = "more-evidence"
    EXTEND = "extend-observation"


class ApprovalEvidenceKind(StrEnum):
    VERIFICATION = "verification"
    PLAN = "plan"


class ApprovalEvidenceSnapshot(Contract):
    approval_id: Identifier
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: ApprovalEvidenceKind
    status: str = Field(min_length=1, max_length=32)
    checks: tuple[str, ...] = ()
    evidence_count: int = Field(default=0, ge=0)
    recorded_at: AwareDatetime


class Approval(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    action_id: Identifier
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    generation_id: Identifier
    requested_by: Identifier
    capability_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: ApprovalDecision = ApprovalDecision.PENDING
    approver_id: Identifier | None = None
    expires_at: AwareDatetime
    created_at: AwareDatetime
    decided_at: AwareDatetime | None = None
    consumed_at: AwareDatetime | None = None
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_decision(self) -> "Approval":
        decided = self.decision is not ApprovalDecision.PENDING
        if decided != (self.decided_at is not None and self.approver_id is not None):
            raise ValueError("a decision requires an approver and decision time")
        if self.consumed_at is not None and self.decision is not ApprovalDecision.APPROVED:
            raise ValueError("only an approved action can be consumed")
        if (
            self.decision is not ApprovalDecision.CANCELLED
            and self.decided_at is not None
            and self.decided_at > self.expires_at
        ):
            raise ValueError("an approval decision cannot be recorded after expiry")
        return self
