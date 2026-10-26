from typing import Annotated

from contracts import (
    Contract,
    Identifier,
    Incident,
    IncidentStatus,
    IngestionEvent,
    RotationRun,
)
from core.auth import Permission
from core.errors import ResourceConflictError
from fastapi import APIRouter, Query, Request, Response, status
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
) -> tuple[Incident, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    statuses = frozenset(incident_status) if incident_status is not None else None
    return await required(api.incidents, "incidents").list_incidents(
        organisation_id, statuses, limit
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
    incident, run, applied = await required(api.incidents, "incidents").start_rotation(
        organisation_id,
        incident_id,
        command_id(identity, organisation_id, key),
        identity.actor_id,
        body.control_version,
        body.reason,
        body.urgency,
        body.received_at,
    )
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
