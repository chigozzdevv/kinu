from contracts import Connection, Contract, Identifier
from core.auth import Permission
from core.connection.service import AwsPrepared, AwsSetup
from core.connection.template import role_template
from fastapi import APIRouter, Request
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(prefix="/v1/organisations/{organisation_id}/connections", tags=["connections"])


class ConfirmConnection(Contract):
    expected_revision: int = Field(ge=0)


class PreparedResponse(AwsPrepared):
    template: dict[str, object]


@router.post("/aws", status_code=201)
async def prepare(
    organisation_id: Identifier, body: AwsSetup, identity: Identity, request: Request
) -> PreparedResponse:
    configured = services(request)
    await configured.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    prepared = await required(configured.connections, "connections").prepare(organisation_id, body)
    return PreparedResponse(**prepared.model_dump(), template=role_template(prepared))


@router.post("/aws/{connection_id}/confirm")
async def confirm(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: ConfirmConnection,
    identity: Identity,
    request: Request,
) -> Connection:
    configured = services(request)
    await configured.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return await required(configured.connections, "connections").confirm(
        organisation_id, connection_id, body.expected_revision
    )
