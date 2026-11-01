from typing import Annotated

from contracts import Contract, Identifier, RotationRun
from fastapi import APIRouter, Depends
from pydantic import Field

from executor.deps import authenticated
from executor.runtime import Runtime

router = APIRouter()


class RecoveryRequest(Contract):
    organisation_id: Identifier
    run_id: Identifier
    actor_id: Identifier
    command_id: Identifier
    expected_revision: int = Field(ge=0)


@router.post("/rotations/recover", response_model=RotationRun)
async def recover(
    body: RecoveryRequest, service: Annotated[Runtime, Depends(authenticated)]
) -> RotationRun:
    return await service.recovery.request(
        body.organisation_id,
        body.run_id,
        body.expected_revision,
        body.actor_id,
        body.command_id,
    )
