from typing import Any

from contracts import Stage, StageExecutionResult
from core.errors import ResourceConflictError
from core.storage.database import Database
from psycopg.types.json import Jsonb


class Journal:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, org: str, run_id: str, stage: Stage) -> StageExecutionResult | None:
        async with self.database.connection() as sql:
            row = await (
                await sql.execute(
                    "SELECT data FROM kinu.executions "
                    "WHERE organisation_id=%s AND run_id=%s AND stage=%s",
                    (org, run_id, stage.value),
                )
            ).fetchone()
        return StageExecutionResult.model_validate(row["data"]) if row else None

    async def save(self, result: StageExecutionResult) -> StageExecutionResult:
        data = result.model_dump(mode="json")
        if _sensitive(data):
            raise ResourceConflictError("rotation journal cannot contain credential values")
        async with self.database.connection() as sql:
            await sql.execute(
                "INSERT INTO kinu.executions VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (result.organisation_id, result.run_id, result.stage.value, Jsonb(data)),
            )
        stored = await self.get(result.organisation_id, result.run_id, result.stage)
        if stored != result:
            raise ResourceConflictError("rotation checkpoint already contains another result")
        return stored


def _sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key.casefold() in {"secret", "password", "token", "api_key", "authorization", "cookie"}
            or _sensitive(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_sensitive(item) for item in value)
    return isinstance(value, str) and ("kinu_demo_" in value or "Bearer " in value)
