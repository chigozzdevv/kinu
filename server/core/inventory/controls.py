from datetime import datetime

from contracts import (
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlDefinition,
    ControlPreferences,
    GenerationBinding,
    ManagedCredential,
    ProbeDefinition,
    ProbeKind,
    ProbeState,
    ProbeVersion,
    RecoveryAction,
    RecoveryBranch,
    RecoveryMode,
    Stage,
    TargetBinding,
    TelemetryThresholds,
)
from policy import REQUIRED_CHECKS, digest

from core.errors import ResourceConflictError
from core.ids import new_id

_TRIGGER_EVENTS = {
    "expiry": frozenset({"credential-expiring", "credential-rotation-due"}),
    "drift": frozenset(
        {
            "credential-inventory-drift",
            "credential-provider-drift",
            "credential-runtime-drift",
        }
    ),
    "verified-exposure": frozenset({"credential-exposure-detected"}),
}


def validate_exposure_sources(
    connections: tuple[Connection, ...], preferences: ControlPreferences
) -> None:
    by_id = {connection.id: connection for connection in connections}
    for source in preferences.exposure_sources:
        connection = by_id.get(source.connection_id)
        if (
            connection is None
            or ConnectionRole.INCIDENT not in connection.roles
            or connection.interface is not ConnectionInterface.API
            or connection.status is not ConnectionStatus.READY
            or source.resource not in connection.allowed_resources
        ):
            raise ResourceConflictError("exposure source is not available to this organisation")


def _trigger_events(preferences: ControlPreferences) -> frozenset[str]:
    return frozenset(
        event for trigger in preferences.automatic_triggers for event in _TRIGGER_EVENTS[trigger]
    )


