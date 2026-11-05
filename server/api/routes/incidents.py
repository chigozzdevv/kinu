from typing import Annotated

from contracts import (
    Contract,
    Identifier,
    Incident,
    IncidentStatus,
    IngestionEvent,
    RotationRun,
    RunStatus,
)
from core.auth import Permission
from core.errors import ResourceConflictError
from fastapi import APIRouter, Query, Request, Response, status
from mail.reports import IncidentKind, ReportOptions, ReportRequest, ReportStatus
from pydantic import AwareDatetime, Field

from api.deps import IdempotencyKey, Identity, command_id, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/incidents",
    tags=["incidents"],
)


class IncidentResponse(Contract):
    incident: Incident
    applied: bool


class ConfirmRequest(Contract):
    expected_revision: int = Field(ge=0)
    credential_id: Identifier


class RotationRequest(Contract):
    control_version: Identifier
    reason: str = Field(min_length=1, max_length=1024)
    urgency: str = Field(min_length=1, max_length=32)
    received_at: AwareDatetime


class DismissRequest(Contract):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1024)


class RotationResponse(Contract):
    incident: Incident
    run: RotationRun
    applied: bool


@router.get("/report-options", response_model=ReportOptions)
async def report_options(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
    credential_id: Identifier | None = None,
    kind: IncidentKind = "exposure",
) -> ReportOptions:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    result = await required(api.private, "incident reporting").call(
        "/incident-sources",
        {
            "operation": "report-options",
            "organisation_id": organisation_id,
            "credential_id": credential_id,
            "kind": kind,
        },
    )
    return ReportOptions.model_validate(result | {"recipient": identity.email})


@router.post("/reports", response_model=ReportStatus)
async def report(
    organisation_id: Identifier,
    body: ReportRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
    response: Response,
) -> ReportStatus:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INCIDENT_WRITE)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    result = await required(api.private, "incident reporting").call(
        "/incident-sources",
        {
            "operation": "report-create",
            "organisation_id": organisation_id,
            "subject": identity.subject,
            "request_id": command_id(identity, organisation_id, key),
            "report": body.model_dump(mode="json"),
        },
    )
    response.status_code = status.HTTP_202_ACCEPTED if body.simulate else status.HTTP_201_CREATED
    return ReportStatus.model_validate(result)


@router.get("/reports/{report_id}", response_model=ReportStatus)
async def report_status(
    organisation_id: Identifier,
    report_id: Identifier,
    identity: Identity,
    request: Request,
) -> ReportStatus:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INCIDENT_WRITE)
    return ReportStatus.model_validate(
        await required(api.private, "incident reporting").call(
            "/incident-sources",
            {
                "operation": "report-status",
                "organisation_id": organisation_id,
                "request_id": report_id,
            },
        )
    )


@router.post("/ingest", response_model=IncidentResponse)
async def ingest(
    organisation_id: Identifier,
    body: IngestionEvent,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
    response: Response,
) -> IncidentResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INCIDENT_WRITE)
    if body.organisation_id != organisation_id:
        raise ResourceConflictError("incident crosses organisation boundary")
    incident_id = command_id(identity, organisation_id, key).replace("cmd_", "incident_", 1)
    incident, applied = await required(api.incidents, "incidents").ingest(incident_id, body)
    response.status_code = status.HTTP_201_CREATED if applied else status.HTTP_200_OK
    return IncidentResponse(incident=incident, applied=applied)


@router.get("", response_model=tuple[Incident, ...])
async def list_incidents(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
    incident_status: Annotated[list[IncidentStatus] | None, Query(alias="status")] = None,
    limit: int = Query(default=100, ge=1, le=500),
    before: Identifier | None = None,
) -> tuple[Incident, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    statuses = frozenset(incident_status) if incident_status is not None else None
    return await required(api.incidents, "incidents").list_incidents(
        organisation_id, statuses, limit, before
    )


@router.get("/{incident_id}", response_model=Incident)
async def get_incident(
    organisation_id: Identifier,
    incident_id: Identifier,
    identity: Identity,
    request: Request,
) -> Incident:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.incidents, "incidents").get(organisation_id, incident_id)


@router.post("/{incident_id}/confirm", response_model=Incident)
async def confirm(
    organisation_id: Identifier,
    incident_id: Identifier,
    body: ConfirmRequest,
    identity: Identity,
    request: Request,
) -> Incident:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INCIDENT_WRITE)
    return await required(api.incidents, "incidents").confirm(
        organisation_id,
        incident_id,
        body.expected_revision,
        body.credential_id,
        identity.actor_id,
    )


@router.post("/{incident_id}/rotate", response_model=RotationResponse)
async def rotate(
    organisation_id: Identifier,
    incident_id: Identifier,
    body: RotationRequest,
    identity: Identity,
    key: IdempotencyKey,
    request: Request,
    response: Response,
) -> RotationResponse:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INCIDENT_WRITE)
    await api.access.require(identity, organisation_id, Permission.RUN_WRITE)
    service = required(api.incidents, "incidents")
    existing = await service.get(organisation_id, incident_id)
    if existing.credential_id is None:
        raise ResourceConflictError("incident requires a confirmed credential")
    dispatcher = required(api.dispatcher, "rotation executor")
    await dispatcher.require_scope(organisation_id, existing.credential_id)
    inventory = required(api.inventory, "inventory")
    credential = await inventory.get_credential(organisation_id, existing.credential_id)
    controls = await inventory.get_controls(
        organisation_id, existing.credential_id, body.control_version
    )
    if credential.control_version != controls.id:
        raise ResourceConflictError("incident controls changed; refresh before starting rotation")
    incident, run, applied = await service.start_rotation(
        organisation_id,
        incident_id,
        command_id(identity, organisation_id, key),
        identity.actor_id,
        body.control_version,
        body.reason,
        body.urgency,
        body.received_at,
    )
    if run.status is RunStatus.PENDING:
        await dispatcher.submit(organisation_id, run.id)
    response.status_code = status.HTTP_201_CREATED if applied else status.HTTP_200_OK
    return RotationResponse(incident=incident, run=run, applied=applied)


@router.post("/{incident_id}/dismiss", response_model=Incident)
async def dismiss(
    organisation_id: Identifier,
    incident_id: Identifier,
    body: DismissRequest,
    identity: Identity,
    request: Request,
) -> Incident:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INCIDENT_WRITE)
    return await required(api.incidents, "incidents").dismiss(
        organisation_id,
        incident_id,
        body.expected_revision,
        body.reason,
        identity.actor_id,
    )
