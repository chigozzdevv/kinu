from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
from browser.catalog import Catalog
from browser.worker import Worker
from connectors.base import SecretValue
from connectors.base.errors import ConnectorError
from connectors.resend.book import procedure
from connectors.resend.browser import BrowserResend
from connectors.resend.client import Resend
from contracts import Connection, PlaybookDraft, PlaybookState, PlaybookVersion
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AuthenticatedIdentity
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.inventory.store import InventoryStore
from core.storage.database import Database
from core.storage.records import Records
from executor.authorization import Authorization
from executor.receipts import Receipts
from executor.vault import Vault
from policy import digest
from pydantic import SecretStr
from tests.storage.resend.browser.rotation import check_rotation
from tests.storage.resend.test import ID, KEY, connection

pytestmark = pytest.mark.anyio


async def test_resend_procedure_rotates_through_real_chromium_without_api_mutations(
    database: Database,
    identity: AuthenticatedIdentity,
) -> None:
    await check_rotation(database, identity)


async def test_browser_receipts_verify_with_read_only_calls_and_preserve_account_boundaries(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    identity: AuthenticatedIdentity,
) -> None:
    source, now = connection(), datetime.now(UTC)
    workspace = await AccountService(
        AccountStore(database, lambda: now), lambda: now
    ).create_organisation(identity, "Browser receipts")
    source = source.model_copy(update={"organisation_id": workspace.organisation.id})
    vault = Vault(database, SecretStr("private-browser-receipt-test-key-at-least-32-characters"))
    await vault.initialize()
    receipts, inventory = Receipts(vault), InventoryStore(database)
    await receipts.initialize()
    await vault.put(source.organisation_id, source.id, {"token": "management-fixture"})
    await inventory.add_connection(source)
    definition = procedure(ID, "issuer.example")
    book = PlaybookVersion(
        id="version_browser",
        organisation_id=source.organisation_id,
        playbook_id="book_browser",
        number=1,
        definition=definition,
        digest=digest(definition),
        state=PlaybookState.PUBLISHED,
        published_by="actor_one",
        published_at=now,
        created_by="actor_one",
        created_at=now,
    )
    async with Records(database).write(source.organisation_id) as rows:
        await rows.add(book, source.organisation_id)
    browser = Connection.model_validate(
        {
            **source.model_dump(),
            "id": "provider_browser",
            "interface": "browser",
            "http": None,
            "authorization": "browser-session",
            "authorization_reference": None,
            "status": "setup-required",
            "playbook_id": book.playbook_id,
            "playbook_version_id": book.id,
            "allowed_resources": definition.allowed_domains,
            "verification_connection_id": source.id,
        }
    )
    await inventory.add_connection(browser)
    listed = [ID]

    async def ids(_: Connection) -> tuple[str, ...]:
        return tuple(listed)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path == "/inboxes":
            return httpx.Response(
                401,
                json={
                    "name": "restricted_api_key",
                    "message": "This API key is restricted to only send emails",
                },
            )
        if request.url.path == "/domains":
            return httpx.Response(
                200,
                json={
                    "has_more": False,
                    "data": [{"id": ID, "name": "issuer.example", "status": "verified"}],
                },
            )
        return httpx.Response(
            200, json={"has_more": False, "data": [{"id": ID, "name": "Sandbox"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        adapter = BrowserResend(
            Catalog(database, vault),
            cast(Worker, SimpleNamespace(ids=ids)),
            Resend(Authorization(vault), receipts, http),
            receipts,
        )
        await adapter.validate(browser)
        with SecretValue(KEY.encode()) as value:
            with pytest.raises(ResourceNotFoundError):
                await adapter.identify(browser, value)
            await receipts.remember(
                source, ID, value, {"kind": "api-key", "scopes": ["sending_access", "domain:" + ID]}
            )
            assert await adapter.identify(browser, value) == ID
            await receipts.collection.delete_one(
                {"organisation_id": source.organisation_id, "connection_id": source.id}
            )
            assert await receipts.metadata(source, ID) is None
            await adapter.remember(browser, ID, value)
            assert await receipts.metadata(source, ID) is not None
            assert await adapter.identify(browser, value) == ID
            with pytest.raises(ConnectorError, match="linked API account"):
                await adapter.remember(browser, "foreign-account-key", value)
            assert await receipts.metadata(source, "foreign-account-key") is None
            assert await adapter.test_credential(browser, value) == 200
            listed.append("foreign-account-key")
            with pytest.raises(ConnectorError, match="accounts differ"):
                await adapter.identify(browser, value)
            listed.pop()
            with pytest.raises(ResourceNotFoundError):
                await adapter.identify(
                    browser.model_copy(update={"organisation_id": "other"}), value
                )
        changed = definition.model_dump(mode="json")
        changed["steps"][3]["parameters"]["value"] = "Full access"
        altered = PlaybookDraft.model_validate(changed)
        monkeypatch.setattr(
            adapter.catalog,
            "definition",
            AsyncMock(
                return_value=book.model_copy(
                    update={"definition": altered, "digest": digest(altered)}
                )
            ),
        )
        with pytest.raises(ResourceConflictError, match="reviewed"):
            await adapter.validate(browser)
        with pytest.raises(ResourceConflictError, match="archived"):
            await inventory.replace_connection(
                source.model_copy(update={"archived_at": now, "revision": 1}), 0
            )
        assert KEY not in str(await database.collection("provider_receipts").find({}).to_list())
