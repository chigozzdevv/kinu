from datetime import UTC, datetime

from contracts import AgentKind, AgentTask, Contract, Identifier
from contracts.api_playbook import (
    ApiPlaybook,
    ApiPlaybookDraft,
    ApiPlaybookVersion,
    validate_creation_scopes,
)
from core.auth import Permission
from core.errors import PlaybookError, ResourceNotFoundError
from core.playbook.walkthrough import sanitise_source_text
from fastapi import APIRouter, Query, Request, status
from policy import digest
from pydantic import Field

from api.deps import IdempotencyKey, Identity, command_id, required, services
from api.routes.playbooks import GenerateDraftRequest

router = APIRouter(prefix="/v1/organisations/{organisation_id}/api-playbooks", tags=["playbooks"])


class ApiPlaybookDetail(Contract):
    playbook: ApiPlaybook
    active_version: ApiPlaybookVersion | None = None
    latest_version: ApiPlaybookVersion | None = None


class CreateVersionRequest(Contract):
    version_id: Identifier
    definition: ApiPlaybookDraft
    source_ids: tuple[Identifier, ...] = ()


class VersionResponse(Contract):
    playbook: ApiPlaybook
    version: ApiPlaybookVersion


class DraftResponse(Contract):
    definition: ApiPlaybookDraft


class ArchiveRequest(Contract):
    expected_revision: int = Field(ge=0)


@router.get("", response_model=tuple[ApiPlaybook, ...])
async def list_playbooks(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> tuple[ApiPlaybook, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_READ)
    return tuple(
        book
        for book in await required(api.api_playbooks, "API playbooks").list_playbooks(
            organisation_id, limit
        )
        if book.archived_at is None
    )


@router.get("/{playbook_id}", response_model=ApiPlaybookDetail)
async def get_playbook(
    organisation_id: Identifier, playbook_id: Identifier, identity: Identity, request: Request
) -> ApiPlaybookDetail:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_READ)
    store = required(api.api_playbooks, "API playbooks")
    book = await store.get(organisation_id, playbook_id)
    active = (
        await store.get_version(organisation_id, playbook_id, book.active_version_id)
        if book.active_version_id
        else None
    )
    latest = (
        await store.get_version(organisation_id, playbook_id, book.latest_version_id)
        if book.latest_version_id
        else active
    )
    return ApiPlaybookDetail(playbook=book, active_version=active, latest_version=latest)


def _bind_fixed_scopes(definition: ApiPlaybookDraft) -> ApiPlaybookDraft:
    """A single declared permission is fixed: render it as a literal, not a list."""
    http = definition.http
    if not http.credential_scopes or len(http.credential_scopes) != 1:
        return definition
    single = next(iter(http.credential_scopes))

    def render(value: object, key: str = "") -> object:
        if value == "${scopes}" and key not in {"scopes", "scope"}:
            return single
        if isinstance(value, dict):
            return {name: render(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [render(item) for item in value]
        return value

    body = render(http.create_credential.body)
    if body == http.create_credential.body:
        return definition
    return definition.model_copy(
        update={
            "http": http.model_copy(
                update={
                    "create_credential": http.create_credential.model_copy(
                        update={"body": body}
                    )
                }
            )
        }
    )


@router.post("/{playbook_id}/draft", response_model=DraftResponse)
async def generate(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: GenerateDraftRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> DraftResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    sources = await required(api.walkthroughs, "walkthroughs").ready(
        organisation_id, playbook_id, body.source_ids
    )
    if sum(source.size for source in sources) > 100_000:
        raise PlaybookError("Use shorter instructions or fewer links")
    objective, _ = sanitise_source_text(body.objective)
    try:
        existing = await required(api.api_playbooks, "API playbooks").get(
            organisation_id, playbook_id
        )
    except ResourceNotFoundError:
        existing = None
    if existing:
        if existing.archived_at:
            raise PlaybookError("Choose an active playbook")
        objective = (
            f"Build the next API credential-rotation playbook. Keep name {existing.name!r} "
            f"and platform {existing.platform!r}."
        )
    task_id = command_id(identity, organisation_id, key).replace("cmd_", "task_", 1)
    result = await required(api.playbook_builder, "playbook generation").execute(
        AgentTask(
            id=task_id,
            organisation_id=organisation_id,
            run_id=f"run_{task_id.removeprefix('task_')}",
            agent=AgentKind.PLAYBOOK,
            skill="build_playbook",
            objective=objective,
            evidence_ids=body.source_ids,
            requested_at=datetime.now(UTC),
            context={
                "playbook_id": playbook_id,
                "execution": "api",
                "credential_scopes": (
                    "Bind ${scopes} in the create request body when permissions vary by key. "
                    "For fixed permissions, set http.credential_scopes to the exact values."
                ),
                "credential_identity": (
                    "Use response mode and a provider_id_field when the key-check response returns "
                    "the key ID. Otherwise use creation-receipt mode with exact response_matches; "
                    "that mode verifies only keys created through Kinu."
                ),
                "walkthroughs": tuple(
                    source.analysis.model_dump(mode="json")
                    for source in sources
                    if source.analysis is not None
                ),
            },
        )
    )
    if not result.succeeded:
        raise PlaybookError(result.error or "Could not generate the API playbook")
    try:
        definition = ApiPlaybookDraft.model_validate(
            result.output.get("playbook_draft", result.output)
        )
        definition = _bind_fixed_scopes(definition)
        validate_creation_scopes(definition)
    except ValueError as error:
        raise PlaybookError(str(error)[:512] or "The generated playbook is invalid") from error
    if existing and (definition.name, definition.platform) != (existing.name, existing.platform):
        raise PlaybookError("Keep the playbook's name and provider")
    return DraftResponse(definition=definition)


@router.post(
    "/{playbook_id}/versions", response_model=VersionResponse, status_code=status.HTTP_201_CREATED
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
            organisation_id, playbook_id, body.source_ids
        )
    try:
        validate_creation_scopes(body.definition)
    except ValueError as error:
        raise PlaybookError(str(error)) from error
    book, version = await required(api.api_playbooks, "API playbooks").add_version(
        playbook_id,
        body.version_id,
        organisation_id,
        body.definition,
        digest(body.definition),
        identity.actor_id,
        datetime.now(UTC),
        body.source_ids,
    )
    return VersionResponse(playbook=book, version=version)


@router.post("/{playbook_id}/versions/{version_id}/publish", response_model=ApiPlaybookVersion)
async def publish(
    organisation_id: Identifier,
    playbook_id: Identifier,
    version_id: Identifier,
    identity: Identity,
    request: Request,
) -> ApiPlaybookVersion:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_APPROVE)
    store = required(api.api_playbooks, "API playbooks")
    version = await store.get_version(organisation_id, playbook_id, version_id)
    if version.digest != digest(version.definition):
        raise PlaybookError("The API playbook definition changed")
    return await store.publish(
        organisation_id, playbook_id, version_id, identity.actor_id, datetime.now(UTC)
    )


@router.post("/{playbook_id}/archive", response_model=ApiPlaybook)
async def archive(
    organisation_id: Identifier,
    playbook_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> ApiPlaybook:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.PLAYBOOK_WRITE)
    store = required(api.api_playbooks, "API playbooks")
    book = await store.get(organisation_id, playbook_id)
    return await store.replace(
        book.model_copy(
            update={"archived_at": datetime.now(UTC), "revision": body.expected_revision + 1}
        ),
        body.expected_revision,
    )
