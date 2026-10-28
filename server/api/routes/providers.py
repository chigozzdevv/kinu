from contracts import Connection, Contract, HttpProviderApi, Identifier
from core.auth import Permission
from fastapi import APIRouter, Request
from pydantic import Field, SecretStr

from api.deps import Identity, required, services

router = APIRouter(prefix="/v1/organisations/{organisation_id}/connections", tags=["connections"])


class ProviderRequest(Contract):
    display_name: str = Field(min_length=1, max_length=100)
    platform: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    http: HttpProviderApi
    token: SecretStr = Field(min_length=1, max_length=16384)


@router.post("/provider", status_code=201)
async def connect(
    organisation_id: Identifier, body: ProviderRequest, identity: Identity, request: Request
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return Connection.model_validate(
        await required(api.private, "private execution").call(
            "/providers",
            {
                "organisation_id": organisation_id,
                "display_name": body.display_name,
                "platform": body.platform,
                "http": body.http.model_dump(mode="json"),
                "token": body.token.get_secret_value(),
            },
        )
    )
