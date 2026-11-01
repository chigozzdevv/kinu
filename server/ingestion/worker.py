import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from connectors.http import HttpProviderConnector
from contracts import ManagedCredential
from core.audit.store import AuditStore
from core.audit.writer import AuditWriter
from core.incident.service import IncidentService
from core.incident.store import IncidentStore
from core.inventory.store import InventoryStore
from core.notification import NotificationService
from core.storage.database import Database
from core.storage.notification import NotificationStore
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from pymongo import ReturnDocument
from rotation.queue import Dispatcher

from ingestion.automation import IncidentAutomation
from ingestion.detection import DetectionService
from ingestion.runtime import RuntimeInspector

log = logging.getLogger("kinu.monitoring")


class Inventory(InventoryStore):
    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self.database = database

    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]:
        busy = {
            row["credential_id"]
            async for row in self.database.collection("run_locks").find(
                {"organisation_id": organisation_id}
            )
        }
        setups = {
            (row["connection_id"], row["resource"])
            async for row in self.database.collection("provision_locks").find(
                {"organisation_id": organisation_id}
            )
        }
        busy.update(
            binding.credential_id
            for binding in await self.bindings(organisation_id)
            if (binding.runtime_connection_id, binding.runtime_resource) in setups
        )
        return tuple(
            credential
            for credential in await super().credentials(organisation_id)
            if not credential.archived_at and credential.id not in busy
        )


class Monitor:
    def __init__(
        self,
        database: Database,
        provider: HttpProviderConnector,
        runtime: RuntimeInspector,
        dispatcher: Dispatcher,
        region: str,
    ) -> None:
        self.database, self.owner = database, "monitor_" + uuid4().hex
        inventory = InventoryStore(database)
        self.detector = DetectionService(
            Inventory(database),
            inventory,
            provider,
            dict.fromkeys(("aws", "render", "railway", "vercel"), runtime),
            lambda: datetime.now(UTC),
        )
        incidents = IncidentService(
            IncidentStore(database),
            lambda: datetime.now(UTC),
            inventory,
            RunWorkflow(RunStore(database)),
            notifier=NotificationService(NotificationStore(database), lambda: datetime.now(UTC)),
            audit=AuditWriter(AuditStore(database), region, lambda: datetime.now(UTC)),
        )
        self.automation = IncidentAutomation(incidents, inventory, inventory, dispatcher)

    async def poll(self) -> None:
        claims = self.database.collection("monitoring")
        organisations = await self.database.collection("inventory").distinct(
            "organisation_id", {"kind": "credential", "data.archived_at": None}
        )
        for org in organisations:
            now = datetime.now(UTC)
            await claims.update_one(
                {"_id": org},
                {"$setOnInsert": {"due": now, "lease_until": now}},
                upsert=True,
            )
            claimed = await claims.find_one_and_update(
                {"_id": org, "due": {"$lte": now}, "lease_until": {"$lte": now}},
                {"$set": {"owner": self.owner, "lease_until": now + timedelta(minutes=5)}},
                return_document=ReturnDocument.AFTER,
            )
            if not claimed:
                continue
            try:
                for event in await self.detector.detect(org):
                    try:
                        await self.automation.ingest(event)
                    except Exception as error:
                        log.warning("Detected incident handling failed: %s", type(error).__name__)
            except Exception as error:
                log.warning("Credential monitoring failed: %s", type(error).__name__)
            finally:
                await claims.update_one(
                    {"_id": org, "owner": self.owner},
                    {"$set": {"due": datetime.now(UTC) + timedelta(minutes=5), "lease_until": now}},
                )
