from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from core.auth import AuthenticatedIdentity
from core.inventory.store import InventoryStore
from core.storage.database import Database
from core.storage.runs import RunStore
from hosting.proof import Proof
from ingestion.runtime import RuntimeInspector
from ingestion.worker import Monitor
from rotation.queue import Dispatcher, TaskQueue
from tests.storage.hosting.rotation.fixture import Platform
from tests.storage.hosting.rotation.test import setup

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("expiry", [True, False])
async def test_worker_detects_live_metadata_and_dispatches_only_enabled_automatic_triggers(
    database: Database, identity: AuthenticatedIdentity, expiry: bool
) -> None:
    platform = Platform()
    expires = datetime.now(UTC) + timedelta(hours=1)
    changed = False

    def response(request: httpx.Request) -> httpx.Response:
        result = platform.response(request)
        if changed and request.url.host == "issuer.example" and request.url.path == "/api_keys":
            assert request.method == "GET"
            data = result.json()
            for item in data["result"]:
                item["expires_at"] = expires.isoformat() if expiry else None
                item["scopes"] = ["read"] if expiry else ["read", "admin"]
            return httpx.Response(200, json=data)
        return result

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        changed = True
        org = credential.organisation_id
        inventory = InventoryStore(database)
        connection = await inventory.get_connection(org, credential.connection_id)
        assert connection.http
        operation = connection.http.list_credentials
        await inventory.replace_connection(
            connection.model_copy(
                update={
                    "http": connection.http.model_copy(
                        update={
                            "list_credentials": operation.model_copy(
                                update={
                                    "metadata_fields": operation.metadata_fields
                                    | {"expires_at": "expires_at"}
                                }
                            )
                        }
                    ),
                    "revision": connection.revision + 1,
                }
            ),
            connection.revision,
        )
        jobs = Mock(spec=TaskQueue)
        jobs.rotation = AsyncMock()
        monitor = Monitor(
            database,
            provider,
            RuntimeInspector(database, hosts, versions, Proof(http)),
            Dispatcher(database, jobs),
            "global",
        )
        await monitor.poll()
        observed = await inventory.get_credential(org, credential.id)
        assert observed.last_observed_at and observed.revision == credential.revision + 1
        incidents = (
            await database.collection("inventory")
            .find({"organisation_id": org, "kind": "incident"})
            .to_list()
        )
        expected = "credential-expiring" if expiry else "credential-provider-drift"
        assert {row["data"]["kind"] for row in incidents} == {expected}
        runs = await RunStore(database).list_runs(org, 100)
        assert len(runs) == (1 if expiry else 0)
        if expiry:
            jobs.rotation.assert_awaited_once_with(org, runs[0].id)
            assert observed.rotation_due_at and observed.rotation_due_at < datetime.now(UTC)
        else:
            jobs.rotation.assert_not_awaited()
        await database.collection("monitoring").update_one(
            {"_id": org}, {"$set": {"due": datetime.now(UTC) - timedelta(seconds=1)}}
        )
        await monitor.poll()
        assert (
            await database.collection("inventory").count_documents(
                {"organisation_id": org, "kind": "incident"}
            )
            == 1
        )
        assert len(await RunStore(database).list_runs(org, 100)) == len(runs)
        if expiry:
            assert (
                await inventory.get_credential(org, credential.id)
            ).revision == observed.revision
