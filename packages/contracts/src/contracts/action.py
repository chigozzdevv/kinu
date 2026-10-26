from typing import Any

from pydantic import Field

from contracts.base import Contract, Identifier


class ProtectedAction(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    kind: str = Field(min_length=1, max_length=96)
    resource: str = Field(min_length=1, max_length=512)
    credential_id: Identifier
    generation_id: Identifier
    provider_id: str = Field(min_length=1, max_length=256)
    control_version: Identifier
    playbook_version: Identifier | None = None
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    preconditions: dict[str, str | int | bool | None] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
