from contracts import Contract, Identifier, Probe, ProbeDefinition, ProbeVersion
from core.auth import Permission
from fastapi import APIRouter, Request, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/probes",
    tags=["verification"],
)


class CreateProbeRequest(Contract):
    id: Identifier
    name: str = Field(min_length=1, max_length=160)


@router.post("", response_model=Probe, status_code=status.HTTP_201_CREATED)
async def create_probe(
    organisation_id: Identifier,
    body: CreateProbeRequest,
    identity: Identity,
    request: Request,
) -> Probe:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.VERIFICATION_WRITE)
    return await required(api.probes, "probes").create(organisation_id, body.id, body.name)


@router.post(
    "/{probe_id}/versions",
    response_model=ProbeVersion,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    organisation_id: Identifier,
    probe_id: Identifier,
    body: ProbeDefinition,
    identity: Identity,
    request: Request,
) -> ProbeVersion:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.VERIFICATION_WRITE)
    return await required(api.probes, "probes").create_version(
        organisation_id, probe_id, body, identity.actor_id
    )


@router.post(
    "/{probe_id}/versions/{version_id}/activate",
    response_model=ProbeVersion,
)
async def activate_version(
    organisation_id: Identifier,
    probe_id: Identifier,
    version_id: Identifier,
    identity: Identity,
    request: Request,
) -> ProbeVersion:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.VERIFICATION_APPROVE)
    return await required(api.probes, "probes").activate(
        organisation_id, probe_id, version_id, identity.actor_id
    )
