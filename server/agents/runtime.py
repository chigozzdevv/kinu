from typing import Protocol

from contracts import AgentResult, AgentTask


class AgentRuntimeService(Protocol):
    async def execute(self, task: AgentTask) -> AgentResult: ...
