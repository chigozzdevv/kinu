import hashlib
from datetime import datetime
from typing import Any

from contracts import Confidence, IngestionEvent, Severity, SourceResource


class ScheduleSource:
    def normalise(
        self,
        organisation_id: str,
        schedule_id: str,
        payload: dict[str, Any],
        received_at: datetime,
    ) -> IngestionEvent:
        credential_id = _string(payload, "credential_id")
        due_at = _datetime(payload.get("due_at"), "due_at")
        if due_at > received_at:
            raise ValueError("scheduled rotation is not due yet")
        occurrence = f"{schedule_id}:{due_at.isoformat()}"
        return _event(
            organisation_id,
            "schedule",
            occurrence,
            "credential-rotation-due",
            due_at,
            Severity.MEDIUM,
            Confidence.VERIFIED,
            SourceResource(credential_id=credential_id),
            f"kinu://schedules/{schedule_id}",
            received_at,
        )


class ProviderSource:
    def normalise(
        self,
        organisation_id: str,
        provider: str,
        payload: dict[str, Any],
        received_at: datetime,
    ) -> IngestionEvent:
        if len(provider) > 55:
            raise ValueError("provider identifier is too long")
        event_id = _string(payload, "event_id")
        event_type = _string(payload, "event_type")
        provider_id = _string(payload, "provider_id")
        observed_at = _datetime(payload.get("observed_at"), "observed_at")
        supported = {
            "credential.compromised": ("credential-exposure-detected", Severity.CRITICAL),
            "credential.abuse": ("credential-abuse-detected", Severity.CRITICAL),
            "credential.disabled": ("credential-disabled", Severity.HIGH),
            "credential.expiring": ("credential-expiring", Severity.MEDIUM),
        }
        if event_type not in supported:
            raise ValueError("provider event type is unsupported")
        kind, severity = supported[event_type]
        return _event(
            organisation_id,
            f"provider-{provider}",
            event_id,
            kind,
            observed_at,
            severity,
            Confidence.VERIFIED,
            SourceResource(provider=provider, provider_id=provider_id),
            str(payload.get("source_reference") or f"provider://{provider}/{event_id}"),
            received_at,
        )


def _event(
    organisation_id: str,
    source: str,
    source_event_id: str,
    kind: str,
    observed_at: datetime,
    severity: Severity,
    confidence: Confidence,
    resource: SourceResource,
    source_reference: str,
    received_at: datetime,
) -> IngestionEvent:
    identity = hashlib.sha256(
        f"{organisation_id}\0{source}\0{source_event_id}\0{kind}".encode()
    ).hexdigest()
    return IngestionEvent(
        id=f"ingestion_{identity[:40]}",
        organisation_id=organisation_id,
        source=source,
        source_event_id=source_event_id,
        kind=kind,
        observed_at=observed_at,
        severity=severity,
        confidence=confidence,
        resource=resource,
        source_reference=source_reference,
        received_at=received_at,
    )


def _string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value


def _datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed
