import asyncio
import hashlib
import io
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from agents.decision import IncidentInput
from connectors.aws import Role
from connectors.base.errors import ConnectorError
from contracts import (
    CreateRunCommand,
    CredentialGeneration,
    GenerationState,
    ManagedCredential,
    RunStatus,
    Stage,
    Trigger,
)
from core.errors import ActiveRunConflictError
from core.storage.database import Database
from core.storage.history import HistoryStore
from core.storage.records import Records
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from demo.config import DemoConfig
from demo.effects import Effects
from demo.mail import Mailbox, MailTask, investigate
from demo.snapshot import loader
from rotation.rotate import Rotation
from rotation.state import Snapshot

pytestmark = pytest.mark.anyio
SECRET = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:kinu-demo-key-ABC123"


async def create(database: Database, config: DemoConfig, suffix: str = "one") -> str:
    result = await RunWorkflow(RunStore(database)).create(
        CreateRunCommand(
            id=f"command_{suffix}",
            organisation_id=config.organisation_id,
            credential_id=config.credential_id,
            control_version="controls_demo_initial",
            trigger=Trigger(
                source="manual",
                kind="manual",
                event_id=f"event_{suffix}",
                actor_id="actor_test",
                reason="Test rotation",
                urgency="routine",
                received_at=datetime.now(UTC),
            ),
        )
    )
    return result.run.id


