import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from typing import TYPE_CHECKING, Any, Literal

import httpx
from contracts import (
    Confidence,
    ConnectionStatus,
    Contract,
    Identifier,
    Incident,
    IngestionEvent,
    ManagedCredential,
    Severity,
    SourceResource,
)
from core.account.store import AccountStore
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.auth import AccessControl, AuthenticatedIdentity, Permission
from core.errors import KinuError, ResourceConflictError, ResourceNotFoundError
from core.inventory.store import InventoryStore
from emailer.templates import message
from ingestion.mail import MailRule
from pydantic import Field, field_validator, model_validator
from pymongo import ReturnDocument

from mail.forwarding import Forwarding
from mail.models import ForwardInbox

if TYPE_CHECKING:
    from executor.runtime import Runtime

IncidentKind = Literal["exposure", "expiry", "drift"]
KINDS = {
    "exposure": ("credential-exposure-detected", "Key exposed", Severity.HIGH),
    "expiry": ("credential-expiring", "Near expiry", Severity.MEDIUM),
    "drift": ("credential-provider-drift", "Unexpected key change", Severity.MEDIUM),
}
REFERENCE = re.compile(r"Kinu test reference: (report_[a-f0-9]{40})\.([A-Za-z0-9_=-]{32,4096})")


class ReportRequest(Contract):
    credential_id: Identifier
    kind: IncidentKind
    details: str = Field(default="", max_length=1024)
    simulate: bool = False
    source_connection_id: Identifier | None = None
    recipient: str | None = Field(default=None, max_length=254)

    @field_validator("recipient")
    @classmethod
    def address(cls, value: str | None) -> str | None:
        return MailRule.exact_sender(value.strip()) if value else None

    @model_validator(mode="after")
    def simulation_fields(self) -> "ReportRequest":
        if self.simulate and (not self.source_connection_id or not self.recipient):
            raise ValueError("Select an Email connection and a test recipient")
        if not self.simulate and (self.source_connection_id or self.recipient):
            raise ValueError("Delivery settings are only used for test alerts")
        return self


class ReportSource(Contract):
    id: Identifier
    name: str
    address: str
    linked: bool


class ReportOptions(Contract):
    email_sender: str
    recipient: str | None = None
    sources: tuple[ReportSource, ...] = ()
    subject: str = ""
    preview: str = ""


class ReportStatus(Contract):
    id: Identifier
    simulate: bool
    state: str
    recipient: str | None = None
    message: str | None = None
    incident: Incident | None = None


