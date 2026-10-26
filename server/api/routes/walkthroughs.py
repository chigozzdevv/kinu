from connectors.base.errors import ConnectorError
from contracts import Contract, Identifier, WalkthroughKind, WalkthroughSource
from core.auth import Permission
from core.errors import PlaybookError
from fastapi import APIRouter, Request, Response, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/playbooks/{playbook_id}/walkthroughs",
    tags=["walkthroughs"],
)


class BeginWalkthroughRequest(Contract):
    source_id: Identifier
    content_type: str = Field(pattern=r"^video/(mp4|webm|quicktime)$")
    size: int = Field(gt=0, le=2_000_000_000)
    crc32c: str = Field(min_length=4, max_length=16)


class BeginWalkthroughResponse(Contract):
    source: WalkthroughSource
    upload_url: str = Field(pattern=r"^https://")


class RegisterSourceRequest(Contract):
    source_id: Identifier
    kind: WalkthroughKind
    content: str = Field(min_length=1, max_length=100_000)
    resource_url: str | None = Field(default=None, max_length=2048)


class RegisterVideoRequest(Contract):
    source_id: Identifier
    resource: str = Field(min_length=8, max_length=2048)


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


@router.post(
    "/video-references",
    response_model=WalkthroughSource,
    status_code=status.HTTP_201_CREATED,
)
async def register_video(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: RegisterVideoRequest,
    identity: Identity,
    request: Request,
    response: Response,
) -> WalkthroughSource:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    try:
        source, created = await required(api.walkthroughs, "walkthroughs").reference_video(
            organisation_id,
            playbook_id,
            body.source_id,
            body.resource,
            identity.actor_id,
        )
    except ConnectorError as error:
        raise PlaybookError(str(error)) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return source


@router.post("", response_model=BeginWalkthroughResponse, status_code=status.HTTP_201_CREATED)
async def begin(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: BeginWalkthroughRequest,
    identity: Identity,
    request: Request,
    response: Response,
) -> BeginWalkthroughResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    source, upload, created = await required(api.walkthroughs, "walkthroughs").begin(
        organisation_id,
        playbook_id,
        body.source_id,
        body.content_type,
        body.size,
        body.crc32c,
        identity.actor_id,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return BeginWalkthroughResponse(source=source, upload_url=upload)


@router.post("/{source_id}/complete", response_model=WalkthroughSource)
async def complete(
    organisation_id: Identifier,
    playbook_id: Identifier,
    source_id: Identifier,
    identity: Identity,
    request: Request,
) -> WalkthroughSource:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    try:
        return await required(api.walkthroughs, "walkthroughs").complete(
            organisation_id,
            playbook_id,
            source_id,
        )
    except ConnectorError as error:
        raise PlaybookError(str(error)) from error


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
        organisation_id,
        playbook_id,
        source_id,
    )
