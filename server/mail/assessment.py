from datetime import UTC, datetime
from typing import Any

from agents.decision import Decision, IncidentAgent, IncidentInput
from contracts import ConnectionStatus, IncidentStatus, RunStatus
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.errors import ResourceConflictError
from core.incident.service import IncidentService
from core.incident.store import IncidentStore
from core.inventory.store import InventoryStore
from core.notification.service import NotificationService
from core.storage.database import Database
from core.storage.notification import NotificationStore
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from ingestion.mail import CredentialHint, EmailSignal, EmailSource, MailRule, MailTrust
from rotation.queue import Dispatcher
from rotation.stages import stable

from mail.models import Mailbox, Sender
from mail.port import MailStore
from mail.proof import authenticated


class Assessment:
    def __init__(
        self,
        database: Database,
        store: MailStore,
        agent: IncidentAgent,
        dispatcher: Dispatcher,
        region: str,
    ) -> None:
        self.database, self.store, self.agent, self.dispatcher = database, store, agent, dispatcher
        self.inventory = InventoryStore(database)
        self.audit = AuditWriter(AuditStore(database), region, now)
        self.incidents = IncidentService(
            IncidentStore(database),
            now,
            self.inventory,
            RunWorkflow(RunStore(database)),
            notifier=NotificationService(NotificationStore(database), now),
            audit=self.audit,
        )

    async def active(self, box: Mailbox) -> None:
        current, _ = await self.store.mailbox(box.organisation_id, box.id)
        connection = await self.inventory.get_connection(box.organisation_id, box.id)
        if (
            current.revision != box.revision
            or current.status != "ready"
            or connection.archived_at
            or connection.status is not ConnectionStatus.READY
        ):
            raise ResourceConflictError("Gmail monitoring changed during this check")

    async def prepare(
        self, box: Mailbox, identity: str, raw: bytes, sender: Sender, received: datetime
    ) -> dict[str, Any]:
        await self.active(box)
        provider = await self.inventory.get_connection(
            box.organisation_id, sender.provider_connection_id
        )
        account = provider.verification_connection_id or provider.id
        providers = {
            item.id
            for item in await self.inventory.connections(box.organisation_id)
            if (item.verification_connection_id or item.id) == account and not item.archived_at
        }
        credentials = [
            item
            for item in await self.inventory.credentials(box.organisation_id)
            if not item.archived_at and item.connection_id in providers and item.provider_id
        ]
        proof = await authenticated(raw, sender.sender)
        rule = MailRule(
            id=box.id,
            organisation_id=box.organisation_id,
            sender=sender.sender,
            provider=provider.platform,
            connection_id=box.id,
            created_at=box.connected_at,
        )
        signal = EmailSource().normalise(
            rule,
            raw,
            received,
            tuple(
                CredentialHint(credential_id=item.id, provider_id=item.provider_id or "")
                for item in credentials
            ),
            MailTrust.VERIFIED_RECEIPT if proof else MailTrust.MAILBOX,
        )
        match = next(
            (item for item in credentials if item.provider_id == signal.event.resource.provider_id),
            None,
        )
        provider_id = match.provider_id if match else None
        resource = signal.event.resource.model_copy(
            update={
                "credential_id": match.id if match else None,
                "generation_id": match.active_generation_id if match else None,
                "provider_id": None,
            }
        )
        signal = signal.model_copy(
            update={"event": signal.event.model_copy(update={"resource": resource})}
        )
        identity_key = stable("mail", box.id + ":" + identity)
        signal = signal.model_copy(
            update={
                "event": signal.event.model_copy(
                    update={
                        "id": identity_key,
                        "source": "gmail",
                        "source_event_id": identity_key,
                        "source_reference": f"kinu://mail/{box.id}/{identity}",
                    }
                )
            }
        )
        return {
            "signal": signal.model_dump(mode="json"),
            "sender": sender.sender,
            "authenticated": proof,
            "provider_id": provider_id,
            "revision": box.revision,
            "decision": None,
            "assessed": False,
            "done": False,
        }

    async def process(self, box: Mailbox, identity: str, data: dict[str, Any]) -> None:
        if data.get("done"):
            return
        await self.active(box)
        if data["revision"] != box.revision:
            raise ResourceConflictError("Gmail sender rules changed")
        signal = EmailSignal.model_validate(data["signal"])
        if signal.event.kind == "credential-security-review" and not data.get("provider_id"):
            await self.complete(box, identity, data)
            return
        incident, _ = await self.incidents.ingest(stable("incident", signal.event.id), signal.event)
        if incident.run_id:
            run = await RunStore(self.database).get(box.organisation_id, incident.run_id)
            if run.status is RunStatus.PENDING:
                await self.dispatcher.submit(box.organisation_id, run.id)
            await self.complete(box, identity, data)
            return
        if incident.status is not IncidentStatus.ACTION or not incident.credential_id:
            await self.complete(box, identity, data)
            return
        credential = await self.inventory.get_credential(
            box.organisation_id, incident.credential_id
        )
        controls = await self.inventory.get_control_version(
            box.organisation_id, credential.id, credential.control_version
        )
        source_allowed = any(
            item.connection_id == box.id and item.resource == data["sender"]
            for item in controls.definition.exposure_sources
        )
        due = bool(
            credential.expires_at
            and (credential.expires_at - now()).total_seconds()
            <= controls.definition.rotate_before_expiry_seconds
        )
        allowed = bool(
            data["authenticated"]
            and data["provider_id"] == credential.provider_id
            and signal.event.kind in controls.definition.automatic_triggers
            and (source_allowed if signal.event.kind == "credential-exposure-detected" else due)
            and not controls.definition.require_revoke_approval
        )
        if data.get("decision") is None:
            decision = Decision(decision="review", reason="insufficient-evidence")
            try:
                if not allowed:
                    raise ResourceConflictError("mail evidence requires review")
                decision = await self.agent.assess(
                    box.organisation_id,
                    IncidentInput.model_validate(
                        {
                            "stage": "investigate",
                            "kind": signal.event.kind,
                            "confidence": "verified" if allowed else signal.event.confidence.value,
                            "candidate_count": 1,
                            "policy_allows_rotation": allowed,
                        }
                    ),
                )
                data["assessed"] = True
            except Exception:
                decision = Decision(decision="review", reason="insufficient-evidence")
            data["decision"] = decision.model_dump(mode="json")
            await self.store.message(box.organisation_id, box.id, identity, data)
        decision = Decision.model_validate(data["decision"])
        await self.audit.append(
            stable("assessment", signal.event.id),
            box.organisation_id,
            "incident.ai-assessed" if data.get("assessed") else "incident.review-required",
            "mail_reader",
            f"incidents/{incident.id}",
            {
                "decision": decision.decision,
                "reason": decision.reason,
                "source": "gmail",
                "sender_authenticated": data["authenticated"],
            },
            occurred_at=signal.event.received_at,
        )
        if not allowed or decision.decision != "rotate":
            await self.complete(box, identity, data)
            return
        await self.active(box)
        current = await self.inventory.get_credential(box.organisation_id, credential.id)
        if current.provider_id != data["provider_id"] or current.control_version != controls.id:
            await self.complete(box, identity, data)
            return
        await self.dispatcher.require_scope(box.organisation_id, credential.id)
        _, run, _ = await self.incidents.start_rotation(
            box.organisation_id,
            incident.id,
            stable("command", signal.event.id),
            "mail_reader",
            controls.id,
            "Authenticated Gmail credential alert",
            "urgent",
            signal.event.received_at,
        )
        if run.status is RunStatus.PENDING:
            await self.dispatcher.submit(box.organisation_id, run.id)
        await self.complete(box, identity, data)

    async def complete(self, box: Mailbox, identity: str, data: dict[str, Any]) -> None:
        data["done"] = True
        await self.store.message(box.organisation_id, box.id, identity, data)


def now() -> datetime:
    return datetime.now(UTC)
