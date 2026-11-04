import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from contracts import Confidence, Incident, IncidentStatus, IngestionEvent, Severity, SourceResource
from core.account import AccountService
from core.account.store import AccountStore
from core.audit.chain import GENESIS, event_hash
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.auth import AuthenticatedIdentity
from core.errors import IdempotencyConflictError, ResourceNotFoundError
from core.incident.service import IncidentService
from core.incident.store import IncidentStore
from core.storage.database import Database

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 27, tzinfo=UTC)


async def test_open_incidents_are_filtered_before_history_limits_and_page_without_loss(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    workspace = await AccountService(
        AccountStore(database, lambda: NOW), lambda: NOW
    ).create_organisation(identity, "Incident status")
    org = workspace.organisation.id
    records = []
    expected = set()
    for index in range(1002):
        opened = index < 501
        identity_id = f"incident_{index:06d}"
        if opened:
            expected.add(identity_id)
        item = Incident(
            id=identity_id,
            organisation_id=org,
            event_id=f"event_{index:06d}",
            source="test",
            source_event_id=f"source_{index:06d}",
            severity=Severity.HIGH,
            confidence=Confidence.VERIFIED,
            status=IncidentStatus.ACTION if opened else IncidentStatus.DISMISSED,
            resource=SourceResource(),
            dismissal_reason=None if opened else "Test incident",
            created_at=NOW + timedelta(seconds=index),
            updated_at=NOW + timedelta(seconds=index),
        )
        records.append(
            {
                "id": item.id,
                "organisation_id": org,
                "kind": "incident",
                "revision": 0,
                "data": item.model_dump(mode="json"),
            }
        )
    await database.collection("inventory").insert_many(records)
    service = IncidentService(IncidentStore(database), lambda: NOW)
    seen: list[str] = []
    before = None
    while page := await service.list_incidents(
        org, frozenset({IncidentStatus.ACTION}), 250, before
    ):
        seen.extend(item.id for item in page)
        before = page[-1].id
    assert len(seen) == len(set(seen)) == 501
    assert set(seen) == expected
    assert len(await service.list_incidents(org, limit=100)) == 100
    assert await service.list_incidents("org_other", frozenset({IncidentStatus.ACTION})) == ()
    with pytest.raises(ResourceNotFoundError):
        await service.list_incidents("org_other", before=seen[0])


async def test_incident_and_audit_retries_remain_durable_and_tenant_scoped(
    database: Database, identity: AuthenticatedIdentity
) -> None:
    workspace = await AccountService(
        AccountStore(database, lambda: NOW), lambda: NOW
    ).create_organisation(identity, "Incident intake")
    org = workspace.organisation.id
    service = IncidentService(IncidentStore(database), lambda: NOW)
    event = IngestionEvent(
        id="email_one",
        organisation_id=org,
        source="email",
        source_event_id="message_one",
        kind="credential-exposure-detected",
        observed_at=NOW,
        severity=Severity.HIGH,
        confidence=Confidence.MEDIUM,
        resource=SourceResource(provider="demo"),
        source_reference="kinu://mail/message_one",
        received_at=NOW,
    )
    results = await asyncio.gather(
        *(service.ingest(f"incident_{index}", event) for index in range(3))
    )
    assert sum(applied for _, applied in results) == 1
    assert len({incident.id for incident, _ in results}) == 1
    incident = results[0][0]
    assert incident.status is IncidentStatus.NEW
    with pytest.raises(IdempotencyConflictError):
        await service.ingest(
            "incident_conflict", event.model_copy(update={"severity": Severity.CRITICAL})
        )
    with pytest.raises(ResourceNotFoundError):
        await service.get("org_other", incident.id)
    log = AuditStore(database)
    writer = AuditWriter(log, "eu-west-2", lambda: NOW)
    await asyncio.gather(
        *(
            writer.append(
                f"audit_{index}",
                org,
                "incident.received",
                "operator_one",
                f"incidents/{incident.id}",
                {"source": "email", "password": "sensitive"},
            )
            for index in range(4)
        )
    )
    events = sorted(await AuditStore(database).list_events(org, 10), key=lambda item: item.sequence)
    assert [item.sequence for item in events] == list(range(4))
    previous = GENESIS
    for item in events:
        assert "sensitive" not in str(item.payload)
        assert item.previous_hash == previous
        assert item.event_hash == event_hash(
            org,
            item.sequence,
            item.kind,
            item.actor_id,
            item.resource,
            item.run_id,
            item.payload,
            item.evidence_ids,
            previous,
            item.occurred_at,
            item.region,
        )
        previous = item.event_hash
    assert await AuditStore(database).list_events("org_other", 10) == ()
