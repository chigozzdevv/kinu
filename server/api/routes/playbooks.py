from datetime import UTC, datetime
from uuid import uuid4

from contracts import (
    AgentKind,
    AgentResult,
    AgentTask,
    Connection,
    Contract,
    Identifier,
    Playbook,
    PlaybookDraft,
    PlaybookVersion,
)
from core.auth import Permission
from core.errors import PlaybookError
from core.playbook import validate_definition
from fastapi import APIRouter, Query, Request, status
from pydantic import Field

from api.deps import ApiServices, IdempotencyKey, Identity, command_id, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/playbooks",
    tags=["playbooks"],
)


class CreateVersionRequest(Contract):
    version_id: Identifier
    definition: PlaybookDraft
    source_ids: tuple[Identifier, ...] = ()


class VersionResponse(Contract):
    playbook: Playbook
    version: PlaybookVersion


class BuildVersionRequest(Contract):
    version_id: Identifier
    objective: str = Field(min_length=1, max_length=2048)
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class BuildVersionResponse(VersionResponse):
    agent: AgentResult


class GenerateDraftRequest(Contract):
    objective: str = Field(min_length=1, max_length=2048)
    source_ids: tuple[Identifier, ...] = Field(min_length=1)


class GenerateDraftResponse(Contract):
    definition: PlaybookDraft
    agent: AgentResult


class AttachRequest(Contract):
    connection_id: Identifier
    expected_revision: int = Field(ge=0)


class PlaybookDetail(Contract):
    playbook: Playbook
    active_version: PlaybookVersion | None = None
    latest_version: PlaybookVersion | None = None


class RenamePlaybookRequest(Contract):
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=160)


class ArchivePlaybookRequest(Contract):
    expected_revision: int = Field(ge=0)
    cascade: bool = False


@router.get("", response_model=tuple[Playbook, ...])
async def list_playbooks(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> tuple[Playbook, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_READ)
    return await required(api.playbooks, "playbooks").list_playbooks(organisation_id, limit)


@router.get("/{playbook_id}", response_model=PlaybookDetail)
async def get_playbook(
    organisation_id: Identifier,
    playbook_id: Identifier,
    identity: Identity,
    request: Request,
) -> PlaybookDetail:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_READ)
    service = required(api.playbooks, "playbooks")
    playbook = await service.get(organisation_id, playbook_id)
    active = (
        await service.get_version(organisation_id, playbook_id, playbook.active_version_id)
        if playbook.active_version_id is not None
        else None
    )
    latest_id = playbook.latest_version_id or playbook.active_version_id
    latest = (
        active
        if latest_id is not None and active is not None and latest_id == active.id
        else await service.get_version(organisation_id, playbook_id, latest_id)
        if latest_id is not None
        else None
    )
    return PlaybookDetail(playbook=playbook, active_version=active, latest_version=latest)


@router.patch("/{playbook_id}", response_model=Playbook)
async def rename_playbook(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: RenamePlaybookRequest,
    identity: Identity,
    request: Request,
) -> Playbook:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    changed = await required(api.playbooks, "playbooks").rename(
        organisation_id, playbook_id, body.expected_revision, body.name
    )
    await _audit_change(
        api, organisation_id, identity.actor_id, "playbook.updated", playbook_id, changed.revision
    )
    return changed


@router.post("/{playbook_id}/archive", response_model=Playbook)
async def archive_playbook(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: ArchivePlaybookRequest,
    identity: Identity,
    request: Request,
) -> Playbook:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    changed = await required(api.playbooks, "playbooks").archive(
        organisation_id, playbook_id, body.expected_revision, body.cascade
    )
    await _audit_change(
        api, organisation_id, identity.actor_id, "playbook.archived", playbook_id, changed.revision
    )
    return changed


@router.post(
    "/{playbook_id}/versions",
    response_model=VersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: CreateVersionRequest,
    identity: Identity,
    request: Request,
) -> VersionResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    if body.source_ids:
        await required(api.walkthroughs, "walkthroughs").ready(
            organisation_id,
            playbook_id,
            body.source_ids,
        )
    root, version = await required(api.playbooks, "playbooks").create_version(
        organisation_id,
        playbook_id,
        body.version_id,
        body.definition,
        identity.actor_id,
        body.source_ids,
    )
    return VersionResponse(playbook=root, version=version)


