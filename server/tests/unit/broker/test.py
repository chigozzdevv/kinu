from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from broker.validate import validate_request
from connectors import ConnectorContext, ConnectorResponse, SecretValue
from connectors.secrets import SecretStore
from contracts import (
    Approval,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ControlVersion,
    Lease,
    ManagedCredential,
    ProtectedAction,
    RunStatus,
    Stage,
    ToolAttempt,
    ToolAttemptStatus,
    ToolRequest,
    ToolResult,
)
from core.errors import CapabilityError
from testkit import make_control_version, make_run

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Provider:
    tools = frozenset({"provider.createCredential", "provider.revokeCredential"})

    def __init__(self) -> None:
        self.creations = 0

    async def execute(
        self, tool: str, payload: dict[str, Any], context: ConnectorContext
    ) -> ConnectorResponse:
        del payload, context
        if tool == "provider.createCredential":
            self.creations += 1
            return ConnectorResponse(
                result={"provider_id": "provider-key-one"}, secret=SecretValue(b"one-time-secret")
            )
        return ConnectorResponse(result={"revoked": True})

    async def prepare(
        self, tool: str, payload: dict[str, Any], context: ConnectorContext
    ) -> dict[str, str | int | bool | tuple[str, ...]]:
        del tool, payload, context
        return {"baseline": ()}

    async def reconcile(
        self,
        tool: str,
        payload: dict[str, Any],
        state: dict[str, str | int | bool | tuple[str, ...]],
        context: ConnectorContext,
    ) -> ConnectorResponse | None:
        del tool, payload, context
        assert state.get("baseline") == ()
        return None


