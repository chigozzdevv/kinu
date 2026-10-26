import hashlib
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Protocol

from connectors import Connector, ConnectorContext, ConnectorResponse
from connectors.base.connector import ReconcilesMutations
from connectors.base.errors import AmbiguousMutationError, ConnectorError
from connectors.secrets import SecretStore
from contracts import (
    Approval,
    ApprovalDecision,
    Connection,
    ConnectionInterface,
    ConnectionRole,
    ConsumerBinding,
    ControlVersion,
    Evidence,
    ManagedCredential,
    ProtectedAction,
    RotationRun,
    ToolAttempt,
    ToolAttemptStatus,
    ToolRequest,
    ToolResult,
)
from core.audit import AuditWriter
from core.errors import ApprovalError, CapabilityError, ResourceConflictError
from policy import digest

from broker.capability import CapabilityClaims, CapabilityVerifier, request_digest
from broker.validate import READ_TOOLS, validate_capability, validate_request


class BrokerRepository(Protocol):
    async def attempt(self, request: ToolRequest, request_hash: str) -> ToolAttempt | None: ...

    async def begin(
        self,
        request: ToolRequest,
        request_hash: str,
        reconciliation: dict[str, str | int | bool | tuple[str, ...]],
        now: datetime,
        lease_expires_at: datetime,
    ) -> None: ...

    async def reclaim(
        self,
        request: ToolRequest,
        request_hash: str,
        expected_expiry: datetime,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None: ...

    async def checkpoint(
        self,
        request: ToolRequest,
        request_hash: str,
        result: ToolResult,
    ) -> None: ...

    async def finish(
        self,
        request: ToolRequest,
        request_hash: str,
        result: ToolResult,
        now: datetime,
    ) -> None: ...

    async def run(self, organisation_id: str, run_id: str) -> RotationRun: ...

    async def connection(self, organisation_id: str, connection_id: str) -> Connection: ...

    async def credential(self, organisation_id: str, credential_id: str) -> ManagedCredential: ...

    async def bindings(
        self, organisation_id: str, credential_id: str
    ) -> tuple[ConsumerBinding, ...]: ...

    async def controls(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion: ...

    async def approval(self, organisation_id: str, approval_id: str) -> Approval: ...

    async def action(self, organisation_id: str, action_id: str) -> ProtectedAction: ...


class EvidenceSink(Protocol):
    async def store(
        self,
        organisation_id: str,
        run_id: str,
        kind: str,
        content: bytes,
        content_type: str,
        now: datetime,
    ) -> Evidence: ...


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[tuple[ConnectionRole, ConnectionInterface, str], Connector] = {}

    def register(
        self,
        role: ConnectionRole,
        interface: ConnectionInterface,
        platform: str,
        connector: Connector,
    ) -> None:
        key = (role, interface, platform)
        if key in self._connectors:
            raise ValueError(
                f"connector {role.value}/{interface.value}/{platform} is already registered"
            )
        self._connectors[key] = connector

    def resolve(self, connection: Connection, tool: str) -> Connector:
        role = _tool_role(tool)
        if role not in connection.roles:
            raise CapabilityError(
                f"connection {connection.id} does not provide the {role.value} role"
            )
        key = (role, connection.interface, connection.platform)
        connector = self._connectors.get(key)
        if connector is None and role is ConnectionRole.PROVIDER:
            connector = self._connectors.get((role, connection.interface, "*"))
        if connector is None or tool not in connector.tools:
            raise CapabilityError(
                f"no connector provides {tool} for "
                f"{role.value}/{connection.interface.value}/{connection.platform}"
            )
        return connector


class BrokerService:
    def __init__(
        self,
        repository: BrokerRepository,
        connectors: ConnectorRegistry,
        capabilities: CapabilityVerifier,
        evidence: EvidenceSink,
        audit: AuditWriter,
        clock: Callable[[], datetime],
        attempt_lease_seconds: int = 120,
    ) -> None:
        self._repository = repository
        self._connectors = connectors
        self._capabilities = capabilities
        self._evidence = evidence
        self._audit = audit
        self._clock = clock
        self._attempt_lease = timedelta(seconds=attempt_lease_seconds)

    async def execute(self, request: ToolRequest, capability: str | None) -> ToolResult:
        request_hash = request_digest(request.tool, request.payload)
        previous = await self._repository.attempt(request, request_hash)
        run = await self._repository.run(request.organisation_id, request.run_id)
        connection = await self._repository.connection(
            request.organisation_id, request.connection_id
        )
        credential = await self._repository.credential(request.organisation_id, run.credential_id)
        bindings = await self._repository.bindings(request.organisation_id, run.credential_id)
        controls = await self._repository.controls(
            request.organisation_id, run.credential_id, run.control_version
        )
        now = self._clock()
        validate_request(request, run, connection, credential, bindings, controls, now)
        if request.tool not in READ_TOOLS:
            await self._authorize_mutation(request, run, request_hash, capability)
        if previous is not None and previous.status is not ToolAttemptStatus.RUNNING:
            if previous.result is None:
                raise RuntimeError("terminal tool attempt has no result")
            return previous.result

        context = ConnectorContext(
            request_id=request.id,
            agent_id=request.agent_id,
            connection=connection,
            run=run,
            now=now,
            idempotency_key=request.id,
        )
        connector = self._connectors.resolve(connection, request.tool)
        if previous is None:
            reconciliation = await self._prepare(connector, request, context, credential)
            await self._repository.begin(
                request,
                request_hash,
                reconciliation,
                now,
                now + self._attempt_lease,
            )
        else:
            if previous.checkpoint is not None:
                await self._repository.finish(
                    request, request_hash, previous.checkpoint, self._clock()
                )
                return previous.checkpoint
            if previous.lease_expires_at > now:
                raise ResourceConflictError(f"tool request {request.id} is already running")
            await self._repository.reclaim(
                request,
                request_hash,
                previous.lease_expires_at,
                now,
                now + self._attempt_lease,
            )
            reconciled = await self._reconcile(
                connector,
                request,
                context,
                credential,
                previous.reconciliation,
            )
            if reconciled is not None:
                reconciled = await self._store_provider_secret(
                    request, credential, reconciled, context
                )
                evidence_ids = await self._write_evidence(request, reconciled, now)
                result = ToolResult(
                    request_id=request.id,
                    succeeded=True,
                    result=reconciled.result,
                    evidence_ids=evidence_ids,
                )
                await self._repository.checkpoint(request, request_hash, result)
                await self._repository.finish(request, request_hash, result, self._clock())
                return result
        try:
            response = await connector.execute(request.tool, request.payload, context)
            response = await self._store_provider_secret(request, credential, response, context)
            evidence_ids = await self._write_evidence(request, response, now)
            result = ToolResult(
                request_id=request.id,
                succeeded=True,
                result=response.result,
                evidence_ids=evidence_ids,
            )
            await self._repository.checkpoint(request, request_hash, result)
        except AmbiguousMutationError:
            # The attempt remains leased and reconcilable. A later reclaim must
            # prove or clean the remote outcome before another mutation runs.
            raise
        except ConnectorError as error:
            result = ToolResult(
                request_id=request.id,
                succeeded=False,
                error_code=error.code,
                result={"retryable": error.retryable},
            )
        except Exception:
            result = ToolResult(
                request_id=request.id,
                succeeded=False,
                error_code="broker-internal-error",
                result={"retryable": False},
            )
            await self._repository.finish(request, request_hash, result, self._clock())
            raise

        await self._repository.finish(request, request_hash, result, self._clock())
        await self._audit.append(
            event_id=request.id,
            organisation_id=request.organisation_id,
            kind="tool.succeeded" if result.succeeded else "tool.failed",
            actor_id=request.agent_id,
            resource=f"connections/{request.connection_id}",
            run_id=request.run_id,
            payload={
                "tool": request.tool,
                "request_digest": request_hash,
                "error_code": result.error_code,
            },
            evidence_ids=result.evidence_ids,
        )
        return result

    async def _prepare(
        self,
        connector: Connector,
        request: ToolRequest,
        context: ConnectorContext,
        credential: ManagedCredential,
    ) -> dict[str, str | int | bool | tuple[str, ...]]:
        if not isinstance(connector, ReconcilesMutations):
            return {}
        value = await connector.prepare(request.tool, request.payload, context)
        if request.tool == "provider.createCredential":
            sink, secrets, secret_resource = await self._secret_sink(request, credential)
            versions = tuple(
                sorted(
                    name
                    for item in await secrets.versions_for(sink, secret_resource)
                    if isinstance(name := item.get("name"), str)
                )
            )
            value = {
                **value,
                "secret_resource": secret_resource,
                "before_secret_versions": versions,
            }
        if not isinstance(value, dict) or _contains_sensitive(value):
            raise ConnectorError(
                "invalid-reconciliation-state",
                "connector reconciliation state is invalid or contains sensitive material",
            )
        return value

    async def _reconcile(
        self,
        connector: Connector,
        request: ToolRequest,
        context: ConnectorContext,
        credential: ManagedCredential,
        state: dict[str, str | int | bool | tuple[str, ...]],
    ) -> ConnectorResponse | None:
        if not isinstance(connector, ReconcilesMutations):
            raise ConnectorError(
                "stale-mutation-unrecoverable",
                "connector cannot reconcile an expired mutation attempt",
            )
        value = await connector.reconcile(request.tool, request.payload, state, context)
        if request.tool == "provider.createCredential":
            await self._reconcile_secret_sink(request, credential, state)
        if value is not None and not isinstance(value, ConnectorResponse):
            raise ConnectorError(
                "invalid-reconciliation-result",
                "connector returned an invalid reconciliation result",
            )
        return value

    async def _authorize_mutation(
        self,
        request: ToolRequest,
        run: RotationRun,
        request_hash: str,
        token: str | None,
    ) -> None:
        if token is None:
            raise CapabilityError("mutating tools require an action capability")
        claims = self._capabilities.verify(token, self._clock())
        validate_capability(request, run, claims)
        if claims.approval_id is None:
            if claims.action_digest != request_hash:
                raise CapabilityError("automatic capability action digest changed")
            return
        await self._validate_approval(request, claims)

    async def _validate_approval(self, request: ToolRequest, claims: CapabilityClaims) -> None:
        assert claims.approval_id is not None
        approval = await self._repository.approval(request.organisation_id, claims.approval_id)
        action = await self._repository.action(request.organisation_id, approval.action_id)
        if approval.decision is not ApprovalDecision.APPROVED or approval.consumed_at is None:
            raise ApprovalError("protected action approval has not been consumed")
        if approval.run_id != request.run_id or action.run_id != request.run_id:
            raise ApprovalError("protected action belongs to another run")
        if action.kind != request.tool:
            raise ApprovalError("protected action does not match the requested tool")
        action_hash = digest(action)
        if claims.action_digest != action_hash or approval.action_digest != action_hash:
            raise ApprovalError("protected action digest changed after approval")

    async def _store_provider_secret(
        self,
        request: ToolRequest,
        credential: ManagedCredential,
        response: ConnectorResponse,
        context: ConnectorContext,
    ) -> ConnectorResponse:
        if request.tool != "provider.createCredential":
            if response.secret is not None:
                response.secret.clear()
                raise ConnectorError(
                    "unexpected-secret", "connector returned a secret for a non-creation tool"
                )
            return response
        if response.secret is None:
            raise ConnectorError(
                "provider-secret-missing", "provider returned no one-time credential secret"
            )
        try:
            sink, connector, secret_resource = await self._secret_sink(request, credential)
            try:
                stored = await connector.add_version_for(sink, secret_resource, response.secret)
            except Exception as store_error:
                with suppress(Exception):
                    await self._compensate_created_credential(response, context)
                raise AmbiguousMutationError(
                    "secret version write outcome requires reconciliation"
                ) from store_error
            return ConnectorResponse(
                result={**response.result, **stored}, evidence=response.evidence
            )
        except AmbiguousMutationError:
            raise
        except Exception as store_error:
            await self._compensate_created_credential(response, context)
            if isinstance(store_error, ConnectorError):
                raise
            raise ConnectorError(
                "secret-transfer-failed", "created credential could not be stored"
            ) from store_error
        finally:
            response.secret.clear()

    async def _secret_sink(
        self,
        request: ToolRequest,
        credential: ManagedCredential,
    ) -> tuple[Connection, SecretStore, str]:
        sink_id = _string(request.payload, "sink_connection_id")
        secret_resource = _string(request.payload, "secret_resource")
        sink = await self._repository.connection(request.organisation_id, sink_id)
        self._validate_secret_sink(sink, credential, secret_resource)
        connector = self._connectors.resolve(sink, "secretStore.getVersion")
        if not isinstance(connector, SecretStore):
            raise ConnectorError(
                "unsupported-secret-sink", "secret sink cannot accept direct secret transfer"
            )
        return sink, connector, secret_resource

    async def _reconcile_secret_sink(
        self,
        request: ToolRequest,
        credential: ManagedCredential,
        state: dict[str, str | int | bool | tuple[str, ...]],
    ) -> None:
        sink, connector, secret_resource = await self._secret_sink(request, credential)
        before = state.get("before_secret_versions")
        if state.get("secret_resource") != secret_resource or not isinstance(before, tuple):
            raise AmbiguousMutationError("secret sink reconciliation checkpoint is invalid")
        candidates = [
            item["name"]
            for item in await connector.versions_for(sink, secret_resource)
            if isinstance(item.get("name"), str)
            and item["name"] not in before
            and item.get("state") == "ENABLED"
        ]
        if len(candidates) > 1:
            raise AmbiguousMutationError(
                "stale secret write has multiple attributable cleanup candidates"
            )
        if candidates:
            await connector.disable_for(sink, candidates[0])

    def _validate_secret_sink(
        self,
        sink: Connection,
        credential: ManagedCredential,
        secret_resource: str,
    ) -> None:
        if (
            sink.id != credential.secret_store_connection_id
            or ConnectionRole.SECRET_STORE not in sink.roles
            or sink.interface is not ConnectionInterface.API
        ):
            raise ConnectorError(
                "secret-sink-not-assigned", "secret sink is not assigned to the credential"
            )
        if secret_resource != credential.secret_resource:
            raise ConnectorError(
                "secret-sink-mismatch", "secret resource differs from the credential mapping"
            )
        allowed = any(
            secret_resource == boundary or secret_resource.startswith(boundary.rstrip("/") + "/")
            for boundary in sink.allowed_resources
        )
        if not allowed:
            raise ConnectorError(
                "secret-sink-boundary", "secret resource escapes the assigned sink"
            )

    async def _compensate_created_credential(
        self,
        response: ConnectorResponse,
        context: ConnectorContext,
    ) -> None:
        provider_id = response.result.get("provider_id")
        if not isinstance(provider_id, str):
            raise ConnectorError(
                "secret-transfer-cleanup", "created credential has no provider ID for cleanup"
            )
        connector = self._connectors.resolve(context.connection, "provider.revokeCredential")
        try:
            await connector.execute(
                "provider.revokeCredential", {"provider_id": provider_id}, context
            )
        except ConnectorError as error:
            raise ConnectorError(
                "secret-transfer-cleanup",
                "secret transfer failed and provider credential cleanup also failed",
            ) from error

    async def _write_evidence(
        self,
        request: ToolRequest,
        response: ConnectorResponse,
        now: datetime,
    ) -> tuple[str, ...]:
        values: list[str] = []
        for kind, content, content_type in response.evidence:
            stored = await self._evidence.store(
                request.organisation_id,
                request.run_id,
                kind,
                content,
                content_type,
                now,
            )
            values.append(stored.id)
        return tuple(values)


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


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectorError("invalid-parameter", f"{key} is required")
    return value


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key.lower() in {"secret", "api_key", "password", "token", "value"}
            or _contains_sensitive(item)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_contains_sensitive(item) for item in value)
    return False
