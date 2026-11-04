from typing import Any
from urllib.parse import urlencode

from contracts import Connection, Identifier
from core.auth import AuthenticatedIdentity, Permission
from core.errors import AuthenticationError, KinuError
from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from hosting.models import Begin, Import, Platform, Provision, Selection

from api.deps import (
    IdempotencyKey,
    Identity,
    browser_session,
    command_id,
    required,
    services,
    session_binding,
)
from api.inventory.shared import _audit_change

router = APIRouter(tags=["connections"])
PREFIX = "/v1/organisations/{organisation_id}/connections/hosting"


async def call(
    request: Request, identity: AuthenticatedIdentity, org: str, operation: str, **parameters: Any
) -> Any:
    api = services(request)
    await api.access.require(identity, org, Permission.INVENTORY_WRITE)
    return await required(api.private, "private execution").call(
        "/hosting",
        {
            "operation": operation,
            "organisation_id": org,
            "actor_id": identity.actor_id,
            "session": session_binding(request),
            **parameters,
        },
    )


@router.post(PREFIX)
async def begin(
    organisation_id: Identifier, body: Begin, identity: Identity, request: Request
) -> Any:
    data = body.model_dump(mode="json", exclude={"token"})
    if body.token:
        data["token"] = body.token.get_secret_value()
    return await call(request, identity, organisation_id, "begin", data=data)


@router.get("/connections/{platform}/callback")
async def callback(
    request: Request,
    platform: Platform,
    state: str = Query(default="", max_length=128),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=256),
    iss: str | None = Query(default=None, max_length=128),
) -> RedirectResponse:
    destination = "/dashboard/connections?hosting=failed&platform=" + platform
    try:
        current, api = await browser_session(request), services(request)
        parameters = {
            "platform": platform,
            "actor_id": current.identity.actor_id,
            "session": session_binding(request),
            "state": state,
        }
        private = required(api.private, "private execution")
        org = await private.call("/hosting", {"operation": "owner", **parameters})
        await api.access.require(current.identity, org, Permission.INVENTORY_WRITE)
        approved = (
            code
            if not error and (platform != "railway" or iss == "https://backboard.railway.com")
            else None
        )
        identity = await private.call(
            "/hosting",
            {"operation": "finish", "organisation_id": org, **parameters, "code": approved},
        )
        destination = "/dashboard/connections?" + urlencode(
            {"hosting": identity, "platform": platform, "workspace": org}
        )
    except AuthenticationError:
        destination = "/dashboard/connections?hosting=expired&platform=" + platform
    except KinuError:
        pass
    return RedirectResponse(
        request.app.state.config.frontend_location(destination),
        status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get(PREFIX + "/{connection_id}")
async def projects(
    organisation_id: Identifier, connection_id: Identifier, identity: Identity, request: Request
) -> Any:
    return await call(request, identity, organisation_id, "projects", connection_id=connection_id)


@router.put(PREFIX + "/{connection_id}")
async def save(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: Selection,
    identity: Identity,
    request: Request,
) -> Connection:
    saved = Connection.model_validate(
        await call(
            request,
            identity,
            organisation_id,
            "save",
            connection_id=connection_id,
            data=body.model_dump(mode="json"),
        )
    )
    await _audit_change(
        services(request),
        organisation_id,
        identity.actor_id,
        "hosting.connected",
        connection_id,
        saved.revision,
    )
    return saved


@router.get(PREFIX + "/{connection_id}/variables")
async def variables(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
    project_id: str = Query(min_length=1, max_length=256),
) -> Any:
    return await call(
        request,
        identity,
        organisation_id,
        "variables",
        connection_id=connection_id,
        project_id=project_id,
    )


@router.post(PREFIX + "/{connection_id}/credentials")
async def import_keys(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: Import,
    identity: Identity,
    request: Request,
) -> Any:
    result = await call(
        request,
        identity,
        organisation_id,
        "import",
        connection_id=connection_id,
        data=body.model_dump(mode="json"),
    )
    await _audit_change(
        services(request),
        organisation_id,
        identity.actor_id,
        "hosting.credentials-imported",
        connection_id,
        0,
    )
    return result


@router.get(PREFIX + "/{connection_id}/credentials")
async def mapped(
    organisation_id: Identifier, connection_id: Identifier, identity: Identity, request: Request
) -> Any:
    return await call(request, identity, organisation_id, "mapped", connection_id=connection_id)


@router.post(PREFIX + "/{connection_id}/key-setups", status_code=202)
async def provision(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: Provision,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> Any:
    return await call(
        request,
        identity,
        organisation_id,
        "provision",
        connection_id=connection_id,
        request_id=command_id(identity, organisation_id, key),
        data=body.model_dump(mode="json"),
    )


@router.get(PREFIX + "/{connection_id}/key-setups")
async def key_setups(
    organisation_id: Identifier, connection_id: Identifier, identity: Identity, request: Request
) -> Any:
    return await call(
        request, identity, organisation_id, "provision-list", connection_id=connection_id
    )


@router.post(PREFIX + "/{connection_id}/key-setups/{setup_id}/recover", status_code=202)
async def recover_key_setup(
    organisation_id: Identifier,
    connection_id: Identifier,
    setup_id: Identifier,
    identity: Identity,
    request: Request,
) -> Any:
    return await call(
        request,
        identity,
        organisation_id,
        "provision-recover",
        connection_id=connection_id,
        request_id=setup_id,
    )
