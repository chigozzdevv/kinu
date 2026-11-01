from typing import Annotated, Any

from fastapi import APIRouter, Depends
from hosting.control import Request, control

from executor.deps import authenticated
from executor.runtime import Runtime

router = APIRouter()


@router.post("/hosting")
async def manage(body: Request, service: Annotated[Runtime, Depends(authenticated)]) -> Any:
    if body.operation in {"provision", "provision-list", "provision-recover"}:
        from core.errors import ResourceConflictError
        from hosting.models import Provision

        if not body.organisation_id or not body.connection_id:
            raise ResourceConflictError("Choose a hosting connection")
        if body.operation == "provision-list":
            return await service.provisioning.list(body.organisation_id, body.connection_id)
        if not body.request_id:
            raise ResourceConflictError("Key setup requires a request identifier")
        if body.operation == "provision-recover":
            row = await service.provisioning.get(body.organisation_id, body.request_id)
            if row["host_id"] != body.connection_id:
                raise ResourceConflictError("Key setup belongs to another hosting connection")
            return await service.provisioning.recover(
                body.organisation_id, body.request_id, body.actor_id
            )
        return await service.provisioning.request(
            body.organisation_id,
            body.connection_id,
            body.actor_id,
            body.request_id,
            Provision.model_validate(body.data),
        )
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
