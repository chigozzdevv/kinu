import json
from datetime import UTC, datetime

import httpx
import pytest
from connectors.aws.verify import AwsLinkVerifier
from connectors.http import HttpProviderConnector
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConsumerBinding,
    ConsumerService,
    ControlVersion,
    ManagedCredential,
    RunStatus,
    Stage,
)
from core.connection.control import ControlRequest, control
from core.connection.service import AwsSetup, ConnectionService
from core.errors import ResourceNotFoundError
from core.storage.database import Database
from core.storage.history import HistoryStore
from core.storage.records import Records
from demo.config import DemoConfig
from policy import digest
from rotation.effects import Effects
from rotation.queue import Dispatcher
from rotation.rotate import Rotation
from rotation.snapshot import loader
from testkit import make_http_provider_api
from tests.storage.rotation.generic.cloud import Cloud

pytestmark = pytest.mark.anyio
SECRET = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:kinu-demo-key-ABC123"
MANAGEMENT = SECRET.replace("demo-key", "management")
OLD_REF = SECRET + "/versions/" + "1" * 64
AUTH_REF = MANAGEMENT + "/versions/" + "2" * 64
FUNCTION = "arn:aws:lambda:eu-west-2:123456789012:function:kinu-demo-consumer"


async def mapping(
    database: Database, demo: DemoConfig, cloud: Cloud
) -> tuple[ManagedCredential, Connection]:
    service = ConnectionService(
        database,
        AwsLinkVerifier(cloud),
        ("arn:aws:iam::123456789012:role/connector",),
        lambda: datetime.now(UTC),
    )
    prepared = await service.prepare(
        demo.organisation_id,
        AwsSetup(
            account_id="123456789012",
            region="eu-west-2",
            display_name="Selected AWS",
            secret_arns=(SECRET, MANAGEMENT),
            function_arns=(FUNCTION,),
        ),
    )
    connection = await service.confirm(demo.organisation_id, prepared.connection.id, 0)
    async with Records(database).write(demo.organisation_id) as rows:
        credential = await rows.get(ManagedCredential, demo.organisation_id, demo.credential_id)
        provider = await rows.get(Connection, demo.organisation_id, credential.connection_id)
        bindings = await rows.match(
            ConsumerBinding, demo.organisation_id, {"credential_id": credential.id}
        )
        control_version = await rows.get(
            ControlVersion, demo.organisation_id, credential.control_version
        )
        api = make_http_provider_api().model_copy(update={"supports_overlap": True})
        api = api.model_copy(
            update={
                "list_credentials": api.list_credentials.model_copy(
                    update={
                        "metadata_fields": {
                            "kind": "kind",
                            "status": "status",
                            "scopes": "scopes",
                            "disabled": "disabled",
                        }
                    }
                )
            }
        )
        await rows.replace(
            provider.model_copy(
                update={
                    "authorization": ConnectionAuthorization.API_KEY,
                    "authorization_reference": AUTH_REF,
                    "http": api,
                    "rpc": None,
                    "revision": provider.revision + 1,
                }
            ),
            provider.revision,
        )
        definition = control_version.definition.model_copy(
            update={
                "require_generation_telemetry": False,
                "allowed_tools": frozenset(
                    {
                        "provider.createCredential",
                        "provider.revokeCredential",
                        "provider.testCredential",
                        "runtime.deployCandidate",
                        "runtime.shiftTraffic",
                        "runtime.rollback",
                        "secretStore.disableVersion",
                    }
                ),
                "required_checks": {
                    stage: checks.difference({"telemetry-healthy", "old-use-clear"})
                    for stage, checks in control_version.definition.required_checks.items()
                },
            }
        )
        control_version = control_version.model_copy(
            update={
                "id": "controls_generic",
                "number": 2,
                "definition": definition,
                "digest": digest(definition),
            }
        )
        await rows.add(control_version, demo.organisation_id)
        credential = credential.model_copy(
            update={
                "secret_store_connection_id": connection.id,
                "control_version": control_version.id,
                "revision": credential.revision + 1,
            }
        )
        await rows.replace(credential, credential.revision - 1)
        binding = bindings[0]
        consumer = await rows.get(ConsumerService, demo.organisation_id, binding.service_id)
        await rows.replace(
            consumer.model_copy(
                update={"runtime_connection_id": connection.id, "revision": consumer.revision + 1}
            ),
            consumer.revision,
        )
        await rows.replace(
            binding.model_copy(
                update={
                    "runtime_connection_id": connection.id,
                    "runtime_secret_name": "PROVIDER_REF",
                    "revision": binding.revision + 1,
                }
            ),
            binding.revision,
        )
    return credential, connection


