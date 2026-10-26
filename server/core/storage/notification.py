from dataclasses import dataclass

from contracts import Notification, NotificationDelivery, NotificationEndpoint


@dataclass(frozen=True, slots=True)
class NotificationClaim:
    path: str
    delivery: NotificationDelivery
    notification: Notification
    endpoint: NotificationEndpoint
