from datetime import timedelta

from contracts import AgentMemory, AgentRegistration, Contract, Identifier
from core.auth import Permission
from fastapi import APIRouter, Request, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/agents",
    tags=["agents"],
)


class MemoryRequest(Contract):
    id: Identifier
    fact: str = Field(min_length=1, max_length=2048)
    provenance: tuple[Identifier, ...] = Field(min_length=1)
    ttl_days: int = Field(default=30, ge=1, le=365)


@router.get("", response_model=tuple[AgentRegistration, ...])
async def list_agents(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[AgentRegistration, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.AGENT_READ)
    return await required(api.agent_repository, "agent repository").list(organisation_id)


@router.post(
    "/{agent_id}/memories",
    response_model=AgentMemory,
    status_code=status.HTTP_201_CREATED,
)
async def remember(
    organisation_id: Identifier,
    agent_id: Identifier,
    body: MemoryRequest,
    identity: Identity,
    request: Request,
) -> AgentMemory:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.AGENT_WRITE)
    registration = await required(api.agent_repository, "agent repository").get(
        organisation_id, agent_id
    )
    return await required(api.agent_continuity, "agent continuity").remember(
        registration,
        body.id,
        body.fact,
        body.provenance,
        identity.actor_id,
        timedelta(days=body.ttl_days),
    )
