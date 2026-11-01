from typing import Annotated

from contracts import AgentResult, AgentTask
from fastapi import APIRouter, Depends

from executor.deps import authenticated
from executor.runtime import Runtime

router = APIRouter()


@router.post("/playbooks/generate", response_model=AgentResult)
async def generate(
    task: AgentTask, service: Annotated[Runtime, Depends(authenticated)]
) -> AgentResult:
    return await service.agent.build(task)
