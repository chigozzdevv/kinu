from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from contracts import (
    Confidence,
    ConsumerBinding,
    Incident,
    IncidentStatus,
    Severity,
    SourceResource,
)
from core.auth import AuthenticatedIdentity
from core.errors import ResourceConflictError
from core.inventory.store import InventoryStore
from core.storage.database import Database
from core.storage.records import Records
from core.storage.runs import RunStore
from hosting.proof import Proof
from ingestion.runtime import RuntimeInspector
from ingestion.worker import Monitor
from rotation.queue import Dispatcher, TaskQueue
from tests.storage.hosting.rotation.fixture import Platform
from tests.storage.hosting.rotation.test import setup

pytestmark = pytest.mark.anyio


async def test_fresh_metadata_resolves_only_stale_alerts_for_the_observed_generation(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        org = credential.organisation_id
        inventory = InventoryStore(database)
        now = datetime.now(UTC)
        credential = await inventory.replace_credential(
            credential.model_copy(
                update={
                    "last_observed_at": now - timedelta(days=3),
                    "revision": credential.revision + 1,
                }
            ),
            credential.revision,
        )
        variants = [
            (
                "stale",
                "credential-metadata-stale",
                credential.active_generation_id,
                IncidentStatus.ACTION,
            ),
            (
                "exposure",
                "credential-exposure-detected",
                credential.active_generation_id,
                IncidentStatus.ACTION,
            ),
            ("old", "credential-metadata-stale", "generation_other", IncidentStatus.ACTION),
            (
                "rotating",
                "credential-metadata-stale",
                credential.active_generation_id,
                IncidentStatus.ROTATING,
            ),
            (
                "future",
                "credential-metadata-stale",
                credential.active_generation_id,
                IncidentStatus.ACTION,
            ),
        ]
        async with Records(database).write(org) as rows:
            for suffix, kind, generation_id, status in variants:
                created = (
                    now + timedelta(days=1) if suffix == "future" else now - timedelta(minutes=1)
                )
                await rows.add(
                    Incident(
                        id=f"incident_{suffix}",
                        organisation_id=org,
                        event_id=f"event_{suffix}",
                        source="detection",
                        source_event_id=f"observation_{suffix}",
                        kind=kind,
                        severity=Severity.HIGH,
                        confidence=Confidence.VERIFIED,
                        status=status,
                        resource=SourceResource(
                            credential_id=credential.id, generation_id=generation_id
                        ),
                        credential_id=credential.id,
                        created_at=created,
                        updated_at=created,
                    ),
                    org,
                )
        with pytest.raises(ResourceConflictError):
            await inventory.record_observation(
                org,
                credential.id,
                credential.active_generation_id or "",
                credential.revision - 1,
                now,
                None,
                None,
                "a" * 64,
            )
        async with Records(database).read() as rows:
            assert (await rows.get(Incident, org, "incident_stale")).status is IncidentStatus.ACTION
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
        assert observed.last_observed_at and observed.last_observed_at >= now
        async with Records(database).read() as rows:
            resolved = await rows.get(Incident, org, "incident_stale")
            assert resolved.status is IncidentStatus.RESOLVED
            assert resolved.resolution_observed_at == observed.last_observed_at
            for suffix, _, _, status in variants[1:]:
                assert (await rows.get(Incident, org, f"incident_{suffix}")).status is status
        with pytest.raises(ValueError, match="verified observation"):
            Incident.model_validate(
                resolved.model_dump() | {"kind": "credential-exposure-detected"}
            )
        assert len(
            await database.collection("inventory")
            .find({"organisation_id": org, "kind": "incident"})
            .to_list()
        ) == len(variants)
        jobs.rotation.assert_not_awaited()


async def test_runtime_drift_resolves_only_after_all_consumers_recover(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        org = credential.organisation_id
        now = datetime.now(UTC)
        variants = [
            ("exposure", "credential-exposure-detected", credential.active_generation_id),
            ("old", "credential-runtime-drift", "generation_other"),
            ("future", "credential-runtime-drift", credential.active_generation_id),
            ("rotating", "credential-runtime-drift", credential.active_generation_id),
            ("dismissed", "credential-runtime-drift", credential.active_generation_id),
        ]
        async with Records(database).write(org) as rows:
            for suffix, kind, generation_id in variants:
                status = {
                    "rotating": IncidentStatus.ROTATING,
                    "dismissed": IncidentStatus.DISMISSED,
                }.get(suffix, IncidentStatus.ACTION)
                created = now + timedelta(days=1) if suffix == "future" else now
                await rows.add(
                    Incident(
                        id=f"incident_{suffix}",
                        organisation_id=org,
                        event_id=f"event_{suffix}",
                        source="detection",
                        source_event_id=f"observation_{suffix}",
                        kind=kind,
                        severity=Severity.HIGH,
                        confidence=Confidence.VERIFIED,
                        status=status,
                        resource=SourceResource(
                            credential_id=credential.id, generation_id=generation_id
                        ),
                        credential_id=credential.id,
                        dismissal_reason="Reviewed" if suffix == "dismissed" else None,
                        created_at=created,
                        updated_at=created,
                    ),
                    org,
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
        platform.loaded = dict.fromkeys(platform.loaded, "unrecognised-runtime-value")
        await monitor.poll()
        detected = (
            await database.collection("inventory")
            .find(
                {
                    "organisation_id": org,
                    "kind": "incident",
                    "data.kind": "credential-runtime-drift",
                    "id": {"$nin": [f"incident_{suffix}" for suffix, _, _ in variants]},
                }
            )
            .to_list()
        )
        assert len(detected) == 2
        for project in ("project-one", "project-two"):
            platform.loaded[project] = platform.variables[project]
            await database.collection("monitoring").update_one(
                {"_id": org}, {"$set": {"due": datetime.now(UTC) - timedelta(seconds=1)}}
            )
            await monitor.poll()
            async with Records(database).read() as rows:
                for row in detected:
                    incident = await rows.get(Incident, org, row["id"])
                    if project == "project-one":
                        assert incident.status is IncidentStatus.ACTION
                        assert incident.resolution_observed_at is None
                    else:
                        assert incident.status is IncidentStatus.RESOLVED
                        assert incident.resolution_observed_at
                        assert incident.resolution_observed_at > incident.created_at
                        assert incident.resolution_run_id is None
                        assert incident.run_id is None
        async with Records(database).read() as rows:
            for suffix, _, _ in variants:
                incident = await rows.get(Incident, org, f"incident_{suffix}")
                expected = {
                    "rotating": IncidentStatus.ROTATING,
                    "dismissed": IncidentStatus.DISMISSED,
                }.get(suffix, IncidentStatus.ACTION)
                assert incident.status is expected
                assert incident.resolution_observed_at is None
        platform.loaded["project-one"] = "unrecognised-runtime-value"
        await database.collection("monitoring").update_one(
            {"_id": org}, {"$set": {"due": datetime.now(UTC) - timedelta(seconds=1)}}
        )
        await monitor.poll()
        async with Records(database).read() as rows:
            incidents = await rows.list(Incident, org)
            previous = {row["id"] for row in detected}
            existing = previous | {f"incident_{suffix}" for suffix, _, _ in variants}
            recurrent = [incident for incident in incidents if incident.id not in existing]
            assert len(recurrent) == 1
            assert recurrent[0].kind == "credential-runtime-drift"
            assert recurrent[0].status is IncidentStatus.ACTION
            assert recurrent[0].resolution_observed_at is None
            assert all(
                incident.status is IncidentStatus.RESOLVED
                for incident in incidents
                if incident.id in previous
            )
        assert (
            await database.collection("inventory").count_documents(
                {"organisation_id": org, "kind": "incident"}
            )
            == len(variants) + len(detected) + 1
        )
        jobs.rotation.assert_not_awaited()


@pytest.mark.parametrize("changed", ["binding", "generation", "controls", "lock"])
async def test_runtime_recovery_rejects_inventory_changes_during_verification(
    database: Database, identity: AuthenticatedIdentity, changed: str
) -> None:
    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        _, _, _, credential = await setup(database, identity, http)
        inventory = InventoryStore(database)
        org = credential.organisation_id
        bindings = await inventory.bindings(org)
        now = datetime.now(UTC)
        async with Records(database).write(org) as rows:
            await rows.add(
                Incident(
                    id="incident_runtime",
                    organisation_id=org,
                    event_id="event_runtime",
                    source="detection",
                    source_event_id="observation_runtime",
                    kind="credential-runtime-drift",
                    severity=Severity.HIGH,
                    confidence=Confidence.VERIFIED,
                    status=IncidentStatus.ACTION,
                    resource=SourceResource(
                        credential_id=credential.id,
                        generation_id=credential.active_generation_id,
                    ),
                    credential_id=credential.id,
                    created_at=now,
                    updated_at=now,
                ),
                org,
            )
            if changed == "binding":
                binding = await rows.get(ConsumerBinding, org, bindings[0].id)
                await rows.replace(
                    binding.model_copy(
                        update={"required": False, "revision": binding.revision + 1}
                    ),
                    binding.revision,
                )
            elif changed in {"generation", "controls"}:
                field = "active_generation_id" if changed == "generation" else "control_version"
                await rows.replace(
                    credential.model_copy(
                        update={field: "changed_one", "revision": credential.revision + 1}
                    ),
                    credential.revision,
                )
        if changed == "lock":
            await database.collection("run_locks").insert_one(
                {"organisation_id": org, "credential_id": credential.id, "run_id": "run_other"}
            )
        with pytest.raises(ResourceConflictError, match="runtime monitoring"):
            await inventory.record_runtime_observation(credential, bindings, now)
        async with Records(database).read() as rows:
            incident = await rows.get(Incident, org, "incident_runtime")
            assert incident.status is IncidentStatus.ACTION
            assert incident.resolution_observed_at is None


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
