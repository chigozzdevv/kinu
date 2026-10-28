from typing import Annotated, Any

from fastapi import APIRouter, Depends
from hosting.control import Request, control

from executor.deps import authenticated
from executor.runtime import Runtime

router = APIRouter()


@router.post("/hosting")
async def manage(body: Request, service: Annotated[Runtime, Depends(authenticated)]) -> Any:
    if body.operation == "import":
        from hosting.models import Import

        if not body.organisation_id or not body.connection_id:
            from core.errors import ResourceConflictError

            raise ResourceConflictError("Choose a hosting connection")
        return await service.mappings.save(
            body.organisation_id,
            body.connection_id,
            body.actor_id,
            Import.model_validate(body.data),
        )
    return await control(service.hosts, body)
