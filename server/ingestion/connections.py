import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from connectors.base import SecretValue
from connectors.github import GitHubOnboardingConnector
from connectors.github.webhook import GitHubWebhook
from contracts import (
    Confidence,
    Connection,
    ConnectionStatus,
    GitHubInstallation,
    GitHubWebhookReceipt,
    IngestionEvent,
)
from core.errors import ResourceSetupRequiredError
from core.github import GitHubOnboardingService
from core.github.store import GitHubStore
from core.inventory.store import InventoryStore
from core.storage.records import Records
from mail.assessment import Assessment
from mail.forwarding import Forwarding
from mail.receiving import Receiving, confirmation, verify_webhook
from pymongo import ReturnDocument

if TYPE_CHECKING:
    from executor.runtime import Runtime

log = logging.getLogger("kinu.sources")


class GitHubSecrets:
    def __init__(self, service: "Runtime") -> None:
        self.service = service

    async def access(self, version: str) -> SecretValue:
        if version != "github-client-secret" or not self.service.config.github_client_secret:
            raise ResourceSetupRequiredError("GitHub is not configured")
        return SecretValue(self.service.config.github_client_secret.get_secret_value().encode())


class Sources:
    def __init__(self, service: "Runtime") -> None:
        self.service, self.database = service, service.database
        self.inventory = InventoryStore(self.database)
        self.forwarding = Forwarding(self.database, service.config.inbound_email_domain or "")
        self.assessment = Assessment(
            self.database,
            self.forwarding,
            service.agent,
            service.dispatcher,
            service.config.aws_region,
            source="email",
        )
        self.github_store = GitHubStore(self.database)
        self.automation = service.monitor.automation

    def status(self) -> dict[str, bool]:
        return {
            "email": self.service.config.email_receiving_ready,
            "github": self.service.config.github_ready,
        }

    def github(self) -> GitHubOnboardingService:
        config = self.service.config
        if not config.github_ready:
            raise ResourceSetupRequiredError("GitHub is not configured on this installation")
        callback = config.public_url + "/dashboard/connections?github=1"
        connector = GitHubOnboardingConnector(
            config.github_app_slug or "",
            config.github_client_id or "",
            "github-client-secret",
            callback,
            GitHubSecrets(self.service),
            self.service.http,
        )
        return GitHubOnboardingService(
            self.github_store,
            connector,
            config.github_app_slug or "",
            config.github_client_id or "",
            callback,
            now,
        )

    async def webhook(self, source: str, body: bytes, headers: dict[str, str]) -> None:
        config = self.service.config
        if source == "email":
            if not config.email_receiving_ready or not config.resend_webhook_secret:
                raise ResourceSetupRequiredError("Email receiving is not configured")
            verify_webhook(body, headers, config.resend_webhook_secret, now())
            event = json.loads(body)
            if not isinstance(event, dict):
                raise ValueError("Invalid received email event")
            if event.get("type") != "email.received":
                return
            data = event.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("email_id"), str):
                raise ValueError("Received email identity is missing")
            from uuid import UUID

            identity = str(UUID(data["email_id"]))
            await self.queue("email", identity, {"email_id": identity})
            return
        if source != "github" or not config.github_ready or not config.github_webhook_secret:
            raise ResourceSetupRequiredError("GitHub is not configured")
        verifier = GitHubWebhook()
        verifier.verify(
            body,
            headers.get("x-hub-signature-256", ""),
            config.github_webhook_secret.get_secret_value().encode(),
        )
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Invalid GitHub webhook")
        installation = payload.get("installation")
        if not isinstance(installation, dict) or not isinstance(installation.get("id"), int):
            return
        identity = installation["id"]
        delivery, event_type = (
            headers.get("x-github-delivery", ""),
            headers.get("x-github-event", ""),
        )
        if not delivery or len(delivery) > 256:
            raise ValueError("GitHub delivery identity is missing")
        await self.github_store.received(
            GitHubWebhookReceipt(
                installation_id=identity,
                delivery_id=delivery,
                event=event_type,
                action=str(payload.get("action") or "received")[:96],
                received_at=now(),
            )
        )
        stored = await self.github_store.find(identity)
        if event_type in {"installation", "installation_repositories"}:
            if stored:
                await self.lifecycle(stored, event_type, str(payload.get("action", "")))
            return
        if not stored or event_type != "secret_scanning_alert":
            return
        org = stored["organisation_id"]
        record = GitHubInstallation.model_validate(stored["data"])
        connection = await self.inventory.get_connection(org, f"conn_github_{identity}")
        repository = payload.get("repository", {})
        allowed = {(item["repository_id"], item["full_name"]) for item in stored["repositories"]}
        if (
            not record.active
            or not record.ready
            or connection.archived_at
            or connection.status is not ConnectionStatus.READY
            or not isinstance(repository, dict)
            or (repository.get("id"), repository.get("full_name")) not in allowed
            or repository.get("full_name") not in connection.allowed_resources
        ):
            return
        event = verifier.normalise(org, event_type, body, now(), connection.id)
        # Compare the reported secret privately. A repository match alone cannot
        # prove which of its keys leaked, and the secret never enters a job or an agent.
        matches = []
        alert = payload.get("alert", {})
        value = alert.get("secret") if isinstance(alert, dict) else None
        if isinstance(value, str) and 1 <= len(value) <= 16_384:
            fingerprint = hashlib.sha256(value.encode()).digest()
            for credential in (await self.inventory.credentials(org))[:500]:
                if credential.archived_at:
                    continue
                controls = await self.inventory.get_control_version(
                    org, credential.id, credential.control_version
                )
                if not any(
                    item.connection_id == connection.id
                    and item.resource == repository.get("full_name")
                    for item in controls.definition.exposure_sources
                ):
                    continue
                store = await self.inventory.get_connection(
                    org, credential.secret_store_connection_id
                )
                try:
                    with await self.service.versions.access_for(
                        store, credential.secret_reference
                    ) as secret:
                        if hmac.compare_digest(
                            hashlib.sha256(secret.bytes()).digest(), fingerprint
                        ):
                            matches.append(credential)
                except Exception:
                    continue
        exact = len(matches) == 1 and event.kind == "credential-exposure-detected"
        if exact:
            credential = matches[0]
            event = event.model_copy(
                update={
                    "confidence": Confidence.VERIFIED,
                    "resource": event.resource.model_copy(
                        update={
                            "credential_id": credential.id,
                            "generation_id": credential.active_generation_id,
                            "provider": credential.provider,
                        }
                    ),
                }
            )
        await self.queue(
            "github", event.id, {"event": event.model_dump(mode="json"), "exact": exact}
        )

    async def lifecycle(self, stored: dict[str, Any], event: str, action: str) -> None:
        org, current = stored["organisation_id"], GitHubInstallation.model_validate(stored["data"])
        disabled = action in {"deleted", "suspend"}
        changed_repositories = event == "installation_repositories"
        record = current.model_copy(
            update={
                "active": not disabled,
                "deleted": action == "deleted",
                "webhook_verified_at": now(),
                "ready": not disabled and not changed_repositories and current.repositories_ready,
                "repositories_ready": current.repositories_ready and not changed_repositories,
                "updated_at": now(),
            }
        )
        async with Records(self.database).write(org) as rows:
            connection = await rows.get(Connection, org, f"conn_github_{current.installation_id}")
            await self.database.collection("github_installations").update_one(
                {"_id": current.installation_id, "organisation_id": org},
                {"$set": {"data": record.model_dump(mode="json")}},
                session=rows.session,
            )
            if connection.archived_at or connection.status is ConnectionStatus.DISABLED:
                return
            await rows.replace(
                connection.model_copy(
                    update={
                        "status": ConnectionStatus.DISABLED
                        if disabled
                        else ConnectionStatus.READY
                        if record.ready
                        else ConnectionStatus.SETUP_REQUIRED,
                        "updated_at": now(),
                        "last_validated_at": now()
                        if record.ready
                        else connection.last_validated_at,
                        "revision": connection.revision + 1,
                    }
                ),
                connection.revision,
            )

    async def queue(self, source: str, identity: str, data: dict[str, Any]) -> None:
        key = hashlib.sha256((source + ":" + identity).encode()).hexdigest()
        await self.database.collection("source_deliveries").update_one(
            {"_id": key},
            {
                "$setOnInsert": {
                    "source": source,
                    "data": data,
                    "state": "pending",
                    "available_at": now(),
                    "lease_until": now(),
                    "attempts": 0,
                }
            },
            upsert=True,
        )

    async def drain(self) -> None:
        jobs = self.database.collection("source_deliveries")
        for _ in range(10):
            row = await jobs.find_one_and_update(
                {
                    "state": {"$in": ["pending", "processing"]},
                    "available_at": {"$lte": now()},
                    "lease_until": {"$lte": now()},
                },
                {
                    "$set": {"state": "processing", "lease_until": now() + timedelta(minutes=5)},
                    "$inc": {"attempts": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if not row:
                return
            try:
                if row["source"] == "email":
                    await self.email(row["data"]["email_id"])
                else:
                    event = IngestionEvent.model_validate(row["data"]["event"])
                    connection = await self.inventory.get_connection(
                        event.organisation_id, event.resource.connection_id or ""
                    )
                    if (
                        not connection.archived_at
                        and connection.status is ConnectionStatus.READY
                        and event.resource.repository in connection.allowed_resources
                    ):
                        credential = (
                            await self.inventory.get_credential(
                                event.organisation_id, event.resource.credential_id
                            )
                            if event.resource.credential_id
                            else None
                        )
                        controls = (
                            await self.inventory.get_control_version(
                                event.organisation_id, credential.id, credential.control_version
                            )
                            if credential and not credential.archived_at
                            else None
                        )
                        approved = controls and any(
                            item.connection_id == connection.id
                            and item.resource == event.resource.repository
                            for item in controls.definition.exposure_sources
                        )
                        if (
                            row["data"]["exact"]
                            and credential
                            and approved
                            and credential.active_generation_id == event.resource.generation_id
                        ):
                            await self.automation.ingest(event)
                        else:
                            await self.assessment.incidents.ingest(
                                "incident_" + hashlib.sha256(event.id.encode()).hexdigest()[:40],
                                event,
                            )
                await jobs.update_one(
                    {"_id": row["_id"]},
                    {"$set": {"state": "done", "expires_on": now() + timedelta(days=30)}},
                )
            except Exception as error:
                log.warning("Incident source delivery failed: %s", type(error).__name__)
                await jobs.update_one(
                    {"_id": row["_id"]},
                    {
                        "$set": {
                            "state": "failed" if row["attempts"] >= 12 else "pending",
                            "lease_until": now(),
                            "available_at": now()
                            + timedelta(seconds=min(900, 15 * 2 ** min(row["attempts"], 6))),
                        }
                    },
                )

    async def email(self, identity: str) -> None:
        config = self.service.config
        if not config.resend_key:
            raise ResourceSetupRequiredError("Email receiving is not configured")
        client = Receiving(self.service.http, config.resend_key)
        data = await client.get(identity)
        addresses = data.get("received_for") or data.get("to")
        if not isinstance(addresses, list) or not all(isinstance(item, str) for item in addresses):
            raise ValueError("Received email recipients are unavailable")
        inboxes = await self.forwarding.recipients(addresses)
        if not inboxes:
            return
        raw: bytes | None = None
        for inbox in inboxes:
            link = confirmation(data, inbox.address)
            await self.forwarding.received(inbox, link)
            if link:
                continue
            box, _ = await self.forwarding.mailbox(inbox.organisation_id, inbox.id)
            prepared = await self.forwarding.message(inbox.organisation_id, inbox.id, identity)
            if prepared is None:
                raw = raw if raw is not None else await client.raw(data)
                prepared = await self.assessment.prepare_forwarded(box, identity, raw, now())
                await self.forwarding.message(inbox.organisation_id, inbox.id, identity, prepared)
            await self.assessment.process(box, identity, prepared)


def now() -> datetime:
    return datetime.now(UTC)
