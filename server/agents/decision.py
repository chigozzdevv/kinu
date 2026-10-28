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


DecisionInput = Annotated[IncidentInput | BrowserInput, Field(discriminator="stage")]


class Decision(Contract):
    decision: Literal["rotate", "review", "ignore", "click-create", "click-revoke", "stop"]
    reason: Literal[
        "confirmed-exposure",
        "expiry-window",
        "insufficient-evidence",
        "unrelated",
        "approved-control-visible",
        "control-unavailable",
    ]


class IncidentAgent(Protocol):
    async def assess(self, organisation_id: str, value: DecisionInput) -> Decision: ...
