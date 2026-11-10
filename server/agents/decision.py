from typing import Annotated, Literal, Protocol

from contracts import Contract
from pydantic import Field


class IncidentInput(Contract):
    stage: Literal["investigate"]
    kind: Literal[
        "credential-exposure-detected", "credential-expiring", "credential-security-review"
    ]
    confidence: Literal["low", "medium", "high", "verified"]
    candidate_count: int = Field(ge=0, le=500)
    policy_allows_rotation: bool


class BrowserInput(Contract):
    stage: Literal["browser"]
    phase: Literal["create", "revoke"]
    redacted: Literal[True]
    image: str = Field(min_length=16, max_length=1_000_000, pattern=r"^[A-Za-z0-9+/]+={0,2}$")


class ApiInput(Contract):
    stage: Literal["api"]
    phase: Literal["create", "revoke"]
    method: str = Field(min_length=2, max_length=16)
    path: str = Field(min_length=1, max_length=512)
    identity_check: bool
    overlap: bool
    scopes_count: int = Field(ge=0, le=50)


DecisionInput = Annotated[IncidentInput | BrowserInput | ApiInput, Field(discriminator="stage")]


class Decision(Contract):
    decision: Literal[
        "rotate", "review", "ignore", "click-create", "click-revoke", "execute", "stop"
    ]
    reason: Literal[
        "confirmed-exposure",
        "expiry-window",
        "insufficient-evidence",
        "unrelated",
        "approved-control-visible",
        "control-unavailable",
        "operation-approved",
        "operation-unclear",
    ]


class IncidentAgent(Protocol):
    async def assess(self, organisation_id: str, value: DecisionInput) -> Decision: ...
