from typing import Literal

from contracts import Contract, Identifier, WalkthroughKind, WalkthroughSource
from core.auth import Permission
from fastapi import APIRouter, Request, Response, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/playbooks/{playbook_id}/walkthroughs",
    tags=["walkthroughs"],
)


class RegisterSourceRequest(Contract):
    source_id: Identifier
    kind: Literal[WalkthroughKind.TEXT, WalkthroughKind.LINK]
    content: str | None = Field(default=None, min_length=1, max_length=100_000)
    resource_url: str | None = Field(default=None, max_length=1024)


@router.post("/references", response_model=WalkthroughSource, status_code=status.HTTP_201_CREATED)
async def register(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: RegisterSourceRequest,
    identity: Identity,
    request: Request,
    response: Response,
) -> WalkthroughSource:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    source, created = await required(api.walkthroughs, "walkthroughs").register(
        organisation_id,
        playbook_id,
        body.source_id,
        body.kind,
        body.content,
        identity.actor_id,
        body.resource_url,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return source


@router.get("/{source_id}", response_model=WalkthroughSource)
async def get(
    organisation_id: Identifier,
    playbook_id: Identifier,
    source_id: Identifier,
    identity: Identity,
    request: Request,
) -> WalkthroughSource:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_READ)
    return await required(api.walkthroughs, "walkthroughs").refresh(
        organisation_id, playbook_id, source_id
    )
