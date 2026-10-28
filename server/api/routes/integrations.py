from typing import Any

from contracts import Contract, Identifier
from core.auth import Permission
from core.config import Settings
from core.errors import AuthorizationError
from core.ids import new_id
from fastapi import APIRouter, Request
from integrations.catalog import integration
from pydantic import Field, SecretStr

from api.deps import Identity, required, services

router = APIRouter(prefix="/v1/organisations/{organisation_id}/integrations", tags=["settings"])


class IntegrationUpdate(Contract):
    values: dict[str, SecretStr] = Field(max_length=5)


@router.get("")
async def list_integrations(
    organisation_id: Identifier, identity: Identity, request: Request
) -> dict[str, Any]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PROFILE_READ)
    config: Settings = request.app.state.config
    editable = bool(
        config.admin_email
        and identity.email_verified
        and identity.email
        and config.admin_email.casefold() == identity.email.casefold()
    )
    private = required(api.private, "private execution")
    return {
        "editable": editable,
        "integrations": await private.call("/integrations", {"operation": "list"}),
    }


@router.put("/{integration_id}")
async def configure(
    organisation_id: Identifier,
    integration_id: str,
    body: IntegrationUpdate,
    identity: Identity,
    request: Request,
) -> dict[str, bool]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.TEAM_WRITE)
    config: Settings = request.app.state.config
    if not (
        config.admin_email
        and identity.email_verified
        and identity.email
        and config.admin_email.casefold() == identity.email.casefold()
    ):
        raise AuthorizationError("An installation administrator must configure integrations")
    definition = integration(integration_id)
    if not body.values.keys() <= set(definition.fields):
        raise AuthorizationError("Unknown integration setting")
    await required(api.private, "private execution").call(
        "/integrations",
        {
            "operation": "configure",
            "integration_id": integration_id,
            "values": {key: value.get_secret_value() for key, value in body.values.items()},
        },
    )
    if api.audit:
        await api.audit.append(
            new_id("audit"),
            organisation_id,
            "integration.configured",
            identity.actor_id,
            "integrations/" + integration_id,
            {},
        )
    return {"configured": True}
