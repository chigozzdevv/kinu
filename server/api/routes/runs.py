from typing import Annotated

from contracts import (
    CancelRunCommand,
    Contract,
    CreateRunCommand,
    Identifier,
    RotationHistory,
    RotationRun,
    RunStatus,
    RunStep,
    Trigger,
)
from core.auth import Permission
from core.errors import ResourceConflictError
from core.storage import MutationResult
from fastapi import APIRouter, Query, Request, Response, status
from pydantic import AwareDatetime, Field

from api.deps import (
    IdempotencyKey,
    Identity,
    command_id,
    required,
    services,
)

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/runs",
    tags=["runs"],
)


class CreateRunRequest(Contract):
    credential_id: Identifier
    control_version: Identifier
    source: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="manual", min_length=1, max_length=96)
    event_id: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=1024)
    urgency: str = Field(min_length=1, max_length=32)
    received_at: AwareDatetime


class RevisionRequest(Contract):
    expected_revision: int = Field(ge=0)


class MutationResponse(Contract):
    run: RotationRun
    step: RunStep
    applied: bool


OrganisationId = Identifier
RunId = Identifier


def _response(result: MutationResult) -> MutationResponse:
    return MutationResponse(run=result.run, step=result.step, applied=result.applied)


@router.post("", response_model=MutationResponse)
async def create_run(
    organisation_id: OrganisationId,
    body: CreateRunRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
    response: Response,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    if (
        body.source not in {"console", "manual", "dashboard", "agent-native"}
        or body.kind != "manual"
    ):
        raise ResourceConflictError("application rotation requests must identify a manual trigger")
    dispatcher = required(api.dispatcher, "rotation executor")
    await dispatcher.require_scope(organisation_id, body.credential_id)
    inventory = required(api.inventory, "inventory")
    credential = await inventory.get_credential(organisation_id, body.credential_id)
    controls = await inventory.get_controls(
        organisation_id, body.credential_id, body.control_version
    )
    if credential.archived_at or credential.control_version != controls.id:
        raise ResourceConflictError(
            "credential mappings or automatic rotation controls require attention"
        )
    command = CreateRunCommand(
        id=command_id(identity, organisation_id, key),
        organisation_id=organisation_id,
        credential_id=body.credential_id,
        control_version=body.control_version,
        trigger=Trigger(
            source=body.source,
            kind=body.kind,
            event_id=body.event_id,
            actor_id=identity.actor_id,
            reason=body.reason,
            urgency=body.urgency,
            received_at=body.received_at,
        ),
    )
    result = await required(api.workflow, "workflow").create(command)
    if result.run.status is RunStatus.PENDING:
        await dispatcher.submit(organisation_id, result.run.id)
    response.status_code = status.HTTP_201_CREATED if result.applied else status.HTTP_200_OK
    return _response(result)


@router.get("", response_model=tuple[RotationRun, ...])
async def list_runs(
    organisation_id: OrganisationId,
    identity: Identity,
    request: Request,
    run_status: Annotated[list[RunStatus] | None, Query(alias="status")] = None,
    limit: int = Query(default=100, ge=1, le=500),
    before: Identifier | None = None,
) -> tuple[RotationRun, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_READ)
    statuses = frozenset(run_status) if run_status is not None else None
    return await required(api.workflow, "workflow").list_runs(
        organisation_id, statuses, limit, before
    )


@router.get("/{run_id}", response_model=RotationRun)
async def get_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    identity: Identity,
    request: Request,
) -> RotationRun:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_READ)
    return await required(api.workflow, "workflow").get(organisation_id, run_id)


@router.get("/{run_id}/history", response_model=RotationHistory)
async def get_run_history(
    organisation_id: OrganisationId,
    run_id: RunId,
    identity: Identity,
    request: Request,
) -> RotationHistory:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_READ)
    if api.history is None:
        raise ResourceConflictError("rotation history is unavailable")
    return await api.history.get(organisation_id, run_id)


@router.get("/{run_id}/computer-use/{activity_id}/image")
async def get_computer_use_image(
    organisation_id: OrganisationId,
    run_id: RunId,
    activity_id: Identifier,
    identity: Identity,
    request: Request,
) -> Response:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_READ)
    if api.history is None:
        raise ResourceConflictError("Computer Use history is unavailable")
    content, content_type = await api.history.image(organisation_id, run_id, activity_id)
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{run_id}/cancel", response_model=MutationResponse)
async def cancel_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: RevisionRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    run = await required(api.workflow, "workflow").get(organisation_id, run_id)
    if run.status is not RunStatus.PENDING:
        raise ResourceConflictError(
            "only a queued rotation can be cancelled through the application"
        )
    result = await required(api.workflow, "workflow").cancel(
        CancelRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
        )
    )
    return _response(result)


@router.post("/{run_id}/recover", response_model=RotationRun, status_code=202)
async def recover_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: RevisionRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> RotationRun:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    await api.access.require(identity, organisation_id, Permission.APPROVAL_DECIDE)
    result = await required(api.private, "private execution").call(
        "/rotations/recover",
        {
            "organisation_id": organisation_id,
            "run_id": run_id,
            "expected_revision": body.expected_revision,
            "actor_id": identity.actor_id,
            "command_id": command_id(identity, organisation_id, key),
        },
    )
    return RotationRun.model_validate(result)
