from connectors.aws.refs import resource_allowed
from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.secrets.store import SecretReader
from contracts import Connection, ConnectionRole, ConnectionStatus

from core.inventory.repository import InventoryRepository


class ConnectionSecrets:
    def __init__(self, inventory: InventoryRepository, secrets: SecretReader) -> None:
        self._inventory, self._secrets = inventory, secrets

    async def access_for(self, connection: Connection, version: str) -> SecretValue:
        if connection.archived_at or connection.status is not ConnectionStatus.READY:
            raise ConnectorError("connection-unavailable", "connection is not ready")
        if ConnectionRole.SECRET_STORE in connection.roles:
            return await self._secrets.access_for(connection, version)
        resource = version.partition("/versions/")[0]
        candidates = [
            item
            for item in await self._inventory.connections(connection.organisation_id)
            if ConnectionRole.SECRET_STORE in item.roles
            and item.status is ConnectionStatus.READY
            and not item.archived_at
            and resource_allowed(resource, item.allowed_resources)
        ]
        if len(candidates) != 1:
            raise ConnectorError(
                "authorization-vault-ambiguous",
                "authorization requires one matching workspace vault connection",
            )
        return await self._secrets.access_for(candidates[0], version)
