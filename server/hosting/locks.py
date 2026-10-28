from contracts import RunStatus
from core.errors import ActiveRunConflictError, RunNotFoundError
from core.storage.database import Database
from core.storage.runs import RunStore
from rotation.state import Snapshot

_TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.COMPENSATED}


async def acquire(database: Database, state: Snapshot, run_id: str) -> None:
    org = state.credential.organisation_id
    async with database.transaction(org) as session:
        for target in state.targets:
            query = {
                "organisation_id": org,
                "connection_id": target.runtime.id,
                "resource": target.binding.runtime_resource,
            }
            current = await database.collection("runtime_locks").find_one(query, session=session)
            if current and current["run_id"] != run_id:
                try:
                    run = await RunStore(database).get(org, current["run_id"])
                except RunNotFoundError:
                    raise ActiveRunConflictError(
                        "Workload execution state requires review"
                    ) from None
                if run.status not in _TERMINAL:
                    raise ActiveRunConflictError(
                        "Another rotation uses this workload; wait for it to finish"
                    )
            await database.collection("runtime_locks").update_one(
                query, {"$set": {"run_id": run_id}}, upsert=True, session=session
            )
