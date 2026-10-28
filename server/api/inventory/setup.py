from contracts import (
    Identifier,
    SetupSession,
)
from core.auth import Permission
from fastapi import APIRouter, Request, status

from api.deps import Identity, services
from api.inventory.schemas import (
    AbortSetupRequest,
    BeginSetupRequest,
    BeginSetupResponse,
    CompleteSetupRequest,
    CompleteSetupResponse,
)
from api.inventory.shared import _setup

router = APIRouter()


@router.post(
    "/connections/{connection_id}/setup",
    response_model=BeginSetupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def begin_setup(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: BeginSetupRequest,
    identity: Identity,
    request: Request,
) -> BeginSetupResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    setup = _setup(api)
    session, token = await setup.begin(
        organisation_id,
        connection_id,
        identity.subject,
        body.extra_domains,
    )
    return BeginSetupResponse(
        session=session,
        token=token,
        setup_url=setup.setup_url,
        gateway_url=setup.gateway_url,
        expires_at=session.expires_at,
    )


@router.get("/setups/{setup_id}", response_model=SetupSession)
async def get_setup(
    organisation_id: Identifier,
    setup_id: Identifier,
    identity: Identity,
    request: Request,
) -> SetupSession:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await _setup(api).get(organisation_id, setup_id)


@router.post("/setups/{setup_id}/complete", response_model=CompleteSetupResponse)
async def complete_setup(
    organisation_id: Identifier,
    setup_id: Identifier,
    body: CompleteSetupRequest,
    identity: Identity,
    request: Request,
) -> CompleteSetupResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    session, connection, resumed = await _setup(api).complete(
        organisation_id,
        setup_id,
        body.expected_revision,
        body.token,
        identity.subject,
        identity.actor_id,
    )
    return CompleteSetupResponse(session=session, connection=connection, resumed_run_ids=resumed)


@router.post("/setups/{setup_id}/abort", response_model=SetupSession)
async def abort_setup(
    organisation_id: Identifier,
    setup_id: Identifier,
    body: AbortSetupRequest,
    identity: Identity,
    request: Request,
) -> SetupSession:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return await _setup(api).abort(
        organisation_id,
        setup_id,
        body.expected_revision,
        identity.subject,
    )
