import asyncio
from datetime import UTC, datetime

import pytest
from contracts import Confidence, IncidentStatus, IngestionEvent, Severity, SourceResource
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