class Api(Effects):
    def __init__(self, config: DemoConfig, fail: Stage | None = None) -> None:
        self.config, self.fail = config, fail
        self.created, self.promoted, self.observed, self.old_revoked = False, False, False, False
        self.candidate_revoked = False
        self.calls: list[str] = []
        self.target: dict[str, Any] = {}
        self.verify_calls = 0

    async def initial(self, state: Snapshot) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        return {
            "deployments": {state.single.binding.id: {"version": "1", "revision": "revision-one"}},
            "old_uses": 1,
        }

    async def issue(self, state: Snapshot, run_id: str) -> dict[str, Any]:
        self.created = True
        self.calls.append("create")
        identity = hashlib.sha256(f"{self.config.organisation_id}\0{run_id}".encode()).hexdigest()
        self.target = {
            "provider_id": identity[:24],
            "secret_reference": SECRET + "/versions/" + identity,
        }
        return self.target

    async def access(self, state: Snapshot, created: dict[str, Any]) -> dict[str, Any]:
        if self.fail is Stage.STORE:
            raise ConnectorError("consumer-access-denied", "consumer cannot read the candidate")
        return {"ok": True}

    async def deploy(self, state: Snapshot, created: dict[str, Any], run_id: str) -> dict[str, Any]:
        self.calls.append("deploy")
        return {
            "deployments": {
                state.single.binding.id: {
                    "candidate_revision": "2",
                    "rollback_revision": "1",
                    "alias_revision": "revision-one",
                }
            }
        }

    async def verify(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> dict[str, Any]:
        self.verify_calls += 1
        if self.fail is Stage.VERIFY or (self.fail is Stage.COMPLETE and self.verify_calls > 1):
            raise ConnectorError("candidate-invalid", "candidate did not verify")
        self.calls.append("verify")
        return {"ok": True}

    async def promote(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> dict[str, Any]:
        self.promoted = True
        self.calls.append("promote")
        if self.fail is Stage.ROLLOUT:
            raise ConnectorError("response-lost", "promotion response was lost")
        return {"ok": True}

    async def observe(self, state: Snapshot, created: dict[str, Any]) -> dict[str, Any]:
        if self.fail is Stage.OBSERVE:
            raise ConnectorError("old-key-active", "old key usage continued")
        self.observed = True
        self.calls.append("observe")
        return {"old_uses": 1, "observation_seconds": 60, "samples": 3}

    async def revoke(
        self,
        state: Snapshot,
        created: dict[str, Any],
        deployment: dict[str, Any],
        observed: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.promoted and self.observed and observed["samples"] == 3
        self.old_revoked = True
        self.calls.append("revoke")
        return {"old_rejected": True, "replacement_valid": True, "old_secret_retired": True}

    async def rollback(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any] | None
    ) -> bool:
        if self.old_revoked:
            return False
        self.promoted, self.candidate_revoked = False, True
        self.calls.append("rollback")
        return True


async def test_rotation_records_all_stages_and_changes_generation_atomically(
    database: Database, demo: DemoConfig
) -> None:
    run_id = await create(database, demo)
    api = Api(demo)
    run = await Rotation(database, api, loader(database, demo), demo.region).execute(
        demo.organisation_id, run_id, "worker_one"
    )
    assert run.status is RunStatus.COMPLETED
    assert api.calls.index("revoke") > api.calls.index("observe") > api.calls.index("verify")
    history = await HistoryStore(database).get(demo.organisation_id, run_id)
    assert tuple(item.stage for item in history.stages) == tuple(Stage)
    assert all(item.status == "succeeded" for item in history.stages)
    async with Records(database).read() as rows:
        credential = await rows.get(ManagedCredential, demo.organisation_id, demo.credential_id)
        old = await rows.get(CredentialGeneration, demo.organisation_id, "generation_demo_initial")
    assert credential.active_generation_id == run.target_generation_id
    assert old.state is GenerationState.REVOKED
    previous_calls = list(api.calls)
    assert (
        await Rotation(database, api, loader(database, demo), demo.region).execute(
            demo.organisation_id, run_id, "worker_duplicate"
        )
    ).status is RunStatus.COMPLETED
    assert api.calls == previous_calls


@pytest.mark.parametrize("stage", [Stage.STORE, Stage.VERIFY, Stage.ROLLOUT, Stage.OBSERVE])
async def test_failure_preserves_old_key_and_compensates_candidate(
    database: Database, demo: DemoConfig, stage: Stage
) -> None:
    run_id, api = await create(database, demo), Api(demo, stage)
    run = await Rotation(database, api, loader(database, demo), demo.region).execute(
        demo.organisation_id, run_id, "worker_one"
    )
    assert run.status is RunStatus.FAILED
    assert not api.old_revoked and api.candidate_revoked and not api.promoted
    async with Records(database).read() as rows:
        old = await rows.get(CredentialGeneration, demo.organisation_id, "generation_demo_initial")
    assert old.state is GenerationState.ACTIVE
    history = await HistoryStore(database).get(demo.organisation_id, run_id)
    assert history.stages[-1].status == "failed"


async def test_failure_after_retirement_keeps_new_workload_and_holds_credential_lock(
    database: Database, demo: DemoConfig
) -> None:
    run_id, api = await create(database, demo), Api(demo, Stage.COMPLETE)
    run = await Rotation(database, api, loader(database, demo), demo.region).execute(
        demo.organisation_id, run_id, "worker_one"
    )
    assert run.status is RunStatus.CLEANUP
    assert api.old_revoked and api.promoted and not api.candidate_revoked
    with pytest.raises(ActiveRunConflictError):
        await create(database, demo, "two")


async def test_concurrent_deliveries_create_only_one_replacement(
    database: Database, demo: DemoConfig
) -> None:
    run_id, api = await create(database, demo), Api(demo)
    results = await asyncio.gather(
        Rotation(database, api, loader(database, demo), demo.region).execute(
            demo.organisation_id, run_id, "worker_one"
        ),
        Rotation(database, api, loader(database, demo), demo.region).execute(
            demo.organisation_id, run_id, "worker_two"
        ),
        return_exceptions=True,
    )
    assert (
        sum(
            not isinstance(item, BaseException) and item.status is RunStatus.COMPLETED
            for item in results
        )
        == 1
    )
    assert api.calls.count("create") == 1


class Model:
    def __init__(self) -> None:
        self.calls = 0

    async def call(
        self,
        service: str,
        operation: str,
        region: str,
        *,
        role: Role | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        assert (service, operation) == ("lambda", "invoke")
        payload = json.loads(parameters["Payload"])
        IncidentInput.model_validate(payload["input"])
        assert "DEMO FIXTURE" not in str(payload)
        assert set(payload["input"]) == {
            "stage",
            "kind",
            "confidence",
            "candidate_count",
            "policy_allows_rotation",
        }
        self.calls += 1
        return {"Payload": io.BytesIO(b'{"decision":"rotate","reason":"confirmed-exposure"}')}


async def test_email_intake_uses_sanitized_native_assessment_and_deduplicates(
    database: Database, demo: DemoConfig
) -> None:
    task = MailTask(
        id="mail_test",
        organisation_id=demo.organisation_id,
        actor_id="actor_test",
        provider_id="a" * 24,
        received_at=datetime.now(UTC),
    )
    await Mailbox(database).put(task)
    model = Model()
    run_id = await investigate(database, model, demo, task)
    assert run_id is not None
    run = await RunStore(database).get(demo.organisation_id, run_id)
    assert run.trigger.source == "demo-email" and run.credential_id == demo.credential_id
    assert await investigate(database, model, demo, task) == run_id
    assert model.calls == 1


async def test_expired_worker_is_finalized_without_repeating_effects(
    database: Database, demo: DemoConfig
) -> None:
    from datetime import timedelta

    from contracts import StartRunCommand

    run_id, api = await create(database, demo), Api(demo)
    old = datetime.now(UTC) - timedelta(minutes=20)
    workflow = RunWorkflow(RunStore(database), clock=lambda: old)
    await workflow.start(
        StartRunCommand(
            id="start_expired",
            organisation_id=demo.organisation_id,
            run_id=run_id,
            actor_id="actor_test",
            owner_id="worker_gone",
            expected_revision=0,
            expires_at=old + timedelta(minutes=14),
        )
    )
    result = await Rotation(database, api, loader(database, demo), demo.region).execute(
        demo.organisation_id, run_id, "worker_recovery"
    )
    assert result.status is RunStatus.FAILED
    assert result.failure and result.failure.code == "worker-lease-expired"
    assert not api.calls
