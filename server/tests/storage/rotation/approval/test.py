from datetime import UTC, datetime

import httpx
import pytest
from contracts import (
    Approval,
    ApprovalDecision,
    CreateRunCommand,
    MemberRole,
    RunStatus,
    Stage,
    Trigger,
)
from core.account import AccountService
from core.account.store import AccountStore
from core.approval import ApprovalService
from core.approval.store import ApprovalStore
from core.auth import AuthenticatedIdentity
from core.errors import ApprovalError
from core.inventory import InventoryService
from core.storage.database import Database
from core.storage.history import HistoryStore
from core.storage.records import Records
from core.storage.runs import RunStore
from core.workflow import RunWorkflow
from hosting.mapping import Mappings
from hosting.proof import Proof
from rotation.approval import RevocationApprovals
from rotation.hosting import Effects
from rotation.rotate import Rotation
from rotation.snapshot import loader
from tests.storage.hosting.rotation.fixture import Platform
from tests.storage.hosting.rotation.test import setup

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "decision",
    [ApprovalDecision.APPROVED, ApprovalDecision.REJECTED, ApprovalDecision.MORE_EVIDENCE],
)
async def test_rotation_pauses_for_another_administrator_and_rechecks_before_retirement(
    database: Database,
    identity: AuthenticatedIdentity,
    monkeypatch: pytest.MonkeyPatch,
    decision: ApprovalDecision,
) -> None:
    platform = Platform()
    async with httpx.AsyncClient(transport=httpx.MockTransport(platform.response)) as http:
        hosts, versions, provider, credential = await setup(database, identity, http)
        org = credential.organisation_id
        accounts = AccountService(
            AccountStore(database, lambda: datetime.now(UTC)), lambda: datetime.now(UTC)
        )
        approver = AuthenticatedIdentity(
            subject="approver_one",
            issuer=identity.issuer,
            email="approver@example.com",
            email_verified=True,
        )
        await accounts.invite(org, identity, approver.email or "", MemberRole.ADMINISTRATOR)
        await accounts.session(approver)
        previous = await hosts.inventory.get_control_version(
            org, credential.id, credential.control_version
        )
        preferences = Mappings.preferences(previous).model_copy(
            update={"require_revoke_approval": True}
        )
        credential, _ = await InventoryService(hosts.inventory).update_controls(
            org,
            credential.id,
            credential.revision,
            "controls_approval",
            preferences,
            identity.actor_id,
        )
        run = (
            await RunWorkflow(RunStore(database)).create(
                CreateRunCommand(
                    id="command_approval",
                    organisation_id=org,
                    credential_id=credential.id,
                    control_version=credential.control_version,
                    trigger=Trigger(
                        source="manual",
                        kind="manual",
                        event_id="approval_fixture",
                        actor_id=identity.actor_id,
                        reason="Require revocation approval",
                        urgency="routine",
                        received_at=datetime.now(UTC),
                    ),
                )
            )
        ).run

        async def sleep(seconds: float) -> None:
            assert seconds == 20

        monkeypatch.setattr("rotation.hosting.sleep", sleep)
        rotation = Rotation(
            database, Effects(hosts, versions, provider, Proof(http)), loader(database), "global"
        )
        rotation.approvals = RevocationApprovals(
            database, versions.vault, rotation.effects, rotation.audit
        )
        paused = await rotation.execute(org, run.id, "worker_one")
        assert paused.status is RunStatus.PAUSED and paused.stage is Stage.APPROVAL
        assert (
            not platform.revoked
            and platform.keys["old"]["active"]
            and platform.keys["new"]["active"]
        )
        async with Records(database).read() as rows:
            approvals = await rows.match(Approval, org, {"run_id": run.id})
        assert len(approvals) == 1
        approval = approvals[0]
        service = ApprovalService(ApprovalStore(database), lambda: datetime.now(UTC))
        with pytest.raises(ApprovalError, match="another operator"):
            await service.decide(
                org, approval.id, approval.revision, ApprovalDecision.APPROVED, identity.actor_id
            )
        await service.decide(org, approval.id, approval.revision, decision, approver.actor_id)
        result = await rotation.execute(org, run.id, "worker_two")
        if decision is ApprovalDecision.MORE_EVIDENCE:
            assert result.status is RunStatus.PAUSED and not platform.revoked
            async with Records(database).read() as rows:
                approvals = await rows.match(Approval, org, {"run_id": run.id})
            refreshed = next(item for item in approvals if item.id != approval.id)
            assert refreshed.evidence_hash != approval.evidence_hash
            assert (
                await HistoryStore(database).approval_evidence(org, refreshed.id)
            ).evidence_hash == refreshed.evidence_hash
            await service.decide(
                org, refreshed.id, refreshed.revision, ApprovalDecision.APPROVED, approver.actor_id
            )
            result = await rotation.execute(org, run.id, "worker_three")
        if decision is ApprovalDecision.REJECTED:
            assert result.status is RunStatus.FAILED and platform.revoked == ["new"]
        else:
            assert result.status is RunStatus.COMPLETED, result.model_dump_json()
            assert platform.revoked == ["old"]
            assert (
                await database.collection("execution_reviews").count_documents(
                    {"organisation_id": org}
                )
                >= 1
            )
