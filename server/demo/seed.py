from datetime import UTC, datetime

from contracts import (
    Application,
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
    ConsumerBinding,
    ConsumerService,
    ControlDefinition,
    ControlVersion,
    CredentialGeneration,
    Environment,
    ExposureSource,
    GenerationState,
    LambdaProviderApi,
    ManagedCredential,
    RecoveryMode,
    Stage,
)
from core.connection.models import AwsLink
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.database import Database
from core.storage.records import Records
from policy import REQUIRED_CHECKS, digest

from demo.config import DemoConfig

TOOLS = frozenset(
    {
        "provider.listCredentials",
        "provider.getCredential",
        "provider.createCredential",
        "provider.revokeCredential",
        "runtime.getService",
        "runtime.updateConfiguration",
        "runtime.health",
        "runtime.promote",
        "secretStore.getVersion",
        "secretStore.disableVersion",
        "verification.run",
    }
)


async def seed(
    database: Database,
    config: DemoConfig,
    provider_id: str,
    reference: str,
    role_arn: str,
    external_id: str,
    actor_id: str,
    *,
    ready: bool = False,
) -> None:
    org, now = config.organisation_id, datetime.now(UTC)
    checks = dict(REQUIRED_CHECKS)
    checks[Stage.PREFLIGHT] = checks[Stage.PREFLIGHT] - {"approvers-known"}
    checks[Stage.APPROVAL] = frozenset({"approval-not-required", "evidence-current"})
    definition = ControlDefinition(
        required_checks=checks,
        allowed_tools=TOOLS,
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=60,
        automatic_triggers=frozenset({"credential-exposure-detected"}),
        exposure_sources=(
            ExposureSource(
                connection_id="connection_demo_mail", resource="security@demo.kinu.invalid"
            ),
        ),
    )
    rpc = LambdaProviderApi(
        function_arn=config.provider_function,
        operations={
            "provider.listCredentials": "metadata",
            "provider.getCredential": "metadata",
            "provider.createCredential": "create",
            "provider.revokeCredential": "revoke",
        },
    )
    connections = []
    for name, role, resources, capabilities in (
        (
            "provider",
            ConnectionRole.PROVIDER,
            (config.provider_function,),
            frozenset(rpc.operations),
        ),
        (
            "store",
            ConnectionRole.SECRET_STORE,
            (config.secret,),
            frozenset(
                {
                    "secretStore.getVersion",
                    "secretStore.disableVersion",
                    "secretStore.testConsumerAccess",
                }
            ),
        ),
        (
            "runtime",
            ConnectionRole.RUNTIME,
            (config.consumer_function,),
            frozenset(
                {
                    "runtime.getService",
                    "runtime.updateConfiguration",
                    "runtime.health",
                    "runtime.promote",
                }
            ),
        ),
    ):
        connections.append(
            Connection(
                id=f"connection_demo_{name}",
                organisation_id=org,
                platform="aws",
                display_name=f"Demo {name}",
                roles=frozenset({role}),
                interface=ConnectionInterface.API,
                authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
                authorization_reference="aws-role://aws_demo",
                capabilities=capabilities,
                allowed_resources=resources,
                rpc=rpc if name == "provider" else None,
                status=ConnectionStatus.READY if ready else ConnectionStatus.SETUP_REQUIRED,
                region=config.region,
                created_at=now,
                updated_at=now,
            )
        )
    credential = ManagedCredential(
        id=config.credential_id,
        organisation_id=org,
        connection_id="connection_demo_provider",
        secret_store_connection_id="connection_demo_store",
        secret_resource=config.secret,
        secret_reference=reference,
        provider="aws",
        kind="api-key",
        display_name="Demo provider key",
        provider_id=provider_id,
        scopes=frozenset({"demo:read"}),
        consumer_ids=("service_demo",),
        active_generation_id="generation_demo_initial",
        control_version="controls_demo_initial",
        created_at=now,
        updated_at=now,
    )
    values = [
        AwsLink(
            id="aws_demo",
            organisation_id=org,
            account_id=role_arn.split(":")[4],
            role_arn=role_arn,
            external_id=external_id,
            region=config.region,
            created_at=now,
            confirmed_at=now if ready else None,
        ),
        *connections,
        Connection(
            id="connection_demo_mail",
            organisation_id=org,
            platform="demo-email",
            display_name="Demo email fixture",
            roles=frozenset({ConnectionRole.INCIDENT}),
            interface=ConnectionInterface.API,
            authorization=ConnectionAuthorization.WORKLOAD_IDENTITY,
            authorization_reference="workload-identity://kinu-worker",
            capabilities=frozenset({"incident.read"}),
            allowed_resources=("security@demo.kinu.invalid",),
            status=ConnectionStatus.READY,
            region=config.region,
            created_at=now,
            updated_at=now,
        ),
        Application(
            id="app_demo",
            organisation_id=org,
            display_name="Demo application",
            created_at=now,
            updated_at=now,
        ),
        Environment(
            id="env_demo",
            organisation_id=org,
            application_id="app_demo",
            display_name="Demo",
            production=False,
            region=config.region,
            created_at=now,
            updated_at=now,
        ),
        ConsumerService(
            id="service_demo",
            organisation_id=org,
            application_id="app_demo",
            environment_id="env_demo",
            runtime_connection_id="connection_demo_runtime",
            runtime_resource=config.consumer_function,
            display_name="Demo workload",
            created_at=now,
            updated_at=now,
        ),
        credential,
        CredentialGeneration(
            id="generation_demo_initial",
            organisation_id=org,
            credential_id=credential.id,
            state=GenerationState.ACTIVE,
            attempt_id="demo_initial",
            provider_id=provider_id,
            scopes=credential.scopes,
            secret_reference=reference,
            created_at=now,
        ),
        ConsumerBinding(
            id="binding_demo",
            organisation_id=org,
            credential_id=credential.id,
            service_id="service_demo",
            environment_id="env_demo",
            runtime_connection_id="connection_demo_runtime",
            runtime_resource=config.consumer_function,
            runtime_secret_name="KINU_SECRET_VERSION",
            secret_reference=reference,
            current_generation_id="generation_demo_initial",
        ),
        ControlVersion(
            id="controls_demo_initial",
            organisation_id=org,
            credential_id=credential.id,
            number=1,
            definition=definition,
            digest=digest(definition),
            created_by=actor_id,
            created_at=now,
        ),
    ]
    async with Records(database).write(org) as rows:
        try:
            existing = await rows.get(ManagedCredential, org, config.credential_id)
        except ResourceNotFoundError:
            for value in values:
                await rows.add(value, org)
        else:
            if existing.secret_resource != config.secret:
                raise ResourceConflictError("existing demo points to another secret")


async def activate(database: Database, organisation_id: str) -> None:
    now = datetime.now(UTC)
    async with Records(database).write(organisation_id) as rows:
        link = await rows.get(AwsLink, organisation_id, "aws_demo")
        if link.confirmed_at is None:
            await rows.replace(
                link.model_copy(update={"confirmed_at": now, "revision": link.revision + 1}),
                link.revision,
            )
        for name in ("provider", "store", "runtime"):
            connection = await rows.get(Connection, organisation_id, f"connection_demo_{name}")
            if connection.status is not ConnectionStatus.READY:
                await rows.replace(
                    connection.model_copy(
                        update={
                            "status": ConnectionStatus.READY,
                            "last_validated_at": now,
                            "authenticated_at": now,
                            "updated_at": now,
                            "revision": connection.revision + 1,
                        }
                    ),
                    connection.revision,
                )
