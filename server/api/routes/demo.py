from contracts import Contract, Identifier
from core.auth import Permission
from fastapi import APIRouter, Request, Response

from api.deps import IdempotencyKey, Identity, command_id, required, services

router = APIRouter(prefix="/v1/organisations/{organisation_id}/demo", tags=["demo"])


class Alert(Contract):
    pass


@router.get("")
async def status(
    organisation_id: Identifier, identity: Identity, request: Request
) -> dict[str, object]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_READ)
    return {
        "configured": api.demo is not None,
        "source": "demo-email",
        "credential_id": "credential_demo",
        "browser": "not-configured",
    }


@router.post("/alert", status_code=202)
async def alert(
    organisation_id: Identifier,
    body: Alert,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
    response: Response,
) -> dict[str, object]:
    del body, response
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INCIDENT_WRITE)
    task = await required(api.demo, "demo email source").alert(
        organisation_id, identity.actor_id, command_id(identity, organisation_id, key)
    )
    return {"id": task.id, "queued": True, "source": "demo-email"}
