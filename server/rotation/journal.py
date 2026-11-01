from typing import Any

from contracts import Stage, StageExecutionResult
from core.errors import ResourceConflictError
from core.storage.database import Database


class Journal:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, org: str, run_id: str, stage: Stage) -> StageExecutionResult | None:
        row = await self.database.collection("executions").find_one(
            {"organisation_id": org, "run_id": run_id, "stage": stage.value}
        )
        return StageExecutionResult.model_validate(row["data"]) if row else None

    async def save(self, result: StageExecutionResult) -> StageExecutionResult:
        data = result.model_dump(mode="json")
        if _sensitive(data):
            raise ResourceConflictError("rotation journal cannot contain credential values")
        async with self.database.transaction(result.organisation_id) as session:
            await self.database.collection("executions").update_one(
                {
                    "organisation_id": result.organisation_id,
                    "run_id": result.run_id,
                    "stage": result.stage.value,
                },
                {"$setOnInsert": {"data": data}},
                upsert=True,
                session=session,
            )
        stored = await self.get(result.organisation_id, result.run_id, result.stage)
        if stored != result:
            raise ResourceConflictError("rotation checkpoint already contains another result")
        return stored

    async def review(self, result: StageExecutionResult) -> None:
        data = result.model_dump(mode="json")
        if _sensitive(data):
            raise ResourceConflictError("rotation evidence cannot contain credential values")
        await self.database.collection("execution_reviews").insert_one(
            {
                "organisation_id": result.organisation_id,
                "run_id": result.run_id,
                "id": result.id,
                "stage": result.stage.value,
                "data": data,
            }
        )


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