def compile_controls(
    credential: ManagedCredential,
    bindings: tuple[ConsumerBinding, ...],
    services: tuple[ConsumerService, ...],
    connections: tuple[Connection, ...],
    preferences: ControlPreferences,
    actor_id: str,
    now: datetime,
) -> tuple[ControlDefinition, tuple[ProbeVersion, ...]]:
    validate_exposure_sources(connections, preferences)
    by_id = {connection.id: connection for connection in connections}
    management = by_id[credential.connection_id]
    secret_store = by_id[credential.secret_store_connection_id]
    browser_managed = management.interface is ConnectionInterface.BROWSER
    verification = (
        None
        if browser_managed
        else next(
            (
                connection
                for connection in connections
                if connection.platform == credential.provider
                and ConnectionRole.PROVIDER in connection.roles
                and connection.interface is ConnectionInterface.API
                and connection.status is ConnectionStatus.READY
                and connection.http is not None
                and connection.http.test_credential is not None
                and connection.http.credential_auth is not None
            ),
            None,
        )
    )
    if verification is None and not browser_managed:
        raise ResourceConflictError(
            "credential automation requires a typed workload authentication test"
        )
    probes: list[ProbeVersion] = []
    assigned: dict[Stage, list[str]] = {
        Stage.VERIFY: [],
        Stage.OBSERVE: [],
        Stage.REVOKE: [],
    }

    def add(stage: Stage, definition: ProbeDefinition) -> None:
        version = ProbeVersion(
            id=definition.id,
            organisation_id=credential.organisation_id,
            probe_id=new_id("probe"),
            number=1,
            definition=definition,
            digest=digest(definition),
            state=ProbeState.ACTIVE,
            created_by=actor_id,
            created_at=now,
            approved_by=actor_id,
            approved_at=now,
        )
        probes.append(version)
        assigned[stage].append(version.id)

    if not browser_managed:
        assert verification is not None
        add(
            Stage.VERIFY,
            _probe(
                credential,
                ProbeKind.PROVIDER,
                management.id,
                credential.provider_id or credential.id,
                GenerationBinding.TARGET,
                TargetBinding.PROVIDER_ID,
            ),
        )
        add(
            Stage.VERIFY,
            _probe(
                credential,
                ProbeKind.CREDENTIAL,
                verification.id,
                credential.provider_id or credential.id,
                GenerationBinding.TARGET,
                TargetBinding.PROVIDER_ID,
                secret_reference=credential.secret_reference,
                secret_connection_id=secret_store.id,
            ),
        )
    add(
        Stage.VERIFY,
        _probe(
            credential,
            ProbeKind.SECRET,
            secret_store.id,
            credential.secret_reference,
            GenerationBinding.TARGET,
            TargetBinding.SECRET_REFERENCE,
        ),
    )
    if not browser_managed:
        assert verification is not None
        add(
            Stage.REVOKE,
            _probe(
                credential,
                ProbeKind.PROVIDER,
                management.id,
                credential.provider_id or credential.id,
                GenerationBinding.CURRENT,
                TargetBinding.PROVIDER_ID,
                negative=True,
            ),
        )
        add(
            Stage.REVOKE,
            _probe(
                credential,
                ProbeKind.CREDENTIAL,
                verification.id,
                credential.provider_id or credential.id,
                GenerationBinding.CURRENT,
                TargetBinding.PROVIDER_ID,
                negative=True,
                secret_reference=credential.secret_reference,
                secret_connection_id=secret_store.id,
            ),
        )
    add(
        Stage.REVOKE,
        _probe(
            credential,
            ProbeKind.SECRET,
            secret_store.id,
            credential.secret_reference,
            GenerationBinding.CURRENT,
            TargetBinding.SECRET_REFERENCE,
        ),
    )

    service_by_id = {service.id: service for service in services}
    for binding in bindings:
        add(
            Stage.VERIFY,
            _probe(
                credential,
                ProbeKind.RUNTIME,
                binding.runtime_connection_id,
                binding.runtime_resource,
                GenerationBinding.TARGET,
            ),
        )
        add(
            Stage.OBSERVE,
            _probe(
                credential,
                ProbeKind.RUNTIME,
                binding.runtime_connection_id,
                binding.runtime_resource,
                GenerationBinding.TARGET,
            ),
        )
        service = service_by_id[binding.service_id]
        for connection_id in service.telemetry_connection_ids:
            connection = by_id[connection_id]
            telemetry = _probe(
                credential,
                ProbeKind.TELEMETRY,
                connection.id,
                connection.allowed_resources[0],
                GenerationBinding.TARGET,
                telemetry=TelemetryThresholds(
                    window_seconds=preferences.maximum_observation_seconds
                ),
            )
            add(Stage.VERIFY, telemetry)
            add(Stage.OBSERVE, telemetry.model_copy(update={"id": new_id("probeversion")}))
            add(
                Stage.OBSERVE,
                telemetry.model_copy(
                    update={
                        "id": new_id("probeversion"),
                        "generation_binding": GenerationBinding.CURRENT,
                        "negative": True,
                    }
                ),
            )

    telemetry_enabled = any(
        service_by_id[binding.service_id].telemetry_connection_ids for binding in bindings
    )
    allowed_tools = {
        "secretStore.getVersion",
        "secretStore.testConsumerAccess",
        "secretStore.disableVersion",
        "secretStore.destroyVersion",
        "runtime.inspectSecretBindings",
        "runtime.deployCandidate",
        "runtime.shiftTraffic",
        "runtime.rollback",
        "verification.run",
    }
    protected_tools: set[str] = set()
    if management.interface is ConnectionInterface.BROWSER:
        allowed_tools.update(
            {
                "browser.click",
                "browser.secure-capture",
                "browser.revokeCredential",
                "provider.testCredential",
            }
        )
        protected_tools.add("browser.secure-capture")
        if preferences.require_revoke_approval:
            protected_tools.add("browser.revokeCredential")
    else:
        allowed_tools.update(
            {
                "provider.listCredentialMetadata",
                "provider.getCredentialStatus",
                "provider.createCredential",
                "provider.revokeCredential",
                "provider.testCredential",
            }
        )
        if preferences.require_revoke_approval:
            protected_tools.add("provider.revokeCredential")
    if preferences.require_revoke_approval:
        protected_tools.update({"secretStore.disableVersion", "secretStore.destroyVersion"})
    if telemetry_enabled:
        allowed_tools.update({"telemetry.queryHealth", "telemetry.queryCredentialUsage"})

    rollback = RecoveryBranch(
        mode=RecoveryMode.ROLLBACK,
        actions=tuple(
            RecoveryAction(
                tool="runtime.rollback",
                operation="rollback",
                parameters={
                    "connection_id": binding.runtime_connection_id,
                    "service": binding.runtime_resource,
                },
            )
            for binding in bindings
        ),
        preserves_old_generation=True,
    )
    required_checks = dict(REQUIRED_CHECKS)
    if not preferences.require_revoke_approval:
        required_checks[Stage.PREFLIGHT] = required_checks[Stage.PREFLIGHT].difference(
            {"approvers-known"}
        )
        required_checks[Stage.APPROVAL] = frozenset({"approval-not-required", "evidence-current"})
    if not telemetry_enabled:
        required_checks[Stage.VERIFY] = required_checks[Stage.VERIFY].difference(
            {"telemetry-healthy"}
        )
        required_checks[Stage.OBSERVE] = required_checks[Stage.OBSERVE].difference(
            {"telemetry-healthy", "old-use-clear"}
        )
    definition = ControlDefinition(
        required_checks=required_checks,
        allowed_tools=frozenset(allowed_tools),
        protected_tools=frozenset(protected_tools),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=preferences.maximum_observation_seconds,
        require_revoke_approval=preferences.require_revoke_approval,
        require_generation_telemetry=telemetry_enabled,
        rotate_before_expiry_seconds=preferences.rotate_before_expiry_seconds,
        automatic_triggers=_trigger_events(preferences),
        emergency_triggers=frozenset(
            {"credential-exposure-detected"}
            if "verified-exposure" in preferences.automatic_triggers
            else ()
        ),
        exposure_sources=preferences.exposure_sources,
        probe_versions={stage: tuple(ids) for stage, ids in assigned.items()},
        recovery={
            Stage.DEPLOY: rollback,
            Stage.VERIFY: rollback,
            Stage.ROLLOUT: rollback,
            Stage.OBSERVE: rollback,
        },
    )
    return definition, tuple(probes)


