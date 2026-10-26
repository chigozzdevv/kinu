from contracts import Identifier, OverviewSummary
from core.auth import Permission
from fastapi import APIRouter, Request

from api.deps import Identity, required, services

router = APIRouter(
    prefix="/v1/organisations/{organisation_id}/overview",
    tags=["overview"],
)


@router.get("", response_model=OverviewSummary)
async def summarise(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> OverviewSummary:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.RUN_READ)
    return await required(api.overview, "overview").summary(organisation_id)
