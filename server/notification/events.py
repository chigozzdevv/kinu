from contracts import EventKind, NotificationKind, RunEvent, Severity, Stage


def run_notification(
    event: RunEvent,
) -> tuple[NotificationKind, Severity, str, str] | None:
    if event.kind is EventKind.RUN_FAILED:
        return (
            NotificationKind.ROTATION_FAILED,
            Severity.CRITICAL,
            "Credential rotation failed",
            f"Kinu run {event.run_id} failed during {event.stage.value}.",
        )
    if event.kind is EventKind.RECOVERY_STARTED:
        return (
            NotificationKind.RECOVERY_STARTED,
            Severity.HIGH,
            "Automatic recovery started",
            f"Kinu run {event.run_id} started its authorised recovery branch.",
        )
    if event.kind is EventKind.CLEANUP_REQUIRED:
        return (
            NotificationKind.CLEANUP_REQUIRED,
            Severity.CRITICAL,
            "Credential cleanup is required",
            f"Kinu run {event.run_id} stopped in a cleanup-required state.",
        )
    if event.kind is EventKind.RUN_COMPLETED:
        return (
            NotificationKind.ROTATION_COMPLETED,
            Severity.LOW,
            "Credential rotation completed",
            f"Kinu run {event.run_id} completed with verification evidence.",
        )
    if event.kind is EventKind.STAGE_COMPLETED and event.stage is Stage.COMPLETE:
        return (
            NotificationKind.REVOCATION_SUCCEEDED,
            Severity.LOW,
            "Old credential was revoked",
            f"Kinu run {event.run_id} completed the protected revocation stage.",
        )
    return None
