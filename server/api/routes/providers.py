from contracts import Connection, Contract, Identifier, ManagedCredential
from core.auth import Permission
from core.connection.provider import ProviderInput, ResendAccess, ResendDomain
from fastapi import APIRouter, Request
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(prefix="/v1/organisations/{organisation_id}/connections", tags=["connections"])


class BrowserSelection(Contract):
    connection_id: Identifier
    expected_revision: int = Field(ge=0)


@router.post("/provider/{connection_id}/browser", status_code=201)
async def prepare_browser(
    organisation_id: Identifier, connection_id: Identifier, identity: Identity, request: Request
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_APPROVE)
    result = await required(api.private, "private execution").call(
        "/providers/browser",
        {
            "organisation_id": organisation_id,
            "connection_id": connection_id,
            "actor_id": identity.actor_id,
        },
    )
    return Connection.model_validate(result)


@router.post("/provider/credentials/{credential_id}/browser")
async def select_browser(
    organisation_id: Identifier,
    credential_id: Identifier,
    body: BrowserSelection,
    identity: Identity,
    request: Request,
) -> ManagedCredential:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    result = await required(api.private, "private execution").call(
        "/providers/browser",
        {
            "organisation_id": organisation_id,
            "credential_id": credential_id,
            "connection_id": body.connection_id,
            "expected_revision": body.expected_revision,
            "actor_id": identity.actor_id,
        },
    )
    return ManagedCredential.model_validate(result)


@router.post("/provider", status_code=201)
async def connect(
    organisation_id: Identifier, body: ProviderInput, identity: Identity, request: Request
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    if body.api_playbook_id:
        await api.access.require(identity, organisation_id, Permission.PLAYBOOK_READ)
    return Connection.model_validate(
        await required(api.private, "private execution").call(
            "/providers",
            {
                "organisation_id": organisation_id,
                "display_name": body.display_name,
                "platform": body.platform,
                "http": body.http.model_dump(mode="json") if body.http else None,
                "domain_id": body.domain_id,
                "api_playbook_id": body.api_playbook_id,
                "api_playbook_version_id": body.api_playbook_version_id,
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
