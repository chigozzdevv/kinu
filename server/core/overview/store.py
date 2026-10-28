from datetime import datetime

from contracts import ApprovalDecision, IncidentStatus, RunStatus

from core.storage.database import Database


class OverviewStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def count_credentials(self, organisation_id: str) -> int:
        return await self._count(
            "inventory", "kind='credential' AND data->>'archived_at' IS NULL", (organisation_id,)
        )

    async def count_runs(self, organisation_id: str, statuses: frozenset[RunStatus]) -> int:
        if not statuses:
            return 0
        placeholders = ",".join("%s" for _ in statuses)
        return await self._count(
            "runs",
            f"data->>'status' IN ({placeholders})",
            (organisation_id, *(item.value for item in statuses)),
        )

    async def count_incidents(
        self, organisation_id: str, statuses: frozenset[IncidentStatus]
    ) -> int:
        if not statuses:
            return 0
        placeholders = ",".join("%s" for _ in statuses)
        return await self._count(
            "inventory",
            f"kind='incident' AND data->>'status' IN ({placeholders})",
            (organisation_id, *(item.value for item in statuses)),
        )

    async def count_approvals(
        self,
        organisation_id: str,
        decisions: frozenset[ApprovalDecision],
        active_at: datetime | None = None,
    ) -> int:
        if not decisions:
            return 0
        placeholders = ",".join("%s" for _ in decisions)
        where = f"kind='approval' AND data->>'decision' IN ({placeholders})"
        parameters: tuple[object, ...] = (organisation_id, *(item.value for item in decisions))
        if active_at:
            where += " AND (data->>'expires_at')::timestamptz>%s AND data->>'consumed_at' IS NULL"
            parameters += (active_at,)
        return await self._count("inventory", where, parameters)

    async def _count(self, table: str, where: str, parameters: tuple[object, ...]) -> int:
        if table not in {"inventory", "runs"}:
            raise ValueError("unsupported overview table")
        async with self._database.connection() as sql:
            row = await (
                await sql.execute(
                    f"SELECT count(*) AS count FROM kinu.{table} "
                    f"WHERE organisation_id=%s AND {where}",
                    parameters,
                )
            ).fetchone()
        return int(row["count"]) if row else 0
