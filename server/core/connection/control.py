from typing import Any, Literal

from connectors.aws import AwsApi
from connectors.aws.runtime import AwsRuntime
from connectors.aws.secrets import AwsSecrets
from connectors.aws.verify import AwsLinkVerifier
from connectors.http import HttpProviderConnector
from connectors.secrets.store import SecretReader
from contracts import Connection, ConnectionRole, ConnectionStatus, Contract, Identifier
from pydantic import Field

from core.connection.models import AwsLink
from core.connection.roles import ConnectionRoles
from core.connection.scope import managed_role
from core.connection.secrets import ConnectionSecrets
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.inventory.store import InventoryStore
from core.storage.database import Database
from core.storage.records import Records


class ControlRequest(Contract):
    operation: Literal["verify", "provider", "identity", "secrets", "versions", "runtimes"]
    organisation_id: Identifier
    connection_id: Identifier
    revision: int = Field(ge=0)
    link_revision: int | None = Field(default=None, ge=0)
    candidate: Connection | None = None
    secret_connection_id: Identifier | None = None
    secret_reference: str | None = Field(default=None, max_length=1024)


async def control(
    database: Database,
    aws: AwsApi,
    request: ControlRequest,
    authorizations: SecretReader | None = None,
    providers: HttpProviderConnector | None = None,
) -> Any:
    rows = Records(database)
    inventory = InventoryStore(database)
    try:
        connection = await inventory.get_connection(request.organisation_id, request.connection_id)
    except ResourceNotFoundError as error:
        if request.operation != "provider":
            raise
        candidate = request.candidate
        if (
            candidate is None
            or candidate.id != request.connection_id
            or candidate.organisation_id != request.organisation_id
            or candidate.roles != frozenset({ConnectionRole.PROVIDER})
            or candidate.http is None
            or candidate.revision != 0
            or candidate.status is not ConnectionStatus.SETUP_REQUIRED
        ):
            raise ResourceConflictError(
                "provider validation requires an unsaved workspace draft"
            ) from error
        connection = candidate.model_copy(update={"status": ConnectionStatus.READY})
    if connection.revision != request.revision or connection.archived_at:
        raise ResourceConflictError("connection changed before this operation")
    if request.operation == "verify":
        async with rows.read() as session:
            link = await session.get(AwsLink, request.organisation_id, connection.id)
        managed_role(link)
        if link.revision != request.link_revision or link.region != connection.region:
            raise ResourceConflictError("AWS role link changed")
        await AwsLinkVerifier(aws).verify(link, connection)
        return True
    if connection.status is not ConnectionStatus.READY:
        raise ResourceConflictError("connection is not ready")
    roles = ConnectionRoles(database, managed=True, read_only=True)
    secrets = AwsSecrets(aws, roles)
    provider = providers or HttpProviderConnector(
        authorizations or ConnectionSecrets(inventory, secrets)
    )
    if request.operation == "provider":
        return await provider.metadata(connection)
    if request.operation == "identity":
        if not request.secret_connection_id or not request.secret_reference:
            raise ResourceConflictError("credential identity requires a selected secret version")
        store = await inventory.get_connection(
            request.organisation_id, request.secret_connection_id
        )
        return await provider.credential_identity(connection, store, request.secret_reference)
    if request.operation == "secrets":
        return await secrets.resources_for(connection)
    if request.operation == "versions" and request.secret_reference:
        return await secrets.versions_for(connection, request.secret_reference)
    if request.operation == "runtimes":
        return await AwsRuntime(aws, roles).resources_for(connection)
    raise ResourceConflictError("connection operation is incomplete")
