from contracts import AccountSession, Contract, OrganisationMembership
from core.ids import new_id
from fastapi import APIRouter, Request, Response, status
from pydantic import Field

from api.deps import Identity, required, services

router = APIRouter(prefix="/v1", tags=["auth"])


class OrganisationRequest(Contract):
    name: str = Field(min_length=1, max_length=120)


@router.get("/session", response_model=AccountSession)
async def account_session(identity: Identity, request: Request) -> AccountSession:
    return await required(services(request).accounts, "accounts").session(identity)


@router.post(
    "/organisations",
    response_model=OrganisationMembership,
    status_code=status.HTTP_201_CREATED,
)
async def create_organisation(
    body: OrganisationRequest,
    identity: Identity,
    request: Request,
) -> OrganisationMembership:
    api = services(request)
    membership = await required(api.accounts, "accounts").create_organisation(
        identity,
        body.name,
    )
    if api.audit is not None:
        await api.audit.append(
            new_id("audit"),
            membership.organisation.id,
            "organisation.created",
            identity.actor_id,
            f"organisations/{membership.organisation.id}",
            {"revision": membership.organisation.revision},
        )
    return membership


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    response.delete_cookie("__session", path="/", secure=True, httponly=True, samesite="lax")
    return response
