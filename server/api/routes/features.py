from contracts import Contract, Identifier
from core.auth import Permission
from fastapi import APIRouter, Request

from api.deps import Identity, services

router = APIRouter(prefix="/v1/organisations/{organisation_id}/features", tags=["settings"])


class Features(Contract):
    aws_connections: bool
    github: bool
    provider_api: bool
    browser_setup: bool
    browser_execution: bool
    playbook_generation: bool


@router.get("", response_model=Features)
async def available(organisation_id: Identifier, identity: Identity, request: Request) -> Features:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_READ)
    return Features(
        aws_connections=api.connections is not None,
        github=api.github is not None,
        provider_api=api.inventory is not None and api.inventory.provider_available,
        browser_setup=api.browser_setup is not None,
        browser_execution=api.browsers is not None,
        playbook_generation=api.walkthroughs is not None and api.agents is not None,
    )
