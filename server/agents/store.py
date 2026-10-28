from contracts import AgentKind, AgentMemory, AgentRegistration, AgentSession, Contract
from core.errors import ResourceConflictError
from core.storage.database import Database
from core.storage.records import Records


class AgentStore:
    def __init__(self, database: Database) -> None:
        self._records = Records(database)

    async def _save[T: Contract](self, value: T, organisation_id: str) -> T:
        async with self._records.write(organisation_id) as rows:
            existing = await rows.match(
                type(value), organisation_id, {"id": value.model_dump()["id"]}
            )
            if existing:
                if existing[0] != value:
                    raise ResourceConflictError("agent record already contains another value")
                return existing[0]
            return await rows.add(value, organisation_id)

    async def activate(self, registration: AgentRegistration) -> AgentRegistration:
        return await self._save(registration, registration.organisation_id)

    async def get(self, organisation_id: str, agent_id: str) -> AgentRegistration:
        async with self._records.read() as rows:
            return await rows.get(AgentRegistration, organisation_id, agent_id)

    async def list(self, organisation_id: str) -> tuple[AgentRegistration, ...]:
        async with self._records.read() as rows:
            return await rows.list(AgentRegistration, organisation_id)

    async def save_session(self, session: AgentSession) -> AgentSession:
        return await self._save(session, session.organisation_id)

    async def get_session(self, organisation_id: str, session_id: str) -> AgentSession:
        async with self._records.read() as rows:
            return await rows.get(AgentSession, organisation_id, session_id)

    async def save_memory(self, memory: AgentMemory) -> AgentMemory:
        return await self._save(memory, memory.organisation_id)

    async def list_memories(
        self, organisation_id: str, agent: AgentKind
    ) -> tuple[AgentMemory, ...]:
        async with self._records.read() as rows:
            return await rows.match(AgentMemory, organisation_id, {"agent": agent.value})
