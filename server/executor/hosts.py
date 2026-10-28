from typing import Annotated, Any

from fastapi import APIRouter, Depends
from hosting.control import Request, control

from executor.deps import authenticated
from executor.runtime import Runtime

router = APIRouter()


@router.post("/hosting")
async def manage(body: Request, service: Annotated[Runtime, Depends(authenticated)]) -> Any:
    return await control(service.hosts, body)
