from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from contracts import AgentMemory, AgentRegistration, AgentSession, AgentStatus
from core.errors import ResourceConflictError
from core.playbook.walkthrough import sanitise_source_text

from agents.storage import AgentRepository


class AgentMemoryService:
    def __init__(self, repository: AgentRepository, clock: Callable[[], datetime]) -> None:
        self._repository, self._clock = repository, clock

    async def create_session(
        self,
        registration: AgentRegistration,
        session_id: str,
        run_id: str,
        purpose: str,
        task_context: dict[str, Any],
        ttl: timedelta = timedelta(hours=24),
    ) -> AgentSession:
        self._ready(registration, ttl, timedelta(hours=24))
        if task_context:
            raise ResourceConflictError("persistent agent task context is not supported")
        now = self._clock()
        return await self._repository.save_session(
            AgentSession(
                id=session_id,
                organisation_id=registration.organisation_id,
                run_id=run_id,
                agent=registration.kind,
                remote_session=f"kinu://sessions/{session_id}",
                region=registration.region,
                purpose=purpose,
                created_at=now,
                expires_at=now + ttl,
            )
        )

    async def remember(
        self,
        registration: AgentRegistration,
        memory_id: str,
        fact: str,
        provenance: tuple[str, ...],
        approved_by: str,
        ttl: timedelta = timedelta(days=30),
    ) -> AgentMemory:
        self._ready(registration, ttl, timedelta(days=365))
        sanitised, redactions = sanitise_source_text(fact)
        if redactions:
            raise ResourceConflictError("agent memory cannot contain secret material")
        now = self._clock()
        return await self._repository.save_memory(
            AgentMemory(
                id=memory_id,
                organisation_id=registration.organisation_id,
                agent=registration.kind,
                remote_memory=f"kinu://memories/{memory_id}",
                fact=sanitised,
                provenance=provenance,
                approved_by=approved_by,
                region=registration.region,
                created_at=now,
                expires_at=now + ttl,
            )
        )

    async def retrieve(
        self, registration: AgentRegistration, query: str, count: int = 10
    ) -> tuple[dict[str, Any], ...]:
        if not 1 <= count <= 100:
            raise ResourceConflictError("memory count must be between 1 and 100")
        self._ready(registration, timedelta(seconds=1), timedelta(seconds=1))
        now, terms = self._clock(), set(query.casefold().split())
        memories = [
            item
            for item in await self._repository.list_memories(
                registration.organisation_id, registration.kind
            )
            if item.expires_at > now and item.region == registration.region
        ]
        ranked = sorted(
            memories,
            key=lambda item: (
                len(terms.intersection(item.fact.casefold().split())),
                item.created_at,
                item.id,
            ),
            reverse=True,
        )
        return tuple(
            item.model_dump(mode="json")
            for item in ranked[:count]
            if not terms or terms.intersection(item.fact.casefold().split())
        )

    @staticmethod
    def _ready(registration: AgentRegistration, ttl: timedelta, maximum: timedelta) -> None:
        if registration.status is not AgentStatus.READY or not timedelta(0) < ttl <= maximum:
            raise ResourceConflictError("agent must be ready and its retention must be bounded")
