from contracts import BrowserAccessGrant, BrowserAccessMode, BrowserSession, Contract, Identifier
from core.auth import Permission
from fastapi import APIRouter, Request

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/browsers",
    tags=["browsers"],
)


class AccessRequest(Contract):
    mode: BrowserAccessMode


@router.post("/{session_id}/access", response_model=BrowserAccessGrant)
async def access(
    organisation_id: Identifier,
    session_id: Identifier,
    body: AccessRequest,
    identity: Identity,
    request: Request,
) -> BrowserAccessGrant:
    api = services(request)
    permission = (
        Permission.RUN_WRITE if body.mode is BrowserAccessMode.TAKEOVER else Permission.RUN_READ
    )
    await api.access.require(identity, organisation_id, permission)
    return await required(api.browsers, "browsers").issue(
        organisation_id,
        session_id,
        body.mode,
        identity.subject,
    )


@router.post("/{session_id}/release", response_model=BrowserSession)
async def release(
    organisation_id: Identifier,
    session_id: Identifier,
    identity: Identity,
    request: Request,
) -> BrowserSession:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    return await required(api.browsers, "browsers").release(
        organisation_id,
        session_id,
        identity.subject,
    )
