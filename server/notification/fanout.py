import hashlib

from contracts import Notification, NotificationDelivery, NotificationEndpoint, NotificationKind
from core.storage.records import RecordTransaction


async def enqueue(rows: RecordTransaction, notification: Notification) -> None:
    org = notification.organisation_id
    invitation = None
    if notification.kind is NotificationKind.TEAM_INVITATION:
        invitation = await rows.database.collection("invitations").find_one(
            {"organisation_id": org, "id": notification.resource_id}, session=rows.session
        )
        if not invitation:
            return
    for endpoint in await rows.list(NotificationEndpoint, org):
        if not endpoint.enabled or notification.kind not in endpoint.event_kinds:
            continue
        if invitation and endpoint.recipients != (invitation["data"]["email"],):
            continue
        identity = (
            "delivery_"
            + hashlib.sha256(f"{org}\0{notification.id}\0{endpoint.id}".encode()).hexdigest()[:40]
        )
        delivery = NotificationDelivery(
            id=identity,
            organisation_id=org,
            notification_id=notification.id,
            endpoint_id=endpoint.id,
            endpoint_revision=endpoint.revision,
            provider=endpoint.provider,
            available_at=notification.created_at,
        )
        await rows.database.collection("notification_deliveries").insert_one(
            {"organisation_id": org, "id": identity, **delivery.model_dump(mode="python")},
            session=rows.session,
        )
