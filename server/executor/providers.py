from datetime import UTC, datetime
from typing import Annotated

from connectors.base import SecretValue
from connectors.http import HttpProviderConnector
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    Identifier,
)
from core.connection.provider import ProviderInput, ResendAccess
from core.errors import ResourceConflictError
from core.ids import new_id
from core.inventory.store import InventoryStore
from fastapi import APIRouter, Depends

from executor.deps import authenticated
from executor.runtime import Runtime

router = APIRouter()


class ProviderRequest(ProviderInput):
    organisation_id: Identifier


class DomainRequest(ResendAccess):
    organisation_id: Identifier


@router.post("/providers/resend/domains")
async def domains(
    body: DomainRequest, service: Annotated[Runtime, Depends(authenticated)]
) -> object:
    with SecretValue(body.token.get_secret_value().encode()) as value:
        return await service.providers.resend.domains(value)


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
    if body.platform == "resend":
        with SecretValue(body.token.get_secret_value().encode()) as token:
            domains = await service.providers.resend.domains(token)
        if not any(item["id"] == body.domain_id for item in domains):
            raise ResourceConflictError("Choose a verified Resend domain")
    await service.vault.put(
        body.organisation_id, identity, {"token": body.token.get_secret_value()}
    )
    try:
        await service.providers.metadata(connection)
        return await InventoryStore(service.database).add_connection(connection)
    except BaseException:
        await service.vault.database.collection("secrets").delete_one(
            {"organisation_id": body.organisation_id, "id": identity}
        )
        raise
