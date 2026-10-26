from pydantic import AwareDatetime, Field

from contracts.agent import AgentKind
from contracts.base import Contract, Identifier
from contracts.browser import ComputerUseActivity
from contracts.coordinator import StageExecutionStatus
from contracts.state import Stage


class AgentDecisionSummary(Contract):
    agent: AgentKind
    decision: str = Field(min_length=1, max_length=160)
    explanation: str = Field(min_length=1, max_length=2048)


class BrowserActionSummary(Contract):
    step_id: Identifier
    objective: str = Field(min_length=1, max_length=1024)
    operation: str = Field(min_length=1, max_length=96)
    outcome: str = Field(min_length=1, max_length=96)


class StageDetail(Contract):
    label: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=512)


class RunStageActivity(Contract):
    id: Identifier
    stage: Stage
    status: StageExecutionStatus
    checks: tuple[str, ...] = ()
    evidence_count: int = Field(default=0, ge=0)
    summary: str | None = Field(default=None, max_length=160)
    details: tuple[StageDetail, ...] = ()
    agent_decisions: tuple[AgentDecisionSummary, ...] = ()
    browser_actions: tuple[BrowserActionSummary, ...] = ()
    reason: str | None = Field(default=None, max_length=1024)
    retryable: bool = False
    started_at: AwareDatetime
    completed_at: AwareDatetime


class RotationHistory(Contract):
    run_id: Identifier
    stages: tuple[RunStageActivity, ...] = ()
    computer_use: tuple[ComputerUseActivity, ...] = ()
