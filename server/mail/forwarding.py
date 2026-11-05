import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any

from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
)
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.inventory.store import InventoryStore
from core.storage.database import Database
from core.storage.records import Records

from mail.models import ForwardInbox, Mailbox


class Forwarding:
    def __init__(self, database: Database, domain: str) -> None:
        self.database, self.domain = database, domain
        self.rows = Records(database)
        self.inventory = InventoryStore(database)

    @staticmethod
    def identity(org: str) -> str:
        return "email_" + hashlib.sha256(org.encode()).hexdigest()[:32]

    async def get(self, org: str) -> ForwardInbox | None:
        try:
            async with self.rows.read() as rows:
                return await rows.get(ForwardInbox, org, self.identity(org))
        except ResourceNotFoundError:
            return None

    async def create(self, org: str) -> ForwardInbox:
        if not self.domain:
            raise ResourceConflictError("Email receiving is not configured on this installation")
        identity, now = self.identity(org), datetime.now(UTC)
        async with self.rows.write(org) as rows:
            previous = await rows.match(ForwardInbox, org, {"id": identity})
            connection = await rows.match(Connection, org, {"id": identity})
            if (
                previous
                and connection
                and not connection[0].archived_at
                and connection[0].status is not ConnectionStatus.DISABLED
            ):
                return previous[0]
            inbox = ForwardInbox(
                id=identity,
                organisation_id=org,
                address="kinu-" + secrets.token_hex(16) + "@" + self.domain,
                created_at=now,
                revision=previous[0].revision + 1 if previous else 0,
            )
            item = Connection(
                id=identity,
                organisation_id=org,
                platform="email",
                display_name="Email alerts",
                roles=frozenset({ConnectionRole.INCIDENT}),
                interface=ConnectionInterface.API,
                authorization=ConnectionAuthorization.API_KEY,
                authorization_reference="inbound://" + identity,
                capabilities=frozenset({"incident.receiveEmail"}),
                allowed_resources=(inbox.address,),
                status=ConnectionStatus.SETUP_REQUIRED,
                region="global",
                created_at=now,
                updated_at=now,
                revision=connection[0].revision + 1 if connection else 0,
            )
            if previous:
                await rows.replace(inbox, previous[0].revision)
            else:
                await rows.add(inbox, org)
            if connection:
                await rows.replace(item, connection[0].revision)
            else:
                await rows.add(item, org)
        return inbox

    async def recipients(self, addresses: list[str]) -> tuple[ForwardInbox, ...]:
        if len(addresses) > 50:
            raise ValueError("Too many email recipients")
        values = []
        async for row in self.database.collection("inventory").find(
            {
                "kind": "forward-inbox",
                "data.address": {"$in": [item.casefold() for item in addresses]},
            }
        ):
            inbox = ForwardInbox.model_validate(row["data"])
            connection = await self.inventory.get_connection(inbox.organisation_id, inbox.id)
            if not connection.archived_at and connection.status is not ConnectionStatus.DISABLED:
                values.append(inbox)
        return tuple(values)

    async def received(self, inbox: ForwardInbox, confirmation_url: str | None) -> None:
        now = datetime.now(UTC)
        async with self.rows.write(inbox.organisation_id) as rows:
            current = await rows.get(ForwardInbox, inbox.organisation_id, inbox.id)
            connection = await rows.get(Connection, inbox.organisation_id, inbox.id)
            if (
                current.address != inbox.address
                or connection.archived_at
                or connection.status is ConnectionStatus.DISABLED
            ):
                raise ResourceConflictError("Email forwarding changed")
            await rows.replace(
                current.model_copy(
                    update={
                        "last_received_at": now,
                        "confirmation_url": confirmation_url,
                        "receiving": current.receiving or confirmation_url is None,
                        "revision": current.revision + 1,
                    }
                ),
                current.revision,
            )
            if confirmation_url is None and connection.status is not ConnectionStatus.READY:
                await rows.replace(
                    connection.model_copy(
                        update={
                            "status": ConnectionStatus.READY,
                            "authenticated_at": now,
                            "last_validated_at": now,
                            "updated_at": now,
                            "revision": connection.revision + 1,
                        }
                    ),
                    connection.revision,
                )

    async def dismiss_confirmation(self, org: str, url: str) -> ForwardInbox:
        async with self.rows.write(org) as rows:
            current = await rows.get(ForwardInbox, org, self.identity(org))
            if current.confirmation_url is None:
                return current
            if current.confirmation_url != url:
                raise ResourceConflictError(
                    "The forwarding confirmation changed. Refresh and try again."
                )
            updated = current.model_copy(
                update={"confirmation_url": None, "revision": current.revision + 1}
            )
            await rows.replace(updated, current.revision)
        return updated

    async def mailbox(self, org: str, identity: str) -> tuple[Mailbox, dict[str, Any]]:
        inbox = await self.get(org)
        if not inbox or inbox.id != identity:
            raise ResourceNotFoundError("Email forwarding is unavailable")
        connection = await self.inventory.get_connection(org, identity)
        return Mailbox(
            id=inbox.id,
            organisation_id=org,
            email=inbox.address,
            status="ready"
            if not connection.archived_at and connection.status is ConnectionStatus.READY
            else "disabled",
            connected_at=inbox.created_at,
            last_checked_at=inbox.last_received_at,
            revision=connection.revision,
        ), {}

    async def message(
        self, org: str, box: str, identity: str, value: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        query = {"organisation_id": org, "id": "forward_" + identity, "box": box}
        if value is not None:
            await self.database.collection("mail").update_one(
                query, {"$set": {"data": value}}, upsert=True
            )
        row = await self.database.collection("mail").find_one(query)
        return row["data"] if row else None
