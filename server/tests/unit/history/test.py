from datetime import UTC, datetime, timedelta

from contracts import (
    Approval,
    ApprovalDecision,
    RotationRun,
    Stage,
    StageExecutionResult,
    StageExecutionStatus,
    Trigger,
)
from core.history import _activity

NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


def test_stage_history_projects_only_safe_agent_and_browser_fields() -> None:
    result = StageExecutionResult(
        id="stage_one",
        organisation_id="org_one",
        run_id="run_one",
        stage=Stage.PLAN,
        status=StageExecutionStatus.SUCCEEDED,
        checks=frozenset({"plan-bound"}),
        evidence_ids=("evidence_one",),
        output={
            "agent": {
                "decision": "plan",
                "strategy": "overlap",
                "rationale": "Keep the previous generation until verification passes.",
            },
            "secret_reference": "secret-value",
            "browser_activity": [
                {
                    "step_id": "step_one",
                    "objective": "Open the approved credential page.",
                    "operation": "navigate",
                    "outcome": "Approved page opened",
                    "secret_reference": "secret-value",
                }
            ],
        },
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=2),
    )
    public = _activity(result).model_dump_json()
    assert "Keep the previous generation" in public
    assert "Open the approved credential page" in public
    assert "secret-value" not in public
    assert "secret_reference" not in public


def test_trigger_history_uses_the_trigger_story_not_internal_gate_checks() -> None:
    run = RotationRun(
        id="run_one",
        organisation_id="org_one",
        credential_id="credential_one",
        trigger=Trigger(
            source="github-secret-scanning",
            kind="credential-exposure-detected",
            event_id="alert_one",
            actor_id="ingestion_one",
            reason="A credential was found in a public repository.",
            urgency="emergency",
            received_at=NOW,
        ),
        control_version="control_one",
        created_at=NOW,
        updated_at=NOW,
    )
    result = StageExecutionResult(
        id="stage_trigger",
        organisation_id="org_one",
        run_id=run.id,
        stage=Stage.TRIGGER,
        status=StageExecutionStatus.SUCCEEDED,
        checks=frozenset({"request-authenticated", "source-deduplicated", "lease-held"}),
        evidence_ids=("evidence_one",),
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    public = _activity(result, run)
    assert public.summary == "Exposure alert started rotation"
    assert [(item.label, item.value) for item in public.details] == [
        ("Configured trigger", "GitHub Secret Scanning"),
        ("Reason", "A credential was found in a public repository."),
    ]


def test_preflight_history_turns_empty_agent_findings_into_a_clear_action() -> None:
    result = StageExecutionResult(
        id="stage_preflight",
        organisation_id="org_one",
        run_id="run_one",
        stage=Stage.PREFLIGHT,
        status=StageExecutionStatus.SUCCEEDED,
        checks=frozenset({"provider-ready", "consumers-known"}),
        evidence_ids=("evidence_one",),
        output={
            "connections": ["provider_one", "secret_store_one", "runtime_one"],
            "bindings": ["binding_one"],
            "agent": {
                "declared_consumers": ["service_one"],
                "observed_consumers": ["service_one"],
                "missing_inventory": [],
                "stale_inventory": [],
                "conclusion": "Inventory is aligned.",
            },
        },
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    public = _activity(result)
    assert public.summary == "Ready to rotate"
    assert public.agent_decisions[0].decision == "Consumer inventory confirmed"
    assert (
        public.agent_decisions[0].explanation
        == "No missing or stale mappings were found across 1 declared consumer."
    )


def test_store_history_describes_verification_instead_of_repeating_capture() -> None:
    result = StageExecutionResult(
        id="stage_store",
        organisation_id="org_one",
        run_id="run_one",
        stage=Stage.STORE,
        status=StageExecutionStatus.SUCCEEDED,
        checks=frozenset({"secret-stored", "consumer-accessible"}),
        evidence_ids=("evidence_store",),
        output={
            "secret_store": "Secret store · Production",
            "secret_reference": "secret://acme/provider/versions/8",
            "consumer_access": [{"binding_id": "binding_one"}],
        },
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    public = _activity(result)
    assert public.summary == "Secret verified"
    assert [(item.label, item.value) for item in public.details] == [
        ("Secret store", "Secret store"),
        ("Version", "8 enabled"),
        ("Consumer access", "Confirmed for 1"),
    ]


def test_approval_history_reflects_the_current_decision() -> None:
    approval = Approval(
        id="approval_one",
        organisation_id="org_one",
        run_id="run_one",
        action_id="action_one",
        action_digest="a" * 64,
        plan_hash="b" * 64,
        evidence_hash="c" * 64,
        generation_id="generation_one",
        requested_by="coordinator_one",
        capability_hash="d" * 64,
        decision=ApprovalDecision.APPROVED,
        approver_id="actor_one",
        expires_at=NOW + timedelta(hours=1),
        created_at=NOW,
        decided_at=NOW + timedelta(minutes=1),
        consumed_at=NOW + timedelta(minutes=2),
    )
    result = StageExecutionResult(
        id="stage_approval",
        organisation_id="org_one",
        run_id="run_one",
        stage=Stage.APPROVAL,
        status=StageExecutionStatus.SUCCEEDED,
        checks=frozenset({"approval-valid"}),
        evidence_ids=("evidence_approval",),
        output={"approvals": [{"approval_id": approval.id, "action_id": approval.action_id}]},
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    public = _activity(result, approvals=(approval,))
    assert public.summary == "Revocation approved"
    assert public.details == ()


def test_browser_failure_history_exposes_the_safe_provider_reason() -> None:
    result = StageExecutionResult(
        id="stage_create",
        organisation_id="org_one",
        run_id="run_one",
        stage=Stage.CREATE,
        status=StageExecutionStatus.FAILED,
        output={
            "browser_error": {
                "status_code": 409,
                "code": "checkpoint-mismatch",
                "message": "approved page was not reached",
            }
        },
        reason="BrowserWorkerError: approved page was not reached",
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    public = _activity(result)
    assert public.summary == "Browser step needs attention"
    assert [(item.label, item.value) for item in public.details] == [
        ("Provider response", "approved page was not reached")
    ]
