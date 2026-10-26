from pydantic import Field

from contracts.base import Contract


class OverviewSummary(Contract):
    credentials: int = Field(ge=0)
    rotations_in_progress: int = Field(ge=0)
    failed_rotations: int = Field(ge=0)
    open_incidents: int = Field(ge=0)
    pending_approvals: int = Field(ge=0)
