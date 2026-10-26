import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from contracts import (
    Confidence,
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ControlVersion,
    CredentialGeneration,
    GenerationState,
    IngestionEvent,
    ManagedCredential,
    Severity,
    SourceResource,
)


class DetectionInventory(Protocol):
    async def credentials(self, organisation_id: str) -> tuple[ManagedCredential, ...]: ...

    async def connections(self, organisation_id: str) -> tuple[Connection, ...]: ...

    async def bindings(self, organisation_id: str) -> tuple[ConsumerBinding, ...]: ...

    async def generations(self, organisation_id: str) -> tuple[CredentialGeneration, ...]: ...

    async def record_observation(
        self,
        organisation_id: str,
        credential_id: str,
        generation_id: str,
        expected_revision: int,
        observed_at: datetime,
        expires_at: datetime | None,
        rotation_due_at: datetime | None,
        metadata_digest: str,
    ) -> ManagedCredential: ...


class DetectionControls(Protocol):
    async def get_control_version(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion: ...


class ProviderMetadata(Protocol):
    async def metadata(self, connection: Connection) -> tuple[dict[str, Any], ...]: ...


class RuntimeMetadata(Protocol):
    async def inspect(self, connection: Connection, service_name: str) -> dict[str, Any]: ...


class DetectionService:
    def __init__(
        self,
        inventory: DetectionInventory,
        controls: DetectionControls,
        provider: ProviderMetadata,
        runtimes: Mapping[str, RuntimeMetadata],
        clock: Callable[[], datetime],
    ) -> None:
        self._inventory = inventory
        self._controls = controls
        self._provider = provider
        self._runtimes = runtimes
        self._clock = clock

    async def detect(self, organisation_id: str) -> tuple[IngestionEvent, ...]:
        observed_at = self._clock()
        credentials = await self._inventory.credentials(organisation_id)
        connections = {item.id: item for item in await self._inventory.connections(organisation_id)}
        generations = {item.id: item for item in await self._inventory.generations(organisation_id)}
        bindings: dict[str, list[ConsumerBinding]] = {}
        for binding in await self._inventory.bindings(organisation_id):
            bindings.setdefault(binding.credential_id, []).append(binding)

        provider_cache: dict[str, dict[str, dict[str, Any]]] = {}
        events: list[IngestionEvent] = []
        for credential in credentials:
            connection = connections.get(credential.connection_id)
            generation = generations.get(credential.active_generation_id or "")
            if connection is None or generation is None:
                events.append(
                    _event(
                        credential,
                        "credential-inventory-drift",
                        "inventory-lineage-missing",
                        Severity.HIGH,
                        observed_at,
                    )
                )
                continue
            if (
                ConnectionRole.PROVIDER not in connection.roles
                or connection.platform != credential.provider
                or generation.organisation_id != credential.organisation_id
                or generation.credential_id != credential.id
                or generation.state is not GenerationState.ACTIVE
                or generation.provider_id != credential.provider_id
                or generation.scopes != credential.scopes
                or generation.secret_reference is None
            ):
                events.append(
                    _event(
                        credential,
                        "credential-inventory-drift",
                        "provider-lineage-mismatch",
                        Severity.HIGH,
                        observed_at,
                    )
                )
                continue
            if connection.status is not ConnectionStatus.READY or (
                connection.authorization_expires_at is not None
                and connection.authorization_expires_at <= observed_at
            ):
                state = (
                    "authorization-expired"
                    if connection.status is ConnectionStatus.READY
                    else f"connection-{connection.status.value}"
                )
                events.append(
                    _event(
                        credential,
                        "credential-disabled",
                        state,
                        Severity.HIGH,
                        observed_at,
                    )
                )
                continue
            controls = await self._controls.get_control_version(
                organisation_id, credential.id, credential.control_version
            )
            if connection.interface is ConnectionInterface.API:
                if connection.id not in provider_cache:
                    indexed: dict[str, dict[str, Any]] = {}
                    for metadata in await self._provider.metadata(connection):
                        provider_id = metadata.get("provider_id")
                        if not isinstance(provider_id, str):
                            continue
                        if provider_id in indexed:
                            raise ValueError(
                                "provider returned duplicate credential metadata identifiers"
                            )
                        indexed[provider_id] = metadata
                    provider_cache[connection.id] = indexed
                item = provider_cache[connection.id].get(credential.provider_id or "")
                events.extend(
                    await self._credential_events(
                        credential,
                        generation,
                        item,
                        controls,
                        observed_at,
                    )
                )
            else:
                events.extend(_stored_events(credential, generation, controls, observed_at))
            if controls.definition.require_runtime_alignment:
                for binding in bindings.get(credential.id, []):
                    runtime_connection = connections.get(binding.runtime_connection_id)
                    inspector = (
                        self._runtimes.get(runtime_connection.platform)
                        if runtime_connection is not None
                        and ConnectionRole.RUNTIME in runtime_connection.roles
                        and runtime_connection.interface is ConnectionInterface.API
                        and runtime_connection.status is ConnectionStatus.READY
                        else None
                    )
                    if inspector is None:
                        events.append(
                            _event(
                                credential,
                                "credential-runtime-drift",
                                f"{binding.id}:runtime-inspector-missing",
                                Severity.HIGH,
                                observed_at,
                            )
                        )
                        continue
                    assert runtime_connection is not None
                    runtime = await inspector.inspect(runtime_connection, binding.runtime_resource)
                    if not _runtime_aligned(runtime, binding, generation):
                        events.append(
                            _event(
                                credential,
                                "credential-runtime-drift",
                                f"{binding.id}:{_digest(runtime)}",
                                Severity.HIGH,
                                observed_at,
                            )
                        )
        return tuple(events)

    async def _credential_events(
        self,
        credential: ManagedCredential,
        generation: CredentialGeneration,
        metadata: dict[str, Any] | None,
        policy: ControlVersion,
        observed_at: datetime,
    ) -> tuple[IngestionEvent, ...]:
        if metadata is None:
            return (
                _event(
                    credential,
                    "credential-disabled",
                    "provider-credential-missing",
                    Severity.HIGH,
                    observed_at,
                ),
            )
        expires_at = _optional_datetime(metadata.get("expires_at"))
        rotation_due_at = (
            expires_at - timedelta(seconds=policy.definition.rotate_before_expiry_seconds)
            if expires_at is not None
            else None
        )
        metadata_digest = _digest(metadata)
        await self._inventory.record_observation(
            credential.organisation_id,
            credential.id,
            generation.id,
            credential.revision,
            observed_at,
            expires_at,
            rotation_due_at,
            metadata_digest,
        )
        events: list[IngestionEvent] = []
        previous_observation = credential.last_observed_at or credential.created_at
        if (
            observed_at - previous_observation
        ).total_seconds() > policy.definition.maximum_metadata_age_seconds:
            events.append(
                _event(
                    credential,
                    "credential-metadata-stale",
                    previous_observation.isoformat(),
                    Severity.HIGH,
                    observed_at,
                )
            )
        status = str(metadata.get("status", "")).lower()
        if metadata.get("disabled") is True or status in {
            "deleted",
            "disabled",
            "inactive",
            "revoked",
        }:
            events.append(
                _event(
                    credential,
                    "credential-disabled",
                    metadata_digest,
                    Severity.HIGH,
                    observed_at,
                )
            )
        observed_scopes = metadata.get("scopes")
        if isinstance(observed_scopes, list) and frozenset(observed_scopes) != credential.scopes:
            events.append(
                _event(
                    credential,
                    "credential-provider-drift",
                    metadata_digest,
                    Severity.HIGH,
                    observed_at,
                )
            )
        if rotation_due_at is not None and rotation_due_at <= observed_at:
            assert expires_at is not None
            events.append(
                _event(
                    credential,
                    "credential-expiring",
                    expires_at.isoformat(),
                    Severity.HIGH if expires_at <= observed_at else Severity.MEDIUM,
                    observed_at,
                )
            )
        return tuple(events)


def _stored_events(
    credential: ManagedCredential,
    generation: CredentialGeneration,
    policy: ControlVersion,
    observed_at: datetime,
) -> tuple[IngestionEvent, ...]:
    events: list[IngestionEvent] = []
    last_observed = credential.last_observed_at or generation.last_observed_at
    if last_observed is None:
        last_observed = credential.created_at
    if (
        observed_at - last_observed
    ).total_seconds() > policy.definition.maximum_metadata_age_seconds:
        events.append(
            _event(
                credential,
                "credential-metadata-stale",
                last_observed.isoformat(),
                Severity.HIGH,
                observed_at,
            )
        )
    expires_at = credential.expires_at or generation.expires_at
    rotation_due_at = credential.rotation_due_at
    if rotation_due_at is None and expires_at is not None:
        rotation_due_at = expires_at - timedelta(
            seconds=policy.definition.rotate_before_expiry_seconds
        )
    if rotation_due_at is not None and rotation_due_at <= observed_at:
        assert expires_at is not None
        events.append(
            _event(
                credential,
                "credential-expiring",
                expires_at.isoformat(),
                Severity.HIGH if expires_at <= observed_at else Severity.MEDIUM,
                observed_at,
            )
        )
    return tuple(events)


def _runtime_aligned(
    runtime: dict[str, Any],
    binding: ConsumerBinding,
    generation: CredentialGeneration,
) -> bool:
    if (
        binding.current_generation_id != generation.id
        or (
            generation.secret_reference is not None
            and binding.secret_reference != generation.secret_reference
        )
        or runtime.get("reconciling") is True
        or runtime.get("generation_id") != generation.id
    ):
        return False
    expected_secret, expected_version = _secret_version(
        generation.secret_reference or binding.secret_reference
    )
    bindings = runtime.get("secret_bindings")
    if not isinstance(bindings, list):
        return False
    for item in bindings:
        if not isinstance(item, dict):
            continue
        source = item.get("valueSource")
        reference = source.get("secretKeyRef") if isinstance(source, dict) else None
        if not isinstance(reference, dict):
            continue
        secret = reference.get("secret")
        version = reference.get("version")
        if (
            isinstance(secret, str)
            and isinstance(version, str)
            and _same_secret(secret, expected_secret)
            and version == expected_version
        ):
            return True
    return False


def _secret_version(reference: str) -> tuple[str, str]:
    secret, marker, version = reference.rpartition("/versions/")
    if not marker or not secret or not version:
        return reference, ""
    return secret, version


def _same_secret(actual: str, expected: str) -> bool:
    return actual == expected or (
        "/" not in actual and actual == expected.rsplit("/", maxsplit=1)[-1]
    )


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("provider expires_at metadata must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("provider expires_at metadata must include a timezone")
    return parsed.astimezone(UTC)


def _event(
    credential: ManagedCredential,
    kind: str,
    state: str,
    severity: Severity,
    observed_at: datetime,
) -> IngestionEvent:
    source_event_id = hashlib.sha256(f"{credential.id}\0{kind}\0{state}".encode()).hexdigest()
    identity = hashlib.sha256(
        f"{credential.organisation_id}\0detection\0{source_event_id}\0{kind}".encode()
    ).hexdigest()
    return IngestionEvent(
        id=f"ingestion_{identity[:40]}",
        organisation_id=credential.organisation_id,
        source="detection",
        source_event_id=source_event_id,
        kind=kind,
        observed_at=observed_at,
        severity=severity,
        confidence=Confidence.VERIFIED,
        resource=SourceResource(
            credential_id=credential.id,
            provider=credential.provider,
            provider_id=credential.provider_id,
        ),
        source_reference=f"kinu://credentials/{credential.id}/observations/{source_event_id}",
        received_at=observed_at,
    )


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
