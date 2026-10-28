from contracts import (
    Connection,
    Identifier,
    ProviderCredentialMetadata,
    RuntimeResourceMetadata,
    SecretResourceMetadata,
    SecretVersionMetadata,
)
from core.auth import Permission
from core.errors import ResourceConflictError
from fastapi import APIRouter, Query, Request, status

from api.deps import Identity, required, services
from api.inventory.schemas import ArchiveRequest, ConnectionUpdateRequest, ResolveCredentialRequest
from api.inventory.shared import _audit_change, _organisation

router = APIRouter()


@router.post("/connections", response_model=Connection, status_code=status.HTTP_201_CREATED)
async def add_connection(
    organisation_id: Identifier,
    body: Connection,
    identity: Identity,
    request: Request,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    _organisation(body.organisation_id, organisation_id)
    return await required(api.inventory, "inventory").add_connection(body)


@router.get("/connections", response_model=tuple[Connection, ...])
async def list_connections(
    organisation_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[Connection, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_connections(organisation_id)


@router.get("/connections/{connection_id}", response_model=Connection)
async def get_connection(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").get_connection(organisation_id, connection_id)


@router.get(
    "/connections/{connection_id}/credential-metadata",
    response_model=tuple[ProviderCredentialMetadata, ...],
)
async def list_provider_credentials(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[ProviderCredentialMetadata, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_provider_credentials(
        organisation_id, connection_id
    )


@router.post(
    "/connections/{connection_id}/resolve-credential",
    response_model=ProviderCredentialMetadata,
)
async def resolve_credential(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: ResolveCredentialRequest,
    identity: Identity,
    request: Request,
) -> ProviderCredentialMetadata:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    return await required(api.inventory, "inventory").resolve_credential(
        organisation_id,
        connection_id,
        body.secret_store_connection_id,
        body.secret_reference,
    )


@router.get(
    "/connections/{connection_id}/runtime-resources",
    response_model=tuple[RuntimeResourceMetadata, ...],
)
async def list_runtime_resources(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[RuntimeResourceMetadata, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_runtime_resources(
        organisation_id, connection_id
    )


@router.get(
    "/connections/{connection_id}/secret-resources",
    response_model=tuple[SecretResourceMetadata, ...],
)
async def list_secret_resources(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
) -> tuple[SecretResourceMetadata, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_secret_resources(
        organisation_id, connection_id
    )


@router.get(
    "/connections/{connection_id}/secret-versions",
    response_model=tuple[SecretVersionMetadata, ...],
)
async def list_secret_versions(
    organisation_id: Identifier,
    connection_id: Identifier,
    identity: Identity,
    request: Request,
    secret: str = Query(min_length=1, max_length=1024),
) -> tuple[SecretVersionMetadata, ...]:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_READ)
    return await required(api.inventory, "inventory").list_secret_versions(
        organisation_id, connection_id, secret
    )


@router.patch("/connections/{connection_id}", response_model=Connection)
async def update_connection(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: ConnectionUpdateRequest,
    identity: Identity,
    request: Request,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    inventory = required(api.inventory, "inventory")
    current = await inventory.get_connection(organisation_id, connection_id)
    if (
        current.platform == "gmail"
        and current.authorization_reference == f"gmail://{current.id}"
        and any(
            value is not None for value in (body.capabilities, body.allowed_resources, body.region)
        )
    ):
        raise ResourceConflictError("Manage Gmail senders in its connection settings")
    changed = await inventory.update_connection(
        organisation_id,
        connection_id,
        body.expected_revision,
        display_name=body.display_name,
        capabilities=body.capabilities,
        allowed_resources=body.allowed_resources,
        region=body.region,
    )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "connection.updated",
        connection_id,
        changed.revision,
    )
    return changed


@router.post("/connections/{connection_id}/archive", response_model=Connection)
async def archive_connection(
    organisation_id: Identifier,
    connection_id: Identifier,
    body: ArchiveRequest,
    identity: Identity,
    request: Request,
) -> Connection:
    api = services(request)
    await api.access.require(identity, organisation_id, Permission.INVENTORY_WRITE)
    inventory = required(api.inventory, "inventory")
    current = await inventory.get_connection(organisation_id, connection_id)
    if current.platform == "gmail" and current.authorization_reference == f"gmail://{current.id}":
        if current.revision != body.expected_revision:
            raise ResourceConflictError("connection changed; refresh first")
        gmail = required(api.gmail, "gmail")
        mailbox = await gmail.get(organisation_id, connection_id)
        await gmail.disconnect(organisation_id, connection_id, mailbox.revision)
        changed = await inventory.get_connection(organisation_id, connection_id)
    else:
        changed = await inventory.archive_connection(
            organisation_id, connection_id, body.expected_revision, body.cascade
        )
    await _audit_change(
        api,
        organisation_id,
        identity.actor_id,
        "connection.archived",
        connection_id,
        changed.revision,
    )
    return changed
