from datetime import datetime
from typing import Any

from contracts import ApprovalDecision, IncidentStatus, RunStatus

from core.storage.database import Database


class OverviewStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def count_credentials(self, organisation_id: str) -> int:
        return await self._count(
            "inventory", organisation_id, {"kind": "credential", "data.archived_at": None}
        )

    async def count_runs(self, organisation_id: str, statuses: frozenset[RunStatus]) -> int:
        return await self._count(
            "runs", organisation_id, {"data.status": {"$in": sorted(statuses)}}
        )

    async def count_incidents(
        self, organisation_id: str, statuses: frozenset[IncidentStatus]
    ) -> int:
        return await self._count(
            "inventory",
            organisation_id,
            {"kind": "incident", "data.status": {"$in": sorted(statuses)}},
        )

    async def count_approvals(
        self,
        organisation_id: str,
        decisions: frozenset[ApprovalDecision],
        active_at: datetime | None = None,
    ) -> int:
        query: dict[str, Any] = {"kind": "approval", "data.decision": {"$in": sorted(decisions)}}
        if active_at:
            query.update(
                {"data.expires_at": {"$gt": active_at.isoformat()}, "data.consumed_at": None}
            )
        return await self._count("inventory", organisation_id, query)

    async def _count(self, collection: str, organisation_id: str, query: dict[str, Any]) -> int:
        return await self._database.collection(collection).count_documents(
            {"organisation_id": organisation_id, **query}
        )
