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
from connectors.http.client import accepted_response
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
    def creation_scopes(self, connection: Connection, requested: frozenset[str]) -> frozenset[str]:
        if _legacy_resend_api(connection):
            return self.resend.creation_scopes(connection, requested)
        return super().creation_scopes(connection, requested)

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
            if _legacy_resend_browser(connection):
                await self.resend_browser.validate(connection)
            await self.browser.lock(connection, run)

    async def metadata(self, connection: Connection) -> tuple[dict[str, Any], ...]:
        if _legacy_resend_api(connection):
            return await self.resend.metadata(connection)
        if connection.interface is not ConnectionInterface.BROWSER:
            values = await super().metadata(connection)
            if _api_receipt_mode(connection):
                result = []
                for item in values:
                    receipt = await self.receipts.metadata(connection, str(item["provider_id"]))
                    metadata = item | {
                        "kind": receipt.get("kind", item.get("kind", "api-key"))
                        if receipt
                        else item.get("kind", "api-key"),
                        "scopes": receipt.get("scopes", item.get("scopes", []))
                        if receipt
                        else item.get("scopes", []),
                        "status": (item.get("status") or "active") if receipt else "unverified",
                        "disabled": item.get("disabled", False),
                    }
                    result.append(metadata)
                return tuple(result)
            return values
        if _legacy_resend_browser(connection):
            return await self.resend_browser.metadata(connection)
        version = await self.catalog.definition(connection)
        config = version.definition.verifier
        if config is None:
            raise ResourceConflictError("Configure the browser credential verifier")
        result = []
        for identity in await self.browser.ids(connection):
            receipt = await self.receipts.metadata(connection, identity)
            row = await self.proofs.find_one(
                {
                    "organisation_id": connection.organisation_id,
                    "connection_id": connection.id,
                    "provider_id": identity,
                }
            )
            saved = row["metadata"] if row else {}
            metadata = {"provider_id": identity, **(receipt or {}), **saved}
            status = metadata.get("status") or ("active" if row else "unverified")
            if config.mode == "creation-receipt":
                status = (metadata.get("status") or "active") if receipt else "unverified"
            result.append(
                metadata
                | {
                    "name": (receipt or {}).get("name") or metadata.get("name") or identity,
                    "kind": (receipt or {}).get("kind", metadata.get("kind", config.kind)),
                    "scopes": (receipt or {}).get("scopes", metadata.get("scopes", [])),
                    "status": status,
                    "disabled": metadata.get("disabled", False),
                }
            )
        return tuple(result)

    async def identify(self, connection: Connection, value: SecretValue) -> str:
        if _legacy_resend_api(connection):
            return await self.resend.identify(connection, value)
        if connection.interface is not ConnectionInterface.BROWSER:
            if _api_receipt_mode(connection):
                receipt = await self.receipts.identify(connection, value)
                api = connection.http
                if api is None or api.test_credential is None:
                    raise ResourceConflictError("Configure this playbook's credential check")
                response = await self.credential_response(connection, value)
                if not accepted_response(api.test_credential, response):
                    raise ConnectorError("provider-proof", "Provider rejected the selected key")
                identity = str(receipt["provider_id"])
                listed = await super().metadata(connection)
                if not any(item["provider_id"] == identity for item in listed):
                    raise ConnectorError("provider-proof", "Provider key is no longer listed")
                return identity
            return await super().identify(connection, value)
        if _legacy_resend_browser(connection):
            return await self.resend_browser.identify(connection, value)
        version = await self.catalog.definition(connection)
        config = version.definition.verifier
        if config is None:
            raise ResourceConflictError("Configure the browser credential verifier")
        if config.mode == "creation-receipt":
            receipt = await self.receipts.identify(connection, value)
            _, metadata = await self.probe.check(config, value)
            if metadata.get("status") != "active":
                raise ConnectorError("browser-probe", "Provider rejected the selected credential")
            identity = str(receipt["provider_id"])
            if identity not in await self.browser.ids(connection):
                raise ConnectorError("browser-probe", "Provider key is no longer listed")
            return identity
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
        if _legacy_resend_api(connection):
            return await self.resend.test_credential(connection, value)
        if connection.interface is not ConnectionInterface.BROWSER:
            return await super().test_credential(connection, value)
        if _legacy_resend_browser(connection):
            return await self.resend_browser.test_credential(connection, value)
        config = (await self.catalog.definition(connection)).definition.verifier
        if config is None:
            raise ResourceConflictError("Configure the browser credential verifier")
        status, metadata = await self.probe.check(config, value)
        if config.mode == "creation-receipt" and metadata.get("status") == "active":
            return 200
        return status

    async def auth_fingerprint(self, connection: Connection) -> str:
        if connection.interface is not ConnectionInterface.BROWSER:
            return await super().auth_fingerprint(connection)
        return hashlib.sha256(
            json.dumps(await self.catalog.state(connection), sort_keys=True).encode()
        ).hexdigest()

    async def create_credential(
        self, connection: Connection, name: str, scopes: frozenset[str]
    ) -> ConnectorResponse:
        if _legacy_resend_api(connection):
            return await self.resend.create_credential(connection, name, scopes)
        result = await super().create_credential(connection, name, scopes)
        if _api_receipt_mode(connection) and result.secret:
            await self._remember_api_creation(connection, result)
        return result

    async def execute(
        self, tool: str, payload: dict[str, Any], context: ConnectorContext
    ) -> ConnectorResponse:
        connection = context.connection
        if _legacy_resend_api(connection):
            return await self.resend.execute(tool, payload, context)
        if connection.interface is not ConnectionInterface.BROWSER:
            result = await super().execute(tool, payload, context)
            if (
                tool == "provider.createCredential"
                and _api_receipt_mode(connection)
                and result.secret
            ):
                await self._remember_api_creation(connection, result)
            return result
        if tool != "provider.createCredential" or context.run is None:
            raise ResourceConflictError("Browser provider only accepts a fenced creation stage")
        if _legacy_resend_browser(connection):
            await self.resend_browser.validate(connection)
        capture = await self.browser.perform(connection, context.run, Stage.CREATE)
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
            version = await self.catalog.definition(connection)
            verifier = version.definition.verifier
            if verifier and verifier.mode == "creation-receipt":
                await self.receipts.remember(
                    connection,
                    capture.provider_id,
                    value,
                    {
                        "kind": verifier.kind,
                        "scopes": [],
                        "name": capture.provider_display_name,
                    },
                )
            if await self.identify(connection, value) != capture.provider_id:
                raise ResourceConflictError("Captured key identity does not match the issuer")
        except BaseException:
            value.clear()
            raise
        return ConnectorResponse(
            result={"provider_id": capture.provider_id, "name": capture.provider_display_name},
            secret=value,
        )

    async def revoke_credential(self, connection: Connection, provider_id: str) -> None:
        if _legacy_resend_api(connection):
            return await self.resend.revoke_credential(connection, provider_id)
        if connection.interface is not ConnectionInterface.BROWSER:
            if _api_receipt_mode(connection):
                receipt = await self.receipts.metadata(connection, provider_id)
                if receipt is None:
                    raise ConnectorError("provider-proof", "Only a Kinu-created key can be retired")
            return await super().revoke_credential(connection, provider_id)
        name = None
        if _legacy_resend_browser(connection):
            await self.resend_browser.validate(connection)
        metadata = await self.metadata(connection)
        name = next(
            (item.get("name") for item in metadata if item["provider_id"] == provider_id), None
        )
        if not isinstance(name, str) or not name:
            raise ResourceConflictError("Selected browser key has no verified name")
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

    async def _remember_api_creation(
        self, connection: Connection, response: ConnectorResponse
    ) -> None:
        if response.secret is None:
            return
        provider_id = response.result.get("provider_id")
        if not isinstance(provider_id, str):
            response.secret.clear()
            raise ConnectorError("provider-create", "Provider did not return a credential ID")
        try:
            await self.receipts.remember(
                connection,
                provider_id,
                response.secret,
                {
                    "kind": "api-key",
                    "scopes": response.result.get("scopes", []),
                    "name": response.result.get("name"),
                },
            )
        except BaseException:
            response.secret.clear()
            raise


def _legacy_resend_api(connection: Connection) -> bool:
    return (
        connection.platform == "resend"
        and connection.interface is ConnectionInterface.API
        and connection.api_playbook_id is None
    )


def _legacy_resend_browser(connection: Connection) -> bool:
    return (
        connection.platform == "resend"
        and connection.interface is ConnectionInterface.BROWSER
        and connection.verification_connection_id is not None
    )


def _api_receipt_mode(connection: Connection) -> bool:
    return (
        connection.interface is ConnectionInterface.API
        and connection.api_playbook_id is not None
        and connection.http is not None
        and connection.http.credential_identity_mode == "creation-receipt"
    )
