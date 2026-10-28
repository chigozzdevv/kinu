from datetime import UTC, datetime
from typing import Annotated

from connectors.http import HttpProviderConnector
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    Contract,
    HttpProviderApi,
    Identifier,
)
from core.ids import new_id
from core.inventory.store import InventoryStore
from fastapi import APIRouter, Depends
from pydantic import Field, SecretStr

from executor.authorization import Authorization
from executor.deps import authenticated
from executor.runtime import Runtime

router = APIRouter()


class ProviderRequest(Contract):
    organisation_id: Identifier
    display_name: str = Field(min_length=1, max_length=100)
    platform: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    http: HttpProviderApi
    token: SecretStr = Field(min_length=1, max_length=16384)


@router.post("/providers")
async def create(
    body: ProviderRequest, service: Annotated[Runtime, Depends(authenticated)]
) -> Connection:
    now, identity = datetime.now(UTC), new_id("connection")
    connection = Connection(
        id=identity,
        organisation_id=body.organisation_id,
        platform=body.platform,
        display_name=body.display_name,
        roles=frozenset({ConnectionRole.PROVIDER}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.API_KEY,
        authorization_reference="private://" + identity,
        capabilities=HttpProviderConnector.tools,
        allowed_resources=(body.platform + ":credentials:*",),
        http=body.http,
        status=ConnectionStatus.READY,
        region="global",
        created_at=now,
        updated_at=now,
    )
    await service.vault.put(
        body.organisation_id, identity, {"token": body.token.get_secret_value()}
    )
    try:
        await HttpProviderConnector(Authorization(service.vault)).metadata(connection)
        return await InventoryStore(service.database).add_connection(connection)
    except BaseException:
        await service.vault.database.collection("secrets").delete_one(
            {"organisation_id": body.organisation_id, "id": identity}
        )
        raise