@router.post(
    "/{playbook_id}/draft",
    response_model=GenerateDraftResponse,
)
async def generate_draft(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: GenerateDraftRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> GenerateDraftResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    definition, result = await _generate_definition(
        api,
        organisation_id,
        playbook_id,
        body.objective,
        body.source_ids,
        identity,
        key,
    )
    return GenerateDraftResponse(definition=definition, agent=result)


@router.post(
    "/{playbook_id}/build",
    response_model=BuildVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def build_version(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: BuildVersionRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> BuildVersionResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    definition, result = await _generate_definition(
        api,
        organisation_id,
        playbook_id,
        body.objective,
        body.source_ids,
        identity,
        key,
    )
    root, version = await required(api.playbooks, "playbooks").create_version(
        organisation_id,
        playbook_id,
        body.version_id,
        definition,
        identity.actor_id,
        body.source_ids,
    )
    return BuildVersionResponse(playbook=root, version=version, agent=result)


async def _generate_definition(
    api: ApiServices,
    organisation_id: str,
    playbook_id: str,
    _objective: str,
    source_ids: tuple[str, ...],
    identity: Identity,
    key: str,
) -> tuple[PlaybookDraft, AgentResult]:
    sources = await required(api.walkthroughs, "walkthroughs").ready(
        organisation_id,
        playbook_id,
        source_ids,
    )
    task_id = command_id(identity, organisation_id, key).replace("cmd_", "task_", 1)
    result = await required(api.agents, "agents").execute(
        AgentTask(
            id=task_id,
            organisation_id=organisation_id,
            run_id=f"run_{task_id.removeprefix('task_')}",
            agent=AgentKind.PLAYBOOK,
            skill="build_playbook",
            objective=(
                f"Credential rotation procedure. Playbook ID: {playbook_id}. "
                f"Sanitised walkthrough source IDs: {', '.join(source_ids)}."
            ),
            evidence_ids=source_ids,
            context={
                "playbook_id": playbook_id,
                "walkthroughs": tuple(
                    source.analysis.model_dump(mode="json")
                    for source in sources
                    if source.analysis is not None
                ),
            },
            requested_at=datetime.now(UTC),
        )
    )
    if not result.succeeded:
        raise PlaybookError(result.error or "Playbook Builder Agent failed")
    candidate = result.output.get("playbook_draft", result.output)
    try:
        definition = PlaybookDraft.model_validate(candidate)
    except ValueError as error:
        raise PlaybookError("Playbook Builder Agent returned an invalid definition") from error
    validate_definition(definition)
    return definition, result


@router.post("/{playbook_id}/versions/{version_id}/publish", response_model=PlaybookVersion)
async def publish(
    organisation_id: Identifier,
    playbook_id: Identifier,
    version_id: Identifier,
    identity: Identity,
    request: Request,
) -> PlaybookVersion:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_APPROVE)
    return await required(api.playbooks, "playbooks").publish(
        organisation_id,
        playbook_id,
        version_id,
        identity.actor_id,
    )


@router.post("/{playbook_id}/versions/{version_id}/attach", response_model=Connection)
async def attach(
    organisation_id: Identifier,
    playbook_id: Identifier,
    version_id: Identifier,
    body: AttachRequest,
    identity: Identity,
    request: Request,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_APPROVE)
    return await required(api.playbooks, "playbooks").attach(
        organisation_id,
        body.connection_id,
        body.expected_revision,
        playbook_id,
        version_id,
    )


async def _audit_change(
    api: ApiServices,
    organisation_id: str,
    actor_id: str,
    kind: str,
    playbook_id: str,
    revision: int,
) -> None:
    if api.audit is None:
        return
    await api.audit.append(
        f"audit_{uuid4().hex}",
        organisation_id,
        kind,
        actor_id,
        f"playbooks/{playbook_id}",
        {"revision": revision},
    )
