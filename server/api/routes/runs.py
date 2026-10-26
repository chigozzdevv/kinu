from typing import Annotated

from contracts import (
    CancelRunCommand,
    CleanupRunCommand,
    CompleteRecoveryCommand,
    CompleteStageCommand,
    ConnectionInterface,
    ConnectionStatus,
    Contract,
    CreateRunCommand,
    FailRunCommand,
    Failure,
    Identifier,
    PauseRunCommand,
    RecoverRunCommand,
    RecoveryMode,
    RenewLeaseCommand,
    ResumeRunCommand,
    RotationHistory,
    RotationRun,
    RunStatus,
    RunStep,
    Stage,
    StageBindings,
    StageProof,
    StartRunCommand,
    Trigger,
)
from core.auth import Permission
from core.errors import ResourceConflictError
from core.storage import MutationResult
from fastapi import APIRouter, Query, Request, Response, status
from pydantic import AwareDatetime, Field

from api.deps import (
    ApiServices,
    IdempotencyKey,
    Identity,
    command_id,
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


class StartRequest(RevisionRequest):
    expires_at: AwareDatetime


class RenewRequest(StartRequest):
    fencing_token: int = Field(gt=0)


class ProofRequest(Contract):
    stage: Stage
    checks: frozenset[str]
    evidence_ids: tuple[Identifier, ...]
    recorded_at: AwareDatetime


class CompleteRequest(RevisionRequest):
    fencing_token: int = Field(gt=0)
    proof: ProofRequest
    bindings: StageBindings = StageBindings()


class FencedRequest(RevisionRequest):
    fencing_token: int = Field(gt=0)


class FailureRequest(FencedRequest):
    failure: Failure


class LeaseRequest(RevisionRequest):
    expires_at: AwareDatetime


class RecoveryCompleteRequest(FencedRequest):
    recovery_id: Identifier
    mode: RecoveryMode
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)


class MutationResponse(Contract):
    run: RotationRun
    step: RunStep
    applied: bool


class ReapResponse(Contract):
    scanned: int = Field(ge=0)
    restarted: int = Field(ge=0)
    failed: int = Field(ge=0)
    runs: tuple[RotationRun, ...]


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
    result = await api.workflow.create(command)
    response.status_code = status.HTTP_201_CREATED if result.applied else status.HTTP_200_OK
    return _response(result)


@router.get("", response_model=tuple[RotationRun, ...])
async def list_runs(
    organisation_id: OrganisationId,
    identity: Identity,
    request: Request,
    run_status: Annotated[list[RunStatus] | None, Query(alias="status")] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> tuple[RotationRun, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_READ)
    statuses = frozenset(run_status) if run_status is not None else None
    return await api.workflow.list_runs(organisation_id, statuses, limit)


@router.post("/reap", response_model=ReapResponse)
async def reap_runs(
    organisation_id: OrganisationId,
    identity: Identity,
    request: Request,
) -> ReapResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.reap_expired(organisation_id, identity.actor_id)
    if api.browser_setup is not None:
        await api.browser_setup.reap_expired(organisation_id)
    return ReapResponse(
        scanned=result.scanned,
        restarted=result.restarted,
        failed=result.failed,
        runs=result.runs,
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
    return await api.workflow.get(organisation_id, run_id)


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
async def get_computer_use_input_image(
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
    content, content_type = await api.history.input_image(organisation_id, run_id, activity_id)
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{run_id}/start", response_model=MutationResponse)
async def start_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: StartRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.start(
        StartRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            owner_id=identity.actor_id,
            expires_at=body.expires_at,
        )
    )
    return _response(result)


@router.post("/{run_id}/renew", response_model=MutationResponse)
async def renew_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: RenewRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.renew(
        RenewLeaseCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            owner_id=identity.actor_id,
            expires_at=body.expires_at,
            fencing_token=body.fencing_token,
        )
    )
    return _response(result)


@router.post("/{run_id}/complete", response_model=MutationResponse)
async def complete_stage(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: CompleteRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    proof = StageProof(
        run_id=run_id,
        organisation_id=organisation_id,
        stage=body.proof.stage,
        checks=body.proof.checks,
        evidence_ids=body.proof.evidence_ids,
        actor_id=identity.actor_id,
        recorded_at=body.proof.recorded_at,
    )
    result = await api.workflow.complete(
        CompleteStageCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
            proof=proof,
            bindings=body.bindings,
        )
    )
    return _response(result)


@router.post("/{run_id}/pause", response_model=MutationResponse)
async def pause_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: FencedRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.pause(
        PauseRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
        )
    )
    return _response(result)


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
    result = await api.workflow.cancel(
        CancelRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
        )
    )
    return _response(result)


@router.post("/{run_id}/resume", response_model=MutationResponse)
async def resume_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: LeaseRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    run = await api.workflow.get(organisation_id, run_id)
    await _require_ready_browser_session(api, run)
    result = await api.workflow.resume(
        ResumeRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            owner_id=identity.actor_id,
            expires_at=body.expires_at,
        )
    )
    return _response(result)


@router.post("/{run_id}/cleanup", response_model=MutationResponse)
async def cleanup_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: FailureRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.cleanup(
        CleanupRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
            failure=body.failure,
        )
    )
    return _response(result)


@router.post("/{run_id}/fail", response_model=MutationResponse)
async def fail_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: FailureRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.fail(
        FailRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
            failure=body.failure,
        )
    )
    return _response(result)


@router.post("/{run_id}/recover", response_model=MutationResponse)
async def recover_run(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: LeaseRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.recover(
        RecoverRunCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            owner_id=identity.actor_id,
            expires_at=body.expires_at,
        )
    )
    return _response(result)


@router.post("/{run_id}/recovery-complete", response_model=MutationResponse)
async def complete_recovery(
    organisation_id: OrganisationId,
    run_id: RunId,
    body: RecoveryCompleteRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
) -> MutationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await api.workflow.complete_recovery(
        CompleteRecoveryCommand(
            id=command_id(identity, organisation_id, key),
            organisation_id=organisation_id,
            run_id=run_id,
            actor_id=identity.actor_id,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
            recovery_id=body.recovery_id,
            mode=body.mode,
            evidence_ids=body.evidence_ids,
        )
    )
    return _response(result)


async def _require_ready_browser_session(api: ApiServices, run: RotationRun) -> None:
    if api.inventory is None:
        return
    credentials, _, _ = await api.inventory.graph(run.organisation_id)
    credential = next((item for item in credentials if item.id == run.credential_id), None)
    if credential is None:
        return
    connection = await api.inventory.get_connection(run.organisation_id, credential.connection_id)
    if connection.interface is ConnectionInterface.BROWSER and (
        connection.status is not ConnectionStatus.READY
        or connection.authorization_reference is None
    ):
        raise ResourceConflictError("connection still needs login")
