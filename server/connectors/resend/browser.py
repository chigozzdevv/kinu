from typing import Any

from browser.catalog import Catalog
from browser.worker import Worker
from contracts import Connection, ConnectionStatus
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.records import Records

from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.resend.client import CreationRecords, Resend
from connectors.resend.config import scopes


class BrowserResend:
    def __init__(
        self, catalog: Catalog, browser: Worker, api: Resend, receipts: CreationRecords
    ) -> None:
        self.catalog, self.browser, self.api, self.receipts = catalog, browser, api, receipts

    async def source(self, connection: Connection) -> Connection:
        if connection.platform != "resend" or not connection.verification_connection_id:
            raise ResourceConflictError("Select a Resend verification account")
        async with Records(self.catalog.database).read() as rows:
            source = await rows.get(
                Connection, connection.organisation_id, connection.verification_connection_id
            )
        self.api.validate(source)
        if source.archived_at or source.status is not ConnectionStatus.READY:
            raise ResourceConflictError("Reconnect the Resend verification account")
        return source

    async def validate(self, connection: Connection) -> Connection:
        source = await self.source(connection)
        version = await self.catalog.definition(connection)
        if version.definition.platform != connection.platform:
            raise ResourceConflictError("Attach a published Resend computer-use playbook")
        return source

    async def metadata(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        source = await self.source(connection)
        assert source.http
        account = {item["provider_id"]: item for item in await self.api.metadata(source)}
        version = await self.catalog.definition(connection)
        definition = version.definition
        browser_inventory = definition.verifier.credential_ids if definition.verifier else None
        browser_inventory = browser_inventory or definition.credential_ids
        identities = (
            await self.browser.ids(connection)
            if browser_inventory is not None
            else tuple(account)
        )
        if browser_inventory is not None and set(identities) != set(account):
            raise ConnectorError("resend-account", "Browser and verification accounts differ")
        result = []
        for identity in identities:
            record = await self.receipts.metadata(connection, identity)
            item = account[identity]
            recorded = record or (item if item.get("status") == "active" else None)
            result.append(
                {
                    **item,
                    "scopes": recorded["scopes"] if recorded else [],
                    "status": "active" if recorded else "unverified",
                }
            )
        return tuple(result)

    async def identify(self, connection: Connection, value: SecretValue) -> str:
        source = await self.source(connection)
        assert source.http
        try:
            record = await self.receipts.identify(connection, value)
        except ResourceNotFoundError:
            record = await self.receipts.identify(source, value)
        if record["metadata"]["scopes"] != scopes(source.http):
            raise ConnectorError("resend-scope", "Resend domain or permissions changed")
        if await self.api.proof.check(value) != "sending_access":
            raise ConnectorError("resend-proof", "Resend key is no longer active")
        identity = str(record["provider_id"])
        if not any(item["provider_id"] == identity for item in await self.metadata(connection)):
            raise ConnectorError("resend-proof", "Resend key is no longer listed")
        return identity

    async def remember(self, connection: Connection, identity: str, value: SecretValue) -> None:
        source = await self.source(connection)
        assert source.http
        if not any(item["provider_id"] == identity for item in await self.api.metadata(source)):
            raise ConnectorError(
                "resend-account", "Captured key is absent from the linked API account"
            )
        metadata = {"kind": "api-key", "scopes": scopes(source.http)}
        await self.receipts.remember(connection, identity, value, metadata)
        await self.receipts.remember(source, identity, value, metadata)

    async def test_credential(self, connection: Connection, value: SecretValue) -> int:
        return await self.api.test_credential(await self.source(connection), value)
