from datetime import timedelta
from typing import Any, Protocol

from contracts import AgentMemory, AgentRegistration, AgentSession


class AgentContinuityService(Protocol):
    async def create_session(
        self,
        registration: AgentRegistration,
        session_id: str,
        run_id: str,
        purpose: str,
        task_context: dict[str, Any],
        ttl: timedelta = timedelta(hours=24),
    ) -> AgentSession: ...

    async def remember(
        self,
        registration: AgentRegistration,
        memory_id: str,
        fact: str,
        provenance: tuple[str, ...],
        approved_by: str,
        ttl: timedelta = timedelta(days=30),
    ) -> AgentMemory: ...

    async def retrieve(
        self, registration: AgentRegistration, query: str, count: int = 10
    ) -> tuple[dict[str, Any], ...]: ...
