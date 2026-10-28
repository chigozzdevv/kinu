from datetime import UTC, datetime
from typing import Any

import pytest
from agents.decision import Decision, DecisionInput
from connectors.aws.agent import NativeAgent
from contracts import ControlVersion, ExposureSource, IncidentStatus, ManagedCredential
from core.audit.store import AuditStore
from core.errors import ResourceConflictError
from core.incident.store import IncidentStore
from core.storage.database import Database
from core.storage.records import Records
from demo.config import DemoConfig
from mail.assessment import Assessment
from mail.links import save_connection
from mail.models import Mailbox, Sender
from policy import digest
from rotation.queue import Dispatcher
from tests.mail import MemoryMail

pytestmark = pytest.mark.anyio
SENDER = "security@provider.example"


class Agent(NativeAgent):
    def __init__(self) -> None:
        self.inputs: list[DecisionInput] = []

    async def assess(self, organisation_id: str, value: DecisionInput) -> Decision:
        self.inputs.append(value)
        return Decision(decision="rotate", reason="confirmed-exposure")


class Queue(Dispatcher):
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def require_scope(self, organisation_id: str, credential_id: str) -> None:
        if self.fail:
            self.fail = False
            raise ResourceConflictError("temporary worker failure")

    async def submit(self, organisation_id: str, run_id: str) -> None:
        self.sent.append(run_id)


async def setup(
    database: Database, demo: DemoConfig, *, approved: bool
) -> tuple[Mailbox, MemoryMail]:
    box = Mailbox(
        id="gmail_one",
        organisation_id=demo.organisation_id,
        email="owner@gmail.com",
        connected_at=datetime.now(UTC),
        status="ready",
        senders=(Sender(sender=SENDER, provider_connection_id="connection_demo_provider"),),
    )
    await save_connection(database, box)
    store = MemoryMail()
    await store.save(box, "cipher", None)
    async with Records(database).write(demo.organisation_id) as rows:
        credential = await rows.get(ManagedCredential, demo.organisation_id, demo.credential_id)
        original = await rows.get(ControlVersion, demo.organisation_id, credential.control_version)
        definition = original.definition.model_copy(
            update={
                "require_revoke_approval": False,
                "exposure_sources": (ExposureSource(connection_id=box.id, resource=SENDER),)
                if approved
                else (),
            }
        )
        version = original.model_copy(
            update={
                "id": "controls_mail",
                "number": 2,
                "definition": definition,
                "digest": digest(definition),
            }
        )
        await rows.add(version, demo.organisation_id)
        await rows.replace(
            credential.model_copy(
                update={"control_version": version.id, "revision": credential.revision + 1}
            ),
            credential.revision,
        )
    return box, store


@pytest.mark.parametrize(("signed", "approved"), [(True, True), (False, True), (True, False)])
async def test_only_authenticated_mapped_and_approved_alerts_can_rotate(
    database: Database,
    demo: DemoConfig,
    monkeypatch: pytest.MonkeyPatch,
    signed: bool,
    approved: bool,
) -> None:
    async def proof(*args: Any) -> bool:
        return signed

    monkeypatch.setattr("mail.assessment.authenticated", proof)
    box, store = await setup(database, demo, approved=approved)
    agent, queue = Agent(), Queue(fail=signed and approved)
    assessment = Assessment(database, store, agent, queue, demo.region)
    raw = (
        f"From: {SENDER}\r\nSubject: API credential exposed\r\n\r\n"
        f"Key {'a' * 24} was exposed. PRIVATE-BODY-MARKER"
    ).encode()
    data = await assessment.prepare(box, "message_one", raw, box.senders[0], datetime.now(UTC))
    assert "PRIVATE-BODY-MARKER" not in str(data) and "Subject" not in str(data)
    if signed and approved:
        with pytest.raises(ResourceConflictError):
            await assessment.process(box, "message_one", data)
    await assessment.process(box, "message_one", data)
    await assessment.process(box, "message_one", data)
    incidents = await IncidentStore(database).list_incidents(demo.organisation_id, 10)
    assert len(incidents) == 1 and data["done"]
    events = await AuditStore(database).list_events(demo.organisation_id, 100)
    if signed and approved:
        assert len(agent.inputs) == len(queue.sent) == 1
        assert incidents[0].status is IncidentStatus.ROTATING
        assert set(agent.inputs[0].model_dump()) == {
            "stage",
            "kind",
            "confidence",
            "candidate_count",
            "policy_allows_rotation",
        }
        assert any(item.kind == "incident.ai-assessed" for item in events)
    else:
        assert not agent.inputs and not queue.sent
        assert incidents[0].status is IncidentStatus.ACTION
        assert any(item.kind == "incident.review-required" for item in events)
