from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from contracts.base import Contract, Identifier


class AgentKind(StrEnum):
    INVENTORY = "inventory"
    PLANNER = "planner"
    PLAYBOOK = "playbook"
    OPERATOR = "operator"


class AgentStatus(StrEnum):
    DEPLOYING = "deploying"
    READY = "ready"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class AgentRegistration(Contract):
    id: Identifier
    organisation_id: Identifier
    kind: AgentKind
    display_name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=64)
    skills: frozenset[str] = Field(min_length=1)
    owner: str = Field(min_length=1, max_length=256)
    identity: str = Field(min_length=1, max_length=512)
    endpoint: str = Field(min_length=1, max_length=1024)
    deployment: str = Field(min_length=1, max_length=1024)
    registry: str = Field(min_length=1, max_length=1024)
    ingress_gateway: str = Field(min_length=1, max_length=1024)
    egress_gateway: str = Field(min_length=1, max_length=1024)
    region: str = Field(min_length=3, max_length=32)
    approved_callers: frozenset[str] = Field(min_length=1)
    tool_destinations: frozenset[str] = Field(min_length=1)
    status: AgentStatus
    registered_at: AwareDatetime


class AgentTask(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    agent: AgentKind
    skill: str = Field(min_length=1, max_length=96)
    objective: str = Field(min_length=1, max_length=2048)
    context: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: tuple[Identifier, ...] = ()
    requested_at: AwareDatetime


class AgentResult(Contract):
    task_id: Identifier
    agent: AgentKind
    skill: str = Field(min_length=1, max_length=96)
    succeeded: bool
    output: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: tuple[Identifier, ...] = ()
    error: str | None = Field(default=None, max_length=1024)
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_failure(self) -> "AgentResult":
        if not self.succeeded and not self.error:
            raise ValueError("failed agent tasks require an error")
        return self


class AgentSession(Contract):
    id: Identifier
    organisation_id: Identifier
    run_id: Identifier
    agent: AgentKind
    remote_session: str = Field(min_length=1, max_length=1024)
    region: str = Field(min_length=3, max_length=32)
    purpose: str = Field(min_length=1, max_length=256)
    created_at: AwareDatetime
    expires_at: AwareDatetime


class AgentMemory(Contract):
    id: Identifier
    organisation_id: Identifier
    agent: AgentKind
    remote_memory: str = Field(min_length=1, max_length=1024)
    fact: str = Field(min_length=1, max_length=2048)
    provenance: tuple[Identifier, ...] = Field(min_length=1)
    approved_by: Identifier
    region: str = Field(min_length=3, max_length=32)
    created_at: AwareDatetime
    expires_at: AwareDatetime