def update_controls(
    current: ControlDefinition,
    preferences: ControlPreferences,
) -> ControlDefinition:
    browser_managed = "browser.secure-capture" in current.allowed_tools
    protected_tools = set(current.protected_tools)
    if browser_managed:
        protected_tools.add("browser.secure-capture")
    revoke_tools = {
        "browser.revokeCredential" if browser_managed else "provider.revokeCredential",
        "secretStore.disableVersion",
        "secretStore.destroyVersion",
    }
    if preferences.require_revoke_approval:
        protected_tools.update(revoke_tools)
    else:
        protected_tools.difference_update(revoke_tools)
    required_checks = dict(current.required_checks)
    if preferences.require_revoke_approval:
        required_checks[Stage.PREFLIGHT] = required_checks[Stage.PREFLIGHT].union(
            {"approvers-known"}
        )
        required_checks[Stage.APPROVAL] = REQUIRED_CHECKS[Stage.APPROVAL]
    else:
        required_checks[Stage.PREFLIGHT] = required_checks[Stage.PREFLIGHT].difference(
            {"approvers-known"}
        )
        required_checks[Stage.APPROVAL] = frozenset({"approval-not-required", "evidence-current"})
    return current.model_copy(
        update={
            "automatic_triggers": _trigger_events(preferences),
            "emergency_triggers": frozenset(
                {"credential-exposure-detected"}
                if "verified-exposure" in preferences.automatic_triggers
                else ()
            ),
            "exposure_sources": preferences.exposure_sources,
            "rotate_before_expiry_seconds": preferences.rotate_before_expiry_seconds,
            "maximum_observation_seconds": preferences.maximum_observation_seconds,
            "protected_tools": frozenset(protected_tools),
            "require_revoke_approval": preferences.require_revoke_approval,
            "required_checks": required_checks,
        }
    )


def _probe(
    credential: ManagedCredential,
    kind: ProbeKind,
    connection_id: str,
    target: str,
    generation_binding: GenerationBinding,
    target_binding: TargetBinding = TargetBinding.STATIC,
    *,
    negative: bool = False,
    telemetry: TelemetryThresholds | None = None,
    secret_reference: str | None = None,
    secret_connection_id: str | None = None,
) -> ProbeDefinition:
    return ProbeDefinition(
        id=new_id("probeversion"),
        organisation_id=credential.organisation_id,
        kind=kind,
        connection_id=connection_id,
        target=target,
        generation_binding=generation_binding,
        target_binding=target_binding,
        telemetry=telemetry,
        secret_reference=secret_reference,
        secret_connection_id=secret_connection_id,
        negative=negative,
    )
