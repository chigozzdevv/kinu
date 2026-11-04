from contracts import Contract, Identifier
from core.auth import Permission
from core.errors import ResourceNotFoundError
from core.github.bridge import GitHubBridge
from fastapi import APIRouter, Request

from api.deps import Identity, services

router = APIRouter(prefix="/v1/organisations/{organisation_id}/features", tags=["settings"])


class Features(Contract):
    hosting: bool
    aws_connections: bool
    github: bool
    gmail: bool
    email: bool = False
    provider_api: bool
    browser_setup: bool
    browser_execution: bool
    playbook_generation: bool


@router.get("", response_model=Features)
async def available(organisation_id: Identifier, identity: Identity, request: Request) -> Features:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_READ)
    try:
        sources = (
            await api.private.call("/incident-sources", {"operation": "status"})
            if api.private
            else {}
        )
    except ResourceNotFoundError:
        sources = {}
    return Features(
        hosting=api.private is not None,
        aws_connections=api.connections is not None,
        github=bool(sources.get("github"))
        if isinstance(api.github, GitHubBridge)
        else api.github is not None,
        email=bool(sources.get("email")),
        gmail=await api.gmail.ready() if api.gmail else False,
        provider_api=api.inventory is not None and api.inventory.provider_available,
        browser_setup=api.browser_setup is not None,
        browser_execution=api.private is not None,
        playbook_generation=api.walkthroughs is not None and api.playbook_builder is not None,
    )
