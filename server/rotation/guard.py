from datetime import UTC, datetime
from typing import Any

from contracts import RunStatus
from core.errors import LeaseConflictError, ResourceConflictError
from core.storage.database import Database
from core.storage.runs import RunStore

from rotation.snapshot import snapshot
from rotation.state import Snapshot


async def validate(database: Database, state: Snapshot, created: dict[str, Any]) -> None:
    run = await RunStore(database).get(state.credential.organisation_id, created["run_id"])
    if (
        run.status not in {RunStatus.RUNNING, RunStatus.RECOVERING}
        or not run.lease
        or run.lease.expires_at <= datetime.now(UTC)
        or run.fencing_token != created["fencing_token"]
    ):
        raise LeaseConflictError("Rotation no longer holds its execution lease")
    if (
        await snapshot(database, run.organisation_id, run.credential_id, run.control_version)
        != state
    ):
        raise ResourceConflictError("Credential mapping changed during rotation")
