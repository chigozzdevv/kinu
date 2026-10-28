from contracts import NotificationKind, NotificationTopic
from core.auth import Permission

TOPICS: tuple[tuple[NotificationTopic, Permission], ...] = (
    (
        NotificationTopic(
            id="incidents",
            label="Incidents",
            event_kinds=frozenset(
                {NotificationKind.INCIDENT, NotificationKind.INCIDENT_CONFIRMATION}
            ),
        ),
        Permission.INCIDENT_WRITE,
    ),
    (
        NotificationTopic(
            id="rotation-failures",
            label="Rotation failures",
            event_kinds=frozenset(
                {
                    NotificationKind.ROTATION_FAILED,
                    NotificationKind.RECOVERY_STARTED,
                    NotificationKind.CLEANUP_REQUIRED,
                }
            ),
        ),
        Permission.RUN_WRITE,
    ),
    (
        NotificationTopic(
            id="approvals",
            label="Approval requests",
            event_kinds=frozenset({NotificationKind.APPROVAL_REQUIRED}),
        ),
        Permission.APPROVAL_DECIDE,
    ),
    (
        NotificationTopic(
            id="credential-use",
            label="Previous credential use",
            event_kinds=frozenset({NotificationKind.OLD_KEY_USED}),
        ),
        Permission.RUN_READ,
    ),
    (
        NotificationTopic(
            id="connection-health",
            label="Connection issues",
            event_kinds=frozenset({NotificationKind.CONNECTION_UNHEALTHY}),
        ),
        Permission.INVENTORY_WRITE,
    ),
    (
        NotificationTopic(
            id="playbook-review",
            label="Playbook reviews",
            event_kinds=frozenset({NotificationKind.PLAYBOOK_REVIEW}),
        ),
        Permission.PLAYBOOK_APPROVE,
    ),
    (
        NotificationTopic(
            id="rotation-due",
            label="Upcoming rotations",
            event_kinds=frozenset({NotificationKind.ROTATION_DUE}),
        ),
        Permission.RUN_WRITE,
    ),
    (
        NotificationTopic(
            id="rotation-completed",
            label="Completed rotations",
            event_kinds=frozenset(
                {
                    NotificationKind.REVOCATION_SUCCEEDED,
                    NotificationKind.ROTATION_COMPLETED,
                }
            ),
        ),
        Permission.RUN_READ,
    ),
)
