from typing import Protocol

from contracts import Contract, Identifier, ManagedCredential
from core.errors import ResourceConflictError
from core.storage.database import Database
from core.storage.records import Records
from core.storage.runs import RunStore

from rotation.snapshot import snapshot


class RotationTask(Contract):
    organisation_id: Identifier
    run_id: Identifier


class TaskQueue(Protocol):
    async def rotation(self, organisation_id: str, run_id: str) -> None: ...


class Dispatcher:
    def __init__(self, database: Database, queue: TaskQueue | None) -> None:
        self.database, self.queue = database, queue

    async def require_scope(self, organisation_id: str, credential_id: str) -> None:
        if self.queue is None:
            raise ResourceConflictError("Rotation execution is not configured")
        async with Records(self.database).read() as rows:
            credential = await rows.get(ManagedCredential, organisation_id, credential_id)
        await snapshot(self.database, organisation_id, credential_id, credential.control_version)

    async def submit(self, organisation_id: str, run_id: str) -> None:
        run = await RunStore(self.database).get(organisation_id, run_id)
        await self.require_scope(organisation_id, run.credential_id)
        assert self.queue is not None
        await self.queue.rotation(organisation_id, run_id)