@pytest.mark.parametrize("fail", [False, True])
async def test_selected_resources_rotate_or_restore_without_exposing_values(
    database: Database, demo: DemoConfig, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    from contracts import CreateRunCommand, Trigger
    from core.storage.runs import RunStore
    from core.workflow import RunWorkflow

    cloud = Cloud(fail)
    credential, _connection = await mapping(database, demo, cloud)
    run = (
        await RunWorkflow(RunStore(database)).create(
            CreateRunCommand(
                id="cmd_generic",
                organisation_id=demo.organisation_id,
                credential_id=credential.id,
                control_version=credential.control_version,
                trigger=Trigger(
                    source="manual",
                    kind="manual",
                    event_id="event_generic",
                    actor_id="operator_one",
                    reason="Test selected mapping",
                    urgency="routine",
                    received_at=datetime.now(UTC),
                ),
            )
        )
    ).run
    effects = Effects(cloud, database)
    async with httpx.AsyncClient(transport=httpx.MockTransport(cloud.http)) as client:
        effects.provider = HttpProviderConnector(effects.provider._secrets, client)

        async def sleep(seconds: float) -> None:
            assert seconds == 20

        monkeypatch.setattr("rotation.effects.sleep", sleep)
        result = await Rotation(database, effects, loader(database), "eu-west-2").execute(
            demo.organisation_id, run.id, "worker_generic"
        )
    assert result.status is (RunStatus.FAILED if fail else RunStatus.COMPLETED)
    assert cloud.alias == ("1" if fail else "2")
    assert cloud.provider["a" * 24]["active"] is fail
    assert cloud.provider["replacement"]["active"] is not fail
    assert cloud.latest["UNRELATED"] == "keep"
    history = await HistoryStore(database).get(demo.organisation_id, run.id)
    assert "fixture-" not in history.model_dump_json()
    if not fail:
        observed = next(item for item in history.stages if item.stage is Stage.OBSERVE)
        assert "telemetry-healthy" not in observed.checks
    with pytest.raises(ResourceNotFoundError):
        await Dispatcher(database, JobQueue()).require_scope("org_other", credential.id)


async def test_control_uses_saved_scope_and_returns_no_environment_values(
    database: Database, demo: DemoConfig
) -> None:
    cloud = Cloud(False)
    _, connection = await mapping(database, demo, cloud)
    response = await control(
        database,
        cloud,
        ControlRequest(
            operation="runtimes",
            organisation_id=demo.organisation_id,
            connection_id=connection.id,
            revision=connection.revision,
        ),
    )
    assert response[0]["secret_bindings"] == [
        {"name": "PROVIDER_REF", "secret": SECRET, "version": "1" * 64}
    ]
    assert "keep" not in json.dumps(response)
    with pytest.raises(ResourceNotFoundError):
        await control(
            database,
            cloud,
            ControlRequest(
                operation="runtimes",
                organisation_id="org_other",
                connection_id=connection.id,
                revision=connection.revision,
            ),
        )


class JobQueue:
    async def rotation(self, organisation_id: str, run_id: str) -> None:
        pass
