import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import (
    ApprovalDecision,
    IncidentStatus,
    OverviewSummary,
    RunStatus,
)

_ACTIVE_RUNS = frozenset(
    {
        RunStatus.PENDING,
        RunStatus.RUNNING,
        RunStatus.PAUSED,
        RunStatus.RECOVERING,
        RunStatus.CLEANUP,
    }
)

_FAILED_RUNS = frozenset({RunStatus.FAILED})

_OPEN_INCIDENTS = frozenset(
    {
        IncidentStatus.NEW,
        IncidentStatus.CORRELATING,
        IncidentStatus.ACTION,
        IncidentStatus.ROTATING,
    }
)

_PENDING_APPROVALS = frozenset({ApprovalDecision.PENDING})


class OverviewCredentials(Protocol):
    async def count_credentials(self, organisation_id: str) -> int: ...


class OverviewRuns(Protocol):
    async def count_runs(self, organisation_id: str, statuses: frozenset[RunStatus]) -> int: ...


class OverviewIncidents(Protocol):
    async def count_incidents(
        self, organisation_id: str, statuses: frozenset[IncidentStatus]
    ) -> int: ...


class OverviewApprovals(Protocol):
    async def count_approvals(
        self,
        organisation_id: str,
        decisions: frozenset[ApprovalDecision],
        active_at: datetime | None = None,
    ) -> int: ...


class OverviewService:
    def __init__(
        self,
        credentials: OverviewCredentials,
        runs: OverviewRuns,
        incidents: OverviewIncidents,
        approvals: OverviewApprovals,
        clock: Callable[[], datetime],
    ) -> None:
        self._credentials = credentials
        self._runs = runs
        self._incidents = incidents
        self._approvals = approvals
        self._clock = clock

    async def summary(self, organisation_id: str) -> OverviewSummary:
        (
            credentials,
            rotations_in_progress,
            failed_rotations,
            open_incidents,
            pending_approvals,
        ) = await asyncio.gather(
            self._credentials.count_credentials(organisation_id),
            self._runs.count_runs(organisation_id, _ACTIVE_RUNS),
            self._runs.count_runs(organisation_id, _FAILED_RUNS),
            self._incidents.count_incidents(organisation_id, _OPEN_INCIDENTS),
            self._approvals.count_approvals(
                organisation_id,
                _PENDING_APPROVALS,
                self._clock(),
            ),
        )
        return OverviewSummary(
            credentials=credentials,
            rotations_in_progress=rotations_in_progress,
            failed_rotations=failed_rotations,
            open_incidents=open_incidents,
            pending_approvals=pending_approvals,
        )
