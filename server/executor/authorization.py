from connectors.base import SecretValue
from connectors.secrets.store import SecretReader
from contracts import Connection
from core.errors import ResourceConflictError

from executor.vault import Vault


class Authorization:
    def __init__(self, vault: Vault, fallback: SecretReader | None = None) -> None:
        self.vault, self.fallback = vault, fallback

    async def access_for(self, connection: Connection, reference: str) -> SecretValue:
        if reference.startswith("private://"):
            if reference != "private://" + connection.id:
                raise ResourceConflictError("Authorization does not belong to this connection")
            saved = await self.vault.get(connection.organisation_id, connection.id)
            return SecretValue(saved["token"].encode())
        if self.fallback:
            return await self.fallback.access_for(connection, reference)
        raise ResourceConflictError("Authorization is unavailable")