def now() -> datetime:
    value = datetime.now(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


class Reports:
    def __init__(self, service: "Runtime", forwarding: Forwarding) -> None:
        self.service, self.forwarding = service, forwarding
        self.database = service.database
        self.inventory = InventoryStore(self.database)
        self.audit = AuditWriter(AuditStore(self.database), service.config.aws_region, now)

    async def options(
        self, org: str, credential_id: str | None = None, kind: IncidentKind = "exposure"
    ) -> ReportOptions:
        sender = parseaddr(self.service.config.email_sender)[1]
        if not credential_id:
            return ReportOptions(email_sender=sender)
        credential = await self.credential(org, credential_id)
        controls = await self.inventory.get_control_version(
            org, credential.id, credential.control_version
        )
        inbox = await self.forwarding.get(org)
        sources = []
        if inbox and self.service.config.email_receiving_ready:
            connection = await self.inventory.get_connection(org, inbox.id)
            if (
                not connection.archived_at
                and connection.status is ConnectionStatus.READY
                and "incident" in connection.roles
                and "incident.receiveEmail" in connection.capabilities
                and inbox.address in connection.allowed_resources
            ):
                sources.append(
                    ReportSource(
                        id=inbox.id,
                        name=connection.display_name,
                        address=inbox.address,
                        linked=any(
                            item.connection_id == inbox.id and item.resource == inbox.address
                            for item in controls.definition.exposure_sources
                        ),
                    )
                )
        services = await self.inventory.services(org)
        apps = ", ".join(
            item.display_name for item in services if item.id in credential.consumer_ids
        )
        subject = f"Kinu test alert: {credential.display_name} — {KINDS[kind][1]}"
        preview = "\n".join(
            (
                f"Credential: {credential.display_name}",
                f"Provider: {credential.provider}",
                f"Key ID: {credential.provider_id or 'Not available'}",
                f"Apps: {apps or 'Not mapped'}",
                f"Issue: {KINDS[kind][1]}",
                "This is a test incident.",
            )
        )
        return ReportOptions(
            email_sender=sender, sources=tuple(sources), subject=subject, preview=preview
        )

    async def credential(self, org: str, identity: str) -> ManagedCredential:
        credential = await self.inventory.get_credential(org, identity)
        if credential.archived_at or not credential.active_generation_id:
            raise ResourceConflictError("Select an active managed credential")
        return credential

    async def authorized(self, org: str, subject: str) -> AuthenticatedIdentity:
        identity = AuthenticatedIdentity(subject=subject, issuer="local")
        access = AccessControl(AccountStore(self.database, now))
        for permission in (Permission.INCIDENT_WRITE, Permission.RUN_WRITE):
            await access.require(identity, org, permission)
        return identity

    async def create(
        self, org: str, subject: str, command: str, request: ReportRequest
    ) -> ReportStatus:
        actor = await self.authorized(org, subject)
        identity = "report_" + hashlib.sha256(command.encode()).hexdigest()[:40]
        fingerprint = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        records = self.database.collection("mail")
        previous = await records.find_one({"organisation_id": org, "id": identity})
        if previous:
            if previous.get("fingerprint") != fingerprint or previous.get("subject") != subject:
                raise ResourceConflictError("This incident request was already used")
            if not request.simulate and previous["state"] != "completed":
                await self.apply(previous)
            return await self.status(org, identity)
        credential = await self.credential(org, request.credential_id)
        options = await self.options(org, credential.id, request.kind)
        source = next(
            (item for item in options.sources if item.id == request.source_connection_id), None
        )
        if request.simulate and (not source or not self.service.config.resend_key):
            raise ResourceConflictError("Connect an Email source before sending a test alert")
        created = now()
        event = IngestionEvent(
            id="event_" + identity.removeprefix("report_"),
            organisation_id=org,
            source="test-email" if request.simulate else "manual",
            source_event_id=identity,
            kind=KINDS[request.kind][0],
            description=request.details.strip() or None,
            observed_at=created,
            received_at=created,
            severity=KINDS[request.kind][2],
            confidence=Confidence.VERIFIED,
            resource=SourceResource(
                credential_id=credential.id,
                generation_id=credential.active_generation_id,
                provider=credential.provider,
                connection_id=source.id if source else None,
            ),
            source_reference=f"kinu://incident-reports/{identity}",
        )
        record: dict[str, Any] = {
            "organisation_id": org,
            "id": identity,
            "type": "incident-report",
            "subject": subject,
            "actor_id": actor.actor_id,
            "fingerprint": fingerprint,
            "simulate": request.simulate,
            "kind": request.kind,
            "credential_id": credential.id,
            "generation_id": credential.active_generation_id,
            "control_version": credential.control_version,
            "source_id": source.id if source else None,
            "address": source.address if source else None,
            "recipient": request.recipient,
            "event": event.model_dump(mode="json"),
            "created_at": created,
            "expires_at": created + timedelta(minutes=30),
            "state": "queued",
            "attempts": 0,
            "available_at": created,
            "lease_until": created,
            "email_subject": options.subject,
            "preview": options.preview,
            "received_email_id": None,
        }
        if request.simulate:
            record["ticket"] = self.service.vault.seal(
                {"id": identity, "fingerprint": fingerprint}, f"incident-report:{org}:{identity}"
            )
        async with self.database.transaction(org) as session:
            previous = await records.find_one(
                {"organisation_id": org, "id": identity}, session=session
            )
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ResourceConflictError("This incident request was already used")
                record = previous
            else:
                if (
                    request.simulate
                    and await records.count_documents(
                        {
                            "organisation_id": org,
                            "type": "incident-report",
                            "simulate": True,
                            "created_at": {"$gte": created - timedelta(minutes=10)},
                        },
                        session=session,
                    )
                    >= 5
                ):
                    raise ResourceConflictError(
                        "Wait a few minutes before sending another test alert"
                    )
                await records.insert_one(record, session=session)
        await self.audit_request(record)
        if not request.simulate:
            await self.apply(record)
        return await self.status(org, identity)

    async def validate(self, record: dict[str, Any]) -> None:
        org = record["organisation_id"]
        await self.authorized(org, record["subject"])
        credential = await self.credential(org, record["credential_id"])
        if (
            credential.active_generation_id != record["generation_id"]
            or credential.control_version != record["control_version"]
        ):
            raise ResourceConflictError("The key or its controls changed. Create a new incident.")
        if record["simulate"]:
            if record["expires_at"] <= now():
                raise ResourceConflictError("This test alert expired. Create a new test.")
            box = await self.forwarding.get(org)
            if not box or box.id != record["source_id"] or box.address != record["address"]:
                raise ResourceConflictError("The Email connection changed. Create a new test.")
            connection = await self.inventory.get_connection(org, box.id)
            if (
                connection.archived_at
                or connection.status is not ConnectionStatus.READY
                or "incident" not in connection.roles
                or "incident.receiveEmail" not in connection.capabilities
                or box.address not in connection.allowed_resources
            ):
                raise ResourceConflictError("Reconnect the Email source before trying again")

    async def apply(self, record: dict[str, Any]) -> None:
        await self.validate(record)
        await self.audit_request(record)
        event = IngestionEvent.model_validate(record["event"])
        controls = await self.inventory.get_control_version(
            record["organisation_id"], record["credential_id"], record["control_version"]
        )
        source_allowed = (
            not record["simulate"]
            or event.kind != "credential-exposure-detected"
            or any(
                item.connection_id == record["source_id"] and item.resource == record["address"]
                for item in controls.definition.exposure_sources
            )
        )
        incident, _ = await self.service.monitor.automation.ingest(
            event,
            actor_id=record["actor_id"],
            allow_automatic=source_allowed,
            reason=("Test alert: " if record["simulate"] else "Manual report: ")
            + KINDS[record["kind"]][1],
        )
        await self.database.collection("mail").update_one(
            {"organisation_id": record["organisation_id"], "id": record["id"]},
            {"$set": {"state": "completed", "incident_id": incident.id}},
        )

    async def audit_request(self, record: dict[str, Any]) -> None:
        await self.audit.append(
            "audit_" + record["id"].removeprefix("report_"),
            record["organisation_id"],
            "incident.simulation-requested" if record["simulate"] else "incident.reported",
            record["actor_id"],
            f"incident-reports/{record['id']}",
            {
                "credential_id": record["credential_id"],
                "kind": record["kind"],
                "source_id": record["source_id"],
            },
            occurred_at=record["created_at"],
        )

    async def status(self, org: str, identity: str) -> ReportStatus:
        record = await self.database.collection("mail").find_one(
            {
                "organisation_id": org,
                "id": identity,
                "type": "incident-report",
            }
        )
        if not record:
            raise ResourceNotFoundError("Incident request is unavailable")
        if (
            record["simulate"]
            and record["state"] not in {"completed", "failed"}
            and record["expires_at"] <= now()
        ):
            record["state"], record["message"] = (
                "failed",
                "This test alert expired. Create a new test.",
            )
        incident = (
            await self.service.sources.assessment.incidents.get(org, record["incident_id"])
            if record.get("incident_id")
            else None
        )
        return ReportStatus(
            id=record["id"],
            simulate=record["simulate"],
            state=record["state"],
            recipient=record["recipient"],
            message=record.get("message"),
            incident=incident,
        )

    async def drain(self) -> None:
        records = self.database.collection("mail")
        for _ in range(3):
            record = await records.find_one_and_update(
                {
                    "type": "incident-report",
                    "simulate": True,
                    "state": {"$in": ["queued", "sending"]},
                    "available_at": {"$lte": now()},
                    "lease_until": {"$lte": now()},
                },
                {
                    "$set": {"state": "sending", "lease_until": now() + timedelta(minutes=2)},
                    "$inc": {"attempts": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if not record:
                return
            query = {"organisation_id": record["organisation_id"], "id": record["id"]}
            try:
                await self.validate(record)
                await self.audit_request(record)
                key = self.service.config.resend_key
                if not key:
                    raise ResourceConflictError("Email sending is not configured")
                text = record["preview"]
                if record["event"].get("description"):
                    text += "\n\nDetails: " + record["event"]["description"]
                reference = record["id"] + "." + record["ticket"]
                _, text, html = message(
                    "Test incident",
                    text,
                    self.service.config.public_url
                    + "/dashboard/credentials/"
                    + record["credential_id"],
                )
                text += "\n\nKinu test reference: " + reference
                html += "<!-- Kinu test reference: " + reference + " -->"
                response = await self.service.http.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": "Bearer " + key.get_secret_value(),
                        "Idempotency-Key": record["id"],
                    },
                    json={
                        "from": self.service.config.email_sender,
                        "to": [record["recipient"]],
                        "subject": record["email_subject"],
                        "text": text,
                        "html": html,
                        "headers": {"X-Kinu-Test-Reference": reference},
                    },
                    timeout=15,
                    follow_redirects=False,
                )
                if not response.is_success:
                    raise RuntimeError("Test email could not be sent")
                receipt = response.json()
                outbound = receipt.get("id") if isinstance(receipt, dict) else None
                if not isinstance(outbound, str) or not outbound:
                    raise ValueError("Email receipt is missing")
                await records.update_one(
                    {**query, "state": "sending"},
                    {"$set": {"state": "sent", "outbound_id": outbound}},
                )
            except (KinuError, httpx.HTTPError, RuntimeError, ValueError) as error:
                failed = isinstance(error, KinuError) or record["attempts"] >= 5
                await records.update_one(
                    {**query, "state": "sending"},
                    {
                        "$set": {
                            "state": "failed" if failed else "queued",
                            "lease_until": now(),
                            "available_at": now() + timedelta(seconds=15 * record["attempts"]),
                            "message": str(error)
                            if isinstance(error, KinuError)
                            else "Test email could not be sent. Try again.",
                        }
                    },
                )

    async def receive(self, inbox: ForwardInbox, identity: str, data: dict[str, Any]) -> bool:
        text = str(data.get("text") or "") + "\n" + str(data.get("html") or "")
        headers = data.get("headers")
        if isinstance(headers, dict):
            value = next(
                (
                    value
                    for name, value in headers.items()
                    if name.casefold() == "x-kinu-test-reference"
                ),
                None,
            )
            if isinstance(value, str):
                text += "\nKinu test reference: " + value
        match = REFERENCE.search(text)
        if not match:
            return (
                "Kinu test reference:" in text
                and parseaddr(str(data.get("from") or ""))[1].casefold()
                == parseaddr(self.service.config.email_sender)[1].casefold()
            )
        report_id, ticket = match.groups()
        record = await self.database.collection("mail").find_one(
            {
                "organisation_id": inbox.organisation_id,
                "id": report_id,
                "type": "incident-report",
                "simulate": True,
            }
        )
        sender = parseaddr(str(data.get("from") or ""))[1].casefold()
        expected = parseaddr(self.service.config.email_sender)[1].casefold()
        if not record or not hmac.compare_digest(ticket, record["ticket"]) or sender != expected:
            return True
        if record["source_id"] != inbox.id or record["address"] != inbox.address:
            return True
        if record["state"] == "completed":
            return True
        if record["state"] == "failed":
            return True
        try:
            await self.validate(record)
        except KinuError as error:
            await self.database.collection("mail").update_one(
                {"organisation_id": inbox.organisation_id, "id": report_id},
                {"$set": {"state": "failed", "message": str(error)}},
            )
            return True
        claimed = await self.database.collection("mail").find_one_and_update(
            {
                "organisation_id": inbox.organisation_id,
                "id": report_id,
                "received_email_id": {"$in": [None, identity]},
            },
            {"$set": {"received_email_id": identity, "state": "received"}},
            return_document=ReturnDocument.AFTER,
        )
        if claimed:
            await self.apply(claimed)
        return True
