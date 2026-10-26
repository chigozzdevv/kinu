from datetime import UTC, datetime

import pytest
from contracts import ControlDefinition, ProtectedAction, RecoveryMode, Stage, StageProof
from policy import REQUIRED_CHECKS, GatePolicy, PolicyViolationError, digest


def test_digest_is_stable_for_parameter_order() -> None:
    first = ProtectedAction(
        id="action_one",
        organisation_id="org_one",
        run_id="run_one",
        kind="revoke",
        resource="sendgrid/key/key-one",
        credential_id="cred_one",
        generation_id="generation_one",
        provider_id="key-one",
        control_version="control_one",
        plan_hash="a" * 64,
        evidence_hash="b" * 64,
        parameters={"reason": "rotation", "force": False},
    )
    second = first.model_copy(update={"parameters": {"force": False, "reason": "rotation"}})
    assert digest(first) == digest(second)


def test_digest_is_stable_for_unordered_policy_values() -> None:
    first = ControlDefinition(
        required_checks=REQUIRED_CHECKS,
        allowed_tools=frozenset({"provider.create", "verification.run"}),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK, RecoveryMode.RETRY}),
        maximum_observation_seconds=1800,
        require_revoke_approval=True,
    )
    payload = first.model_dump(mode="json")
    payload["required_checks"] = {
        key: list(reversed(value)) for key, value in reversed(payload["required_checks"].items())
    }
    payload["allowed_tools"] = list(reversed(payload["allowed_tools"]))
    payload["allowed_recovery_modes"] = list(reversed(payload["allowed_recovery_modes"]))
    second = ControlDefinition.model_validate(payload)
    assert digest(first) == digest(second)


def test_gate_policy_reports_every_missing_check() -> None:
    proof = StageProof(
        run_id="run_one",
        organisation_id="org_one",
        stage=Stage.TRIGGER,
        checks=frozenset({"request-authenticated"}),
        evidence_ids=("evidence_one",),
        actor_id="service_one",
        recorded_at=datetime.now(UTC),
    )
    with pytest.raises(PolicyViolationError) as error:
        GatePolicy().validate(proof)
    assert error.value.missing == frozenset({"source-deduplicated", "lease-held"})
