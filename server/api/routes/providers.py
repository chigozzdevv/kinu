from contracts import Connection, Identifier
from core.auth import Permission
from core.connection.provider import ProviderInput, ResendAccess, ResendDomain
from fastapi import APIRouter, Request

from api.deps import Identity, required, services

router = APIRouter(prefix="/v1/organisations/{organisation_id}/connections", tags=["connections"])


@router.post("/provider", status_code=201)
async def connect(
    organisation_id: Identifier, body: ProviderInput, identity: Identity, request: Request
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return Connection.model_validate(
        await required(api.private, "private execution").call(
            "/providers",
            {
                "organisation_id": organisation_id,
                "display_name": body.display_name,
                "platform": body.platform,
                "http": body.http.model_dump(mode="json") if body.http else None,
                "domain_id": body.domain_id,
                "token": body.token.get_secret_value(),
            },
        )
    )


@router.post("/resend/domains")
async def domains(
    organisation_id: Identifier, body: ResendAccess, identity: Identity, request: Request
) -> tuple[ResendDomain, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    result = await required(api.private, "private execution").call(
        "/providers/resend/domains",
        {"organisation_id": organisation_id, "token": body.token.get_secret_value()},
    )
    return tuple(ResendDomain.model_validate(item) for item in result)
