from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from contracts import (
    Connection,
    ConnectionRole,
    ConsumerBinding,
    ControlVersion,
    ManagedCredential,
    RotationRun,
    RunStatus,
    Stage,
    ToolRequest,
)
from core.errors import CapabilityError

from broker.capability import CapabilityClaims, request_digest

READ_TOOLS = frozenset(
    {
        "provider.listCredentialMetadata",
        "provider.getCredentialStatus",
        "secretStore.getVersion",
        "secretStore.testConsumerAccess",
        "runtime.inspectSecretBindings",
        "telemetry.queryHealth",
        "telemetry.queryCredentialUsage",
    }
)

STAGE_TOOLS: dict[Stage, frozenset[str]] = {
    Stage.TRIGGER: frozenset(),
    Stage.PREFLIGHT: frozenset(
        {
            "provider.listCredentialMetadata",
            "provider.getCredentialStatus",
            "runtime.inspectSecretBindings",
        }
    ),
    Stage.PLAN: frozenset({"provider.listCredentialMetadata", "runtime.inspectSecretBindings"}),
    Stage.CREATE: frozenset({"provider.createCredential", "provider.getCredentialStatus"}),
    Stage.STORE: frozenset(
        {
            "secretStore.getVersion",
            "secretStore.testConsumerAccess",
            "runtime.inspectSecretBindings",
        }
    ),
    Stage.DEPLOY: frozenset({"runtime.deployCandidate", "runtime.rollback"}),
    Stage.VERIFY: frozenset(
        {
            "provider.getCredentialStatus",
            "secretStore.getVersion",
            "runtime.inspectSecretBindings",
            "verification.run",
        }
    ),
    Stage.ROLLOUT: frozenset({"runtime.shiftTraffic", "runtime.rollback"}),
    Stage.OBSERVE: frozenset(
        {"telemetry.queryHealth", "telemetry.queryCredentialUsage", "runtime.rollback"}
    ),
    Stage.APPROVAL: frozenset(),
    Stage.REVOKE: frozenset(
        {
            "provider.revokeCredential",
            "provider.getCredentialStatus",
            "secretStore.disableVersion",
            "secretStore.destroyVersion",
            "verification.run",
        }
    ),
    Stage.COMPLETE: frozenset(),
}


def validate_request(
    request: ToolRequest,
    run: RotationRun,
    connection: Connection,
    credential: ManagedCredential,
    bindings: tuple[ConsumerBinding, ...],
    controls: ControlVersion,
    now: datetime,
) -> None:
    if request.organisation_id != run.organisation_id or request.run_id != run.id:
        raise CapabilityError("tool request does not belong to its run")
    if (
        controls.organisation_id != run.organisation_id
        or controls.credential_id != run.credential_id
        or controls.id != run.control_version
    ):
        raise CapabilityError("credential controls do not belong to the run")
    if (
        run.status is not RunStatus.RUNNING
        or request.fencing_token != run.fencing_token
        or run.lease is None
        or run.lease.fencing_token != run.fencing_token
        or run.lease.expires_at <= now
    ):
        raise CapabilityError("tool request does not hold the current run fence")
    if request.connection_id != connection.id or connection.organisation_id != run.organisation_id:
        raise CapabilityError("tool connection does not belong to the run organisation")
    role = _tool_role(request.tool)
    if role is ConnectionRole.PROVIDER and connection.id != credential.connection_id:
        raise CapabilityError("provider tool is not using the credential management connection")
    if role is ConnectionRole.SECRET_STORE and (
        connection.id != credential.secret_store_connection_id
    ):
        raise CapabilityError("secret-store tool is not using the credential secret store")
    if role is ConnectionRole.RUNTIME and connection.id not in {
        binding.runtime_connection_id for binding in bindings
    }:
        raise CapabilityError("runtime tool is not using a declared consumer binding")
    if request.tool not in controls.definition.allowed_tools:
        raise CapabilityError("tool is not allowed by the credential controls")
    if request.tool not in connection.capabilities:
        raise CapabilityError("connection does not declare the requested capability")
    if request.tool not in STAGE_TOOLS[run.stage]:
        raise CapabilityError(f"tool {request.tool} is not eligible in stage {run.stage.value}")
    resources = _resources(request.tool, request.payload, connection)
    within_boundary = all(
        _allowed(resource, connection.allowed_resources) for resource in resources
    )
    if resources and not within_boundary:
        raise CapabilityError("tool parameters escape the connection resource boundary")


def _tool_role(tool: str) -> ConnectionRole:
    namespace = tool.partition(".")[0]
    roles = {
        "provider": ConnectionRole.PROVIDER,
        "runtime": ConnectionRole.RUNTIME,
        "secretStore": ConnectionRole.SECRET_STORE,
        "telemetry": ConnectionRole.TELEMETRY,
        "incident": ConnectionRole.INCIDENT,
    }
    try:
        return roles[namespace]
    except KeyError as error:
        raise CapabilityError(f"tool {tool} has no connection role") from error


def validate_capability(
    request: ToolRequest,
    run: RotationRun,
    claims: CapabilityClaims,
) -> None:
    expected = (
        request.organisation_id,
        request.run_id,
        request.agent_id,
        request.tool,
        request.connection_id,
        run.stage,
        request.fencing_token,
        request_digest(request.tool, request.payload),
    )
    actual = (
        claims.organisation_id,
        claims.run_id,
        claims.agent_id,
        claims.tool,
        claims.connection_id,
        claims.stage,
        claims.fencing_token,
        claims.request_digest,
    )
    if actual != expected:
        raise CapabilityError("action capability does not bind the exact tool request")


def _resources(tool: str, payload: dict[str, Any], connection: Connection) -> tuple[str, ...]:
    if tool.startswith("provider."):
        provider_id = payload.get("provider_id")
        return (
            (f"{connection.platform}:credentials:{provider_id}",)
            if isinstance(provider_id, str)
            else ()
        )
    keys = {"provider_id", "service", "version", "target", "resource"}
    if tool.startswith("secretStore."):
        keys.add("secret_resource")
    return tuple(value for key, value in payload.items() if key in keys and isinstance(value, str))


def _allowed(resource: str, patterns: Iterable[str]) -> bool:
    parsed = urlparse(resource)
    comparable = parsed.hostname or resource
    return any(
        comparable == pattern
        or comparable.startswith(pattern.rstrip("/") + "/")
        or (len(pattern) > 1 and pattern.endswith("*") and comparable.startswith(pattern[:-1]))
        or (pattern.startswith("*.") and comparable.endswith(pattern[1:]))
        for pattern in patterns
    )
