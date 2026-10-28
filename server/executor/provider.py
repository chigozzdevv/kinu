import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from browser.catalog import Catalog
from browser.probe import Probe
from browser.worker import Worker
from connectors.base import ConnectorContext, ConnectorResponse, SecretValue
from connectors.base.errors import ConnectorError
from connectors.http import HttpProviderConnector
from connectors.resend.browser import BrowserResend
from connectors.resend.client import Resend
from connectors.secrets.store import SecretReader
from contracts import (
    Connection,
    ConnectionInterface,
    CredentialGeneration,
    ManagedCredential,
    RotationRun,
    Stage,
)
from core.errors import ResourceConflictError
from core.storage.records import Records
from core.storage.runs import RunStore

from executor.receipts import Receipts
from executor.versions import Versions


class Providers(HttpProviderConnector):
    def __init__(
        self,
        authorizations: SecretReader,
        browser: Worker,
        versions: Versions,
        probe: Probe | None = None,
    ) -> None:
        super().__init__(authorizations)
        self.browser, self.versions, self.probe = browser, versions, probe or Probe()
        self.catalog: Catalog = browser.catalog
        self.receipts = Receipts(self.catalog.vault)
        self.resend = Resend(authorizations, self.receipts)
        self.resend_browser = BrowserResend(self.catalog, browser, self.resend, self.receipts)
        self.proofs = self.catalog.vault.database.collection("browser_keys")

    async def preflight(self, connection: Connection, run: RotationRun) -> None:
        if connection.interface is ConnectionInterface.BROWSER:
            if connection.platform == "resend":
                await self.resend_browser.validate(connection)
            await self.browser.lock(connection, run)

    async def metadata(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        if connection.platform == "resend" and connection.interface is ConnectionInterface.API:
            return await self.resend.metadata(connection)
        if connection.interface is not ConnectionInterface.BROWSER:
            return await super().metadata(connection)
        if connection.platform == "resend":
            return await self.resend_browser.metadata(connection)
        version = await self.catalog.definition(connection)
        config = version.definition.verifier
        if config is None:
            raise ResourceConflictError("Configure the browser credential verifier")
        result = []
        for identity in await self.browser.ids(connection):
            row = await self.proofs.find_one(
                {
                    "organisation_id": connection.organisation_id,
                    "connection_id": connection.id,
                    "provider_id": identity,
                }
            )
            result.append(
                (
                    row["metadata"]
                    if row
                    else {"provider_id": identity, "kind": config.kind, "scopes": []}
                )
                | {"name": identity, "status": "active", "disabled": False}
            )
        return tuple(result)

    async def identify(self, connection: Connection, value: SecretValue) -> str:
        if connection.platform == "resend" and connection.interface is ConnectionInterface.API:
            return await self.resend.identify(connection, value)
        if connection.interface is not ConnectionInterface.BROWSER:
            return await super().identify(connection, value)
        if connection.platform == "resend":
            return await self.resend_browser.identify(connection, value)
        version = await self.catalog.definition(connection)
        config = version.definition.verifier
        if config is None:
            raise ResourceConflictError("Configure the browser credential verifier")
        code, metadata = await self.probe.check(config, value)
        if code not in config.operation.success_statuses:
            raise ConnectorError("browser-probe", "Provider rejected the selected credential")
        identity = str(metadata["provider_id"])
        await self.proofs.update_one(
            {
                "organisation_id": connection.organisation_id,
                "connection_id": connection.id,
                "provider_id": identity,
            },
            {"$set": {"metadata": metadata, "verified_at": datetime.now(UTC)}},
            upsert=True,
        )
        return identity

    async def test_credential(self, connection: Connection, value: SecretValue) -> int:
        if connection.platform == "resend" and connection.interface is ConnectionInterface.API:
            return await self.resend.test_credential(connection, value)
        if connection.interface is not ConnectionInterface.BROWSER:
            return await super().test_credential(connection, value)
        if connection.platform == "resend":
            return await self.resend_browser.test_credential(connection, value)
        config = (await self.catalog.definition(connection)).definition.verifier
        if config is None:
            raise ResourceConflictError("Configure the browser credential verifier")
        return (await self.probe.check(config, value))[0]

    async def auth_fingerprint(self, connection: Connection) -> str:
        if connection.interface is not ConnectionInterface.BROWSER:
            return await super().auth_fingerprint(connection)
        return hashlib.sha256(
            json.dumps(await self.catalog.state(connection), sort_keys=True).encode()
        ).hexdigest()

    async def execute(
        self, tool: str, payload: dict[str, Any], context: ConnectorContext
    ) -> ConnectorResponse:
        if (
            context.connection.platform == "resend"
            and context.connection.interface is ConnectionInterface.API
        ):
            return await self.resend.execute(tool, payload, context)
        if context.connection.interface is not ConnectionInterface.BROWSER:
            return await super().execute(tool, payload, context)
        if tool != "provider.createCredential" or context.run is None:
            raise ResourceConflictError("Browser provider only accepts a fenced creation stage")
        if context.connection.platform == "resend":
            await self.resend_browser.validate(context.connection)
        capture = await self.browser.perform(context.connection, context.run, Stage.CREATE)
        if capture is None:
            raise ResourceConflictError("Browser did not capture a replacement credential")
        store = await self.browser.catalog.database.collection("inventory").find_one(
            {
                "organisation_id": context.run.organisation_id,
                "kind": "credential",
                "id": context.run.credential_id,
            }
        )
        if not store:
            raise ResourceConflictError("Credential mapping changed")
        async with Records(self.catalog.database).read() as rows:
            target = await rows.get(
                Connection, context.run.organisation_id, store["data"]["secret_store_connection_id"]
            )
        value = await self.versions.access_for(target, capture.secret_reference)
        try:
            if context.connection.platform == "resend":
                await self.resend_browser.remember(context.connection, capture.provider_id, value)
            if await self.identify(context.connection, value) != capture.provider_id:
                raise ResourceConflictError("Captured key identity does not match the issuer")
        except BaseException:
            value.clear()
            raise
        return ConnectorResponse(
            result={"provider_id": capture.provider_id, "name": capture.provider_display_name},
            secret=value,
        )

    async def revoke_credential(self, connection: Connection, provider_id: str) -> None:
        if connection.platform == "resend" and connection.interface is ConnectionInterface.API:
            return await self.resend.revoke_credential(connection, provider_id)
        if connection.interface is not ConnectionInterface.BROWSER:
            return await super().revoke_credential(connection, provider_id)
        name = None
        if connection.platform == "resend":
            await self.resend_browser.validate(connection)
            metadata = await self.resend_browser.metadata(connection)
            name = next(
                (item.get("name") for item in metadata if item["provider_id"] == provider_id), None
            )
            if not isinstance(name, str) or not name:
                raise ResourceConflictError("Selected Resend key has no verified name")
        matches = []
        async with Records(self.catalog.database).read() as rows:
            for generation in await rows.match(
                CredentialGeneration, connection.organisation_id, {"provider_id": provider_id}
            ):
                credential = await rows.get(
                    ManagedCredential, connection.organisation_id, generation.credential_id
                )
                if credential.connection_id == connection.id and not credential.archived_at:
                    matches.append(credential)
        if len(matches) != 1:
            raise ResourceConflictError("Browser key ownership is ambiguous")
        lock = await self.catalog.database.collection("run_locks").find_one(
            {"organisation_id": connection.organisation_id, "credential_id": matches[0].id}
        )
        if not lock:
            raise ResourceConflictError("Browser revocation needs its active rotation lock")
        run = await RunStore(self.catalog.database).get(connection.organisation_id, lock["run_id"])
        await self.browser.perform(connection, run, Stage.REVOKE, provider_id, name)
