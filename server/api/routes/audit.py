from typing import Annotated

from contracts import AuditEvent, Identifier
from core.auth import Permission
from fastapi import APIRouter, Query, Request

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/audit",
    tags=["audit"],
)


@router.get("", response_model=tuple[AuditEvent, ...])
async def search_audit(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
    run_id: Annotated[Identifier | None, Query()] = None,
    kind: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> tuple[AuditEvent, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.AUDIT_READ)
    return await required(api.audit, "audit").search(
        organisation_id,
        run_id=run_id,
        kind=kind,
        limit=limit,
    )
