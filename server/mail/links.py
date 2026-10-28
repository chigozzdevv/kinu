from datetime import UTC, datetime

from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
)
from core.errors import ResourceNotFoundError
from core.storage.database import Database
from core.storage.records import Records

from mail.models import Mailbox


async def save_connection(database: Database, box: Mailbox, *, disabled: bool = False) -> None:
    now = datetime.now(UTC)
    async with Records(database).write(box.organisation_id) as rows:
        try:
            current = await rows.get(Connection, box.organisation_id, box.id)
        except ResourceNotFoundError:
            if disabled:
                return
            await rows.add(
                Connection(
                    id=box.id,
                    organisation_id=box.organisation_id,
                    platform="gmail",
                    display_name=box.email,
                    roles=frozenset({ConnectionRole.INCIDENT}),
                    interface=ConnectionInterface.API,
                    authorization=ConnectionAuthorization.OAUTH,
                    authorization_reference=f"gmail://{box.id}",
                    capabilities=frozenset({"incident.readMail"}),
                    allowed_resources=tuple(item.sender for item in box.senders),
                    status=ConnectionStatus.READY,
                    region="global",
                    created_at=now,
                    updated_at=now,
                    authenticated_at=box.connected_at,
                    last_validated_at=now,
                ),
                box.organisation_id,
            )
            return
        await rows.replace(
            current.model_copy(
                update={
                    "allowed_resources": tuple(item.sender for item in box.senders)
                    or current.allowed_resources,
                    "status": ConnectionStatus.DISABLED
                    if disabled
                    else ConnectionStatus.REAUTHENTICATION
                    if box.status == "reauthentication-required"
                    else ConnectionStatus.READY,
                    "archived_at": now if disabled else None,
                    "authenticated_at": box.connected_at,
                    "last_validated_at": now,
                    "updated_at": now,
                    "revision": current.revision + 1,
                }
            ),
            current.revision,
        )
