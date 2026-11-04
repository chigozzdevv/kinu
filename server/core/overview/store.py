from datetime import datetime
from typing import Any

from contracts import ApprovalDecision, IncidentStatus, RunStatus

from core.storage.database import Database
from core.storage.runs import current_runs_pipeline


class OverviewStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def count_credentials(self, organisation_id: str) -> int:
        return await self._count(
            "inventory", organisation_id, {"kind": "credential", "data.archived_at": None}
        )

    async def count_runs(self, organisation_id: str, statuses: frozenset[RunStatus]) -> int:
        cursor = await self._database.collection("runs").aggregate(
            [
                *current_runs_pipeline(organisation_id),
                {"$match": {"data.status": {"$in": sorted(statuses)}}},
                {"$count": "total"},
            ]
        )
        async for row in cursor:
            return int(row["total"])
        return 0

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
            cursor = await self._database.collection("inventory").aggregate(
                [
                    {"$match": {"organisation_id": organisation_id, **query}},
                    {
                        "$lookup": {
                            "from": "runs",
                            "let": {
                                "run": "$data.run_id",
                                "plan": "$data.plan_hash",
                                "generation": "$data.generation_id",
                            },
                            "pipeline": [
                                {
                                    "$match": {
                                        "organisation_id": organisation_id,
                                        "$expr": {
                                            "$and": [
                                                {"$eq": ["$id", "$$run"]},
                                                {
                                                    "$in": [
                                                        "$data.status",
                                                        ["running", "paused", "recovering"],
                                                    ]
                                                },
                                                {"$eq": ["$data.plan_hash", "$$plan"]},
                                                {
                                                    "$in": [
                                                        "$$generation",
                                                        [
                                                            "$data.current_generation_id",
                                                            "$data.target_generation_id",
                                                        ],
                                                    ]
                                                },
                                            ]
                                        },
                                    }
                                }
                            ],
                            "as": "active_run",
                        }
                    },
                    {"$unwind": "$active_run"},
                    {
                        "$lookup": {
                            "from": "inventory",
                            "let": {
                                "credential": "$active_run.credential_id",
                                "controls": "$active_run.data.control_version",
                            },
                            "pipeline": [
                                {
                                    "$match": {
                                        "organisation_id": organisation_id,
                                        "kind": "credential",
                                        "data.archived_at": None,
                                        "$expr": {
                                            "$and": [
                                                {"$eq": ["$id", "$$credential"]},
                                                {"$eq": ["$data.control_version", "$$controls"]},
                                            ]
                                        },
                                    }
                                }
                            ],
                            "as": "credential",
                        }
                    },
                    {"$match": {"credential.0": {"$exists": True}}},
                    {"$count": "total"},
                ]
            )
            async for row in cursor:
                return int(row["total"])
            return 0
        return await self._count("inventory", organisation_id, query)

    async def _count(self, collection: str, organisation_id: str, query: dict[str, Any]) -> int:
        return await self._database.collection(collection).count_documents(
            {"organisation_id": organisation_id, **query}
        )