class Repository:
    def __init__(self) -> None:
        self.results: dict[str, tuple[str, ToolAttempt]] = {}
        self.run_value = make_run(NOW).model_copy(
            update={
                "status": RunStatus.RUNNING,
                "stage": Stage.CREATE,
                "lease": Lease(
                    owner_id="worker_one", fencing_token=1, expires_at=NOW + timedelta(minutes=5)
                ),
                "fencing_token": 1,
            }
        )
        self.provider = _connection(
            "provider_one", ConnectionRole.PROVIDER, "provider", Provider.tools
        )
        self.sink = _connection(
            "sink_one", ConnectionRole.SECRET_STORE, "secret-store", SecretStore.tools
        )
        self.credential_value = ManagedCredential(
            id=self.run_value.credential_id,
            organisation_id="org_one",
            connection_id="provider_one",
            secret_store_connection_id="sink_one",
            secret_resource="secret://project-one/credential",
            secret_reference="secret://project-one/credential/versions/3",
            provider="provider",
            kind="api-key",
            display_name="Production credential",
            control_version=self.run_value.control_version,
            created_at=NOW,
            updated_at=NOW,
        )
        controls = make_control_version(now=NOW)
        definition = controls.definition.model_copy(
            update={
                "allowed_tools": frozenset(
                    {"provider.createCredential", "provider.revokeCredential", "verification.run"}
                )
            }
        )
        self.control_value = controls.model_copy(update={"definition": definition})

    async def attempt(self, request: ToolRequest, request_hash: str) -> ToolAttempt | None:
        value = self.results.get(request.id)
        if value is None:
            return None
        assert value[0] == request_hash
        return value[1]

    async def begin(
        self,
        request: ToolRequest,
        request_hash: str,
        reconciliation: dict[str, str | int | bool | tuple[str, ...]],
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        assert request.id not in self.results
        self.results[request.id] = (
            request_hash,
            ToolAttempt(
                id=request.id,
                organisation_id=request.organisation_id,
                run_id=request.run_id,
                request_digest=request_hash,
                tool=request.tool,
                status=ToolAttemptStatus.RUNNING,
                reconciliation=reconciliation,
                started_at=now,
                lease_expires_at=lease_expires_at,
            ),
        )

    async def reclaim(
        self,
        request: ToolRequest,
        request_hash: str,
        expected_expiry: datetime,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        attempt = self.results[request.id][1]
        assert attempt.lease_expires_at == expected_expiry
        assert attempt.lease_expires_at <= now
        self.results[request.id] = (
            request_hash,
            attempt.model_copy(update={"lease_expires_at": lease_expires_at}),
        )

    async def checkpoint(self, request: ToolRequest, request_hash: str, result: ToolResult) -> None:
        attempt = self.results[request.id][1]
        self.results[request.id] = (request_hash, attempt.model_copy(update={"checkpoint": result}))

    async def finish(
        self, request: ToolRequest, request_hash: str, result: ToolResult, now: datetime
    ) -> None:
        attempt = self.results[request.id][1]
        self.results[request.id] = (
            request_hash,
            attempt.model_copy(
                update={
                    "status": ToolAttemptStatus.SUCCEEDED
                    if result.succeeded
                    else ToolAttemptStatus.FAILED,
                    "result": result,
                    "checkpoint": None,
                    "completed_at": now,
                }
            ),
        )

    async def run(self, organisation_id: str, run_id: str) -> Any:
        assert (organisation_id, run_id) == ("org_one", self.run_value.id)
        return self.run_value

    async def connection(self, organisation_id: str, connection_id: str) -> Connection:
        assert organisation_id == "org_one"
        return {"provider_one": self.provider, "sink_one": self.sink}[connection_id]

    async def credential(self, organisation_id: str, credential_id: str) -> ManagedCredential:
        assert credential_id == self.credential_value.id
        return self.credential_value

    async def bindings(
        self, organisation_id: str, credential_id: str
    ) -> tuple[ConsumerBinding, ...]:
        return ()

    async def controls(
        self, organisation_id: str, credential_id: str, version_id: str
    ) -> ControlVersion:
        assert credential_id == self.control_value.credential_id
        assert version_id == self.control_value.id
        return self.control_value

    async def approval(self, organisation_id: str, approval_id: str) -> Approval:
        raise AssertionError("automatic creation requires no approval")

    async def action(self, organisation_id: str, action_id: str) -> ProtectedAction:
        raise AssertionError("automatic creation requires no approval")


def _connection(
    connection_id: str, role: ConnectionRole, platform: str, tools: frozenset[str]
) -> Connection:
    resources = (
        "secret://project-one/credential"
        if role is ConnectionRole.SECRET_STORE
        else "provider-key-one"
    )
    from testkit import make_http_provider_api

    return Connection(
        id=connection_id,
        organisation_id="org_one",
        platform=platform,
        display_name=platform,
        roles=frozenset({role}),
        interface=ConnectionInterface.API,
        authorization=ConnectionAuthorization.WORKLOAD_IDENTITY
        if role is ConnectionRole.SECRET_STORE
        else ConnectionAuthorization.API_KEY,
        authorization_reference="workload-identity://kinu-broker@project-one.identity.example.com"
        if role is ConnectionRole.SECRET_STORE
        else "secret://project-one/auth/versions/1",
        capabilities=tools,
        allowed_resources=(resources,),
        http=make_http_provider_api() if role is ConnectionRole.PROVIDER else None,
        status=ConnectionStatus.READY,
        region="us-east1",
        created_at=NOW,
        updated_at=NOW,
    )


def test_provider_resource_boundary_uses_connection_platform_and_credential_id() -> None:
    repository = Repository()
    run = repository.run_value.model_copy(update={"stage": Stage.REVOKE})
    connection = repository.provider.model_copy(
        update={"allowed_resources": ("provider:credentials:*",)}
    )
    request = ToolRequest(
        id="request_revoke",
        organisation_id="org_one",
        run_id=run.id,
        agent_id="operator_one",
        tool="provider.revokeCredential",
        connection_id=connection.id,
        payload={"provider_id": "provider-key-one"},
        fencing_token=run.fencing_token,
    )
    validate_request(
        request, run, connection, repository.credential_value, (), repository.control_value, NOW
    )
    with pytest.raises(CapabilityError, match="resource boundary"):
        validate_request(
            request,
            run,
            connection.model_copy(
                update={"allowed_resources": ("provider:credentials:another-key",)}
            ),
            repository.credential_value,
            (),
            repository.control_value,
            NOW,
        )
