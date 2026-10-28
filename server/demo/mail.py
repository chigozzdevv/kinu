from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Literal

from agents.decision import Decision, IncidentInput
from connectors.aws import AwsApi
from connectors.aws.agent import NativeAgent
from contracts import Contract, Identifier, IncidentStatus
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.incident.service import IncidentService
from core.incident.store import IncidentStore
from core.inventory.store import InventoryStore
from core.storage.database import Database
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from ingestion.mail import CredentialHint, EmailSource, MailRule, MailTrust
from pydantic import AwareDatetime, Field
from rotation.stages import stable

from demo.config import DemoConfig
from demo.queue import Queue


class MailTask(Contract):
    kind: Literal["email"] = "email"
    id: Identifier
    organisation_id: Identifier
    actor_id: Identifier
    provider_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    received_at: AwareDatetime


class Source:
    def __init__(self, database: Database, queue: Queue) -> None:
        self.database, self.queue = database, queue

    async def alert(self, organisation_id: str, actor_id: str, task_id: str) -> MailTask:
        self.queue.require_scope(organisation_id, "credential_demo")
        credential = await InventoryStore(self.database).get_credential(
            organisation_id, "credential_demo"
        )
        task = await Mailbox(self.database).put(
            MailTask(
                id=task_id,
                organisation_id=organisation_id,
                actor_id=actor_id,
                provider_id=credential.provider_id or "",
                received_at=datetime.now(UTC),
            )
        )
        await self.queue.submit_mail(task)
        return task


class Mailbox:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def put(self, task: MailTask) -> MailTask:
        async with self.database.transaction(task.organisation_id) as session:
            await self.database.collection("mail").update_one(
                {"organisation_id": task.organisation_id, "id": task.id},
                {
                    "$setOnInsert": {
                        "data": task.model_dump(mode="json"),
                        "done": False,
                        "assessment": None,
                    }
                },
                upsert=True,
                session=session,
            )
        stored, _ = await self.get(task.organisation_id, task.id)
        return stored

    async def get(self, org: str, task_id: str) -> tuple[MailTask, bool]:
        row = await self.database.collection("mail").find_one(
            {"organisation_id": org, "id": task_id}
        )
        if not row:
            raise ResourceNotFoundError("demo email fixture is unavailable")
        return MailTask.model_validate(row["data"]), bool(row["done"])

    async def complete(self, task: MailTask) -> None:
        await self.database.collection("mail").update_one(
            {"organisation_id": task.organisation_id, "id": task.id},
            {"$set": {"done": True}},
        )

    async def decision(self, task: MailTask, value: Decision | None = None) -> Decision | None:
        query = {"organisation_id": task.organisation_id, "id": task.id}
        if value is not None:
            await self.database.collection("mail").update_one(
                {**query, "assessment": None},
                {"$set": {"assessment": value.model_dump(mode="json")}},
            )
        row = await self.database.collection("mail").find_one(query)
        return Decision.model_validate(row["assessment"]) if row and row["assessment"] else None


async def investigate(
    database: Database, aws: AwsApi, config: DemoConfig, task: MailTask
) -> str | None:
    stored, done = await Mailbox(database).get(task.organisation_id, task.id)
    if stored != task or task.organisation_id != config.organisation_id:
        raise ResourceConflictError("email job differs from the saved fixture")
    if done:
        return None
    org, now = task.organisation_id, task.received_at
    inventory = InventoryStore(database)
    credentials = await inventory.credentials(org)
    rule = MailRule(
        id="mail_demo",
        organisation_id=org,
        sender="security@demo.kinu.invalid",
        provider="aws",
        connection_id="connection_demo_mail",
        created_at=now,
    )
    message = EmailMessage()
    message["From"], message["To"] = rule.sender, "alerts@demo.kinu.invalid"
    message["Subject"], message["Message-ID"] = (
        "Demo security alert: exposed credential",
        f"<{task.id}@demo.kinu.invalid>",
    )
    message["Date"] = format_datetime(now)
    message.set_content(
        f"DEMO FIXTURE. Credential {task.provider_id} was exposed. Rotate this credential."
    )
    signal = EmailSource().normalise(
        rule,
        message.as_bytes(),
        now,
        tuple(
            CredentialHint(credential_id=item.id, provider_id=item.provider_id)
            for item in credentials
            if item.provider_id
        ),
        MailTrust.DEMO_FIXTURE,
    )

    def clock() -> datetime:
        return datetime.now(UTC)

    service = IncidentService(
        IncidentStore(database),
        clock,
        inventory,
        RunWorkflow(RunStore(database)),
        audit=AuditWriter(AuditStore(database), config.region, clock),
    )
    incident, _ = await service.ingest(stable("incident", task.id), signal.event)
    if incident.run_id:
        return incident.run_id
    if incident.credential_id is None or incident.status is not IncidentStatus.ACTION:
        await Mailbox(database).complete(task)
        return None
    credential = next(item for item in credentials if item.id == incident.credential_id)
    if credential.id != config.credential_id or credential.provider_id != task.provider_id:
        await Mailbox(database).complete(task)
        return None
    controls = await inventory.get_control_version(org, credential.id, credential.control_version)
    allowed = signal.event.kind in controls.definition.automatic_triggers and any(
        source.connection_id == rule.connection_id and source.resource == rule.sender
        for source in controls.definition.exposure_sources
    )
    decision = await Mailbox(database).decision(task)
    if decision is None:
        decision = await NativeAgent(aws, config.region, config.assessment_function).assess(
            org,
            IncidentInput.model_validate(
                {
                    "stage": "investigate",
                    "kind": signal.event.kind,
                    "confidence": signal.event.confidence.value,
                    "candidate_count": len(incident.candidates),
                    "policy_allows_rotation": allowed,
                }
            ),
        )
        decision = await Mailbox(database).decision(task, decision)
    if decision is None:
        raise ResourceConflictError("AI assessment was not saved")
    await AuditWriter(AuditStore(database), config.region, clock).append(
        stable("assessment", task.id),
        org,
        "incident.ai-assessed",
        "worker_one",
        f"incidents/{incident.id}",
        {"decision": decision.decision, "reason": decision.reason, "source": "demo-email"},
        occurred_at=now,
    )
    if decision.decision != "rotate" or not allowed:
        await Mailbox(database).complete(task)
        return None
    current = await inventory.get_credential(org, credential.id)
    if current.provider_id != task.provider_id or current.control_version != controls.id:
        await Mailbox(database).complete(task)
        return None
    _, run, _ = await service.start_rotation(
        org,
        incident.id,
        stable("command", task.id),
        task.actor_id,
        controls.id,
        "Verified demo email exposure",
        "urgent",
        now,
    )
    return run.id
