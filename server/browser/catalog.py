from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from contracts import (
    Connection,
    ConnectionInterface,
    ConnectionStatus,
    PlaybookState,
    PlaybookVersion,
)
from core.errors import ResourceConflictError
from core.storage.database import Database
from core.storage.records import Records
from executor.vault import Vault
from policy import digest

from browser.auth import validate_storage_state
from browser.driver import AuthenticationRequiredError


class Catalog:
    def __init__(self, database: Database, vault: Vault) -> None:
        self.database, self.vault = database, vault

    @asynccontextmanager
    async def authenticated(self, connection: Connection) -> AsyncIterator[None]:
        try:
            yield
        except AuthenticationRequiredError:
            async with Records(self.database).write(connection.organisation_id) as rows:
                current = await rows.get(Connection, connection.organisation_id, connection.id)
                if current == connection and not current.archived_at:
                    await rows.replace(
                        current.model_copy(
                            update={
                                "status": ConnectionStatus.REAUTHENTICATION,
                                "updated_at": datetime.now(UTC),
                                "revision": current.revision + 1,
                            }
                        ),
                        current.revision,
                    )
            raise ResourceConflictError("Provider sign-in expired; reconnect the browser") from None

    async def definition(self, connection: Connection) -> PlaybookVersion:
        if (
            connection.interface is not ConnectionInterface.BROWSER
            or not connection.playbook_version_id
        ):
            raise ResourceConflictError("Attach a published browser playbook")
        async with Records(self.database).read() as rows:
            version = await rows.get(
                PlaybookVersion, connection.organisation_id, connection.playbook_version_id
            )
        if (
            version.playbook_id != connection.playbook_id
            or version.state is not PlaybookState.PUBLISHED
            or version.digest != digest(version.definition)
        ):
            raise ResourceConflictError("Browser playbook is unavailable or changed")
        if not set(version.definition.allowed_domains) <= set(connection.allowed_resources):
            raise ResourceConflictError("Browser playbook domains exceed the connection")
        return version

    async def state(self, connection: Connection) -> dict[str, Any]:
        prefix = f"browser://{connection.id}/"
        reference = connection.authorization_reference or ""
        if (
            not reference.startswith(prefix)
            or connection.archived_at
            or connection.status != "ready"
            or (
                connection.authorization_expires_at is not None
                and connection.authorization_expires_at <= datetime.now(UTC)
            )
        ):
            raise ResourceConflictError("Reconnect the provider browser")
        grant = await self.vault.get(connection.organisation_id, reference[len(prefix) :])
        if grant.get("connection_id") != connection.id:
            raise ResourceConflictError("Browser authentication belongs to another connection")
        return validate_storage_state(grant["state"], connection.allowed_resources)
