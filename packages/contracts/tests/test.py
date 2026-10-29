from datetime import UTC, datetime, timedelta

import pytest
from contracts import (
    Approval,
    ApprovalDecision,
    BrowserActionKind,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    ComputerUseActivity,
    ComputerUseActivityPhase,
    ComputerUseActivityStatus,
    ControlDefinition,
    ControlVersion,
    EventKind,
    MutationMode,
    MutationSemantics,
    OutboxEvent,
    PageCheckpoint,
    PlaybookDraft,
    PlaybookEffect,
    PlaybookStep,
    RecoveryMode,
    RotationPlan,
    RotationStrategy,
    RunEvent,
    RunStatus,
    SecureField,
    Selector,
    SelectorKind,
    Stage,
    StepOutput,
)
from policy import REQUIRED_CHECKS, digest
from pydantic import ValidationError

NOW = datetime.now(UTC)
DIGEST = "a" * 64


def test_native_mutation_requires_provider_token() -> None:
    with pytest.raises(ValidationError, match="provider token"):
        MutationSemantics(mode=MutationMode.NATIVE)


def test_compensatable_mutation_requires_cleanup() -> None:
    with pytest.raises(ValidationError, match="compensation action"):
        MutationSemantics(mode=MutationMode.COMPENSATABLE)


def test_rollout_must_be_ordered_and_complete() -> None:
    with pytest.raises(ValidationError, match="unique and increasing"):
        RotationPlan(
            id="plan_one",
            organisation_id="org_one",
            run_id="run_one",
            credential_id="cred_one",
            control_version="policy_one",
            browser_playbook_version=None,
            strategy=RotationStrategy.PARALLEL,
            target_scopes=frozenset({"mail.send"}),
            consumer_ids=("service_one",),
            rollout=(25, 5, 100),
            observation_seconds=60,
            recovery_ids={Stage.DEPLOY: "recovery_one"},
        )


def test_approval_consumption_requires_approved_decision() -> None:
    with pytest.raises(ValidationError, match="only an approved action"):
        Approval(
            id="approval_one",
            organisation_id="org_one",
            run_id="run_one",
            action_id="action_one",
            action_digest=DIGEST,
            plan_hash=DIGEST,
            evidence_hash=DIGEST,
            generation_id="generation_one",
            requested_by="service_one",
            capability_hash=DIGEST,
            decision=ApprovalDecision.REJECTED,
            approver_id="user_one",
            expires_at=NOW + timedelta(minutes=10),
            created_at=NOW,
            decided_at=NOW,
            consumed_at=NOW,
        )


def test_contracts_are_immutable() -> None:
    mutation = MutationSemantics(
        mode=MutationMode.COMPENSATABLE, compensation="revoke-attributable-orphan"
    )
    with pytest.raises(ValidationError, match="frozen"):
        mutation.compensation = "retry"


def test_computer_use_input_records_the_playbook_task_and_browser_evidence() -> None:
    activity = ComputerUseActivity(
        id="activity_one",
        organisation_id="org_one",
        session_id="browser_one",
        run_id="run_one",
        step_id="step_one",
        stage=Stage.CREATE,
        turn=1,
        phase=ComputerUseActivityPhase.INPUT,
        status=ComputerUseActivityStatus.SENT,
        effect=PlaybookEffect.CREATE_CREDENTIAL,
        prompt="Create the replacement credential.",
        instruction="Do not handle secrets.",
        image_reference="storage://evidence/input#1",
        image_digest="a" * 64,
        recorded_at=NOW,
    )
    public = activity.model_dump_json()
    assert "Create the replacement credential" in public
    assert "Do not handle secrets" in public
    assert "storage://evidence/input#1" in public


def test_computer_use_activity_keeps_returned_function_arguments() -> None:
    activity = ComputerUseActivity.model_validate(
        {
            "id": "activity_one",
            "organisation_id": "org_one",
            "session_id": "browser_one",
            "run_id": "run_one",
            "step_id": "step_one",
            "stage": "create",
            "turn": 1,
            "phase": "proposal",
            "status": "proposed",
            "action": "click",
            "target": "Create credential",
            "arguments": {"x": 500, "y": 400},
            "intent": "Open the approved form",
            "recorded_at": NOW,
        }
    )
    assert activity.arguments == {"x": 500, "y": 400}


def test_outbox_lease_must_be_complete() -> None:
    event = RunEvent(
        id="event_one",
        organisation_id="org_one",
        run_id="run_one",
        credential_id="cred_one",
        kind=EventKind.RUN_CREATED,
        revision=0,
        stage=Stage.TRIGGER,
        status=RunStatus.PENDING,
        actor_id="service_one",
        occurred_at=NOW,
    )
    with pytest.raises(ValidationError, match="owner and expiry"):
        OutboxEvent(event=event, available_at=NOW, lease_owner="publisher_one")


def test_outbox_publication_requires_provider_receipt() -> None:
    event = RunEvent(
        id="event_one",
        organisation_id="org_one",
        run_id="run_one",
        credential_id="cred_one",
        kind=EventKind.RUN_CREATED,
        revision=0,
        stage=Stage.TRIGGER,
        status=RunStatus.PENDING,
        actor_id="service_one",
        occurred_at=NOW,
    )
    with pytest.raises(ValidationError, match="message ID"):
        OutboxEvent(event=event, available_at=NOW, published_at=NOW)


def test_legacy_playbook_stage_migrates_to_plan() -> None:
    assert Stage("playbook") is Stage.PLAN


def test_browser_playbook_requires_a_login_url_pattern() -> None:
    with pytest.raises(ValidationError, match="login URL"):
        PlaybookDraft(
            name="Vendor rotation",
            platform="vendor",
            allowed_domains=("vendor.example.com",),
            steps=(
                PlaybookStep(
                    id="step_one",
                    stage=Stage.CREATE,
                    effect=PlaybookEffect.CREATE_CREDENTIAL,
                    tool="browser.secure-capture",
                    operation="capture",
                    objective="Submit the credential creation form",
                    selectors=(Selector(kind=SelectorKind.TEST_ID, value="create-api-key"),),
                    checkpoint=PageCheckpoint(url_pattern="https://vendor.example.com/keys"),
                    secure_field=SecureField(
                        name="api_key",
                        selector=Selector(kind=SelectorKind.TEST_ID, value="new-api-key"),
                        provider_id_selector=Selector(
                            kind=SelectorKind.TEST_ID, value="new-key-id"
                        ),
                    ),
                    evidence_checks=frozenset({"captured"}),
                ),
            ),
        )


def test_browser_playbook_requires_secure_capture() -> None:
    with pytest.raises(ValidationError, match="credential creation"):
        PlaybookDraft(
            name="Vendor rotation",
            platform="vendor",
            allowed_domains=("vendor.example.com",),
            login_url_pattern="https://vendor.example.com/login",
            steps=(
                PlaybookStep(
                    id="step_one",
                    stage=Stage.CREATE,
                    effect=PlaybookEffect.CREATE_CREDENTIAL,
                    tool="browser.click",
                    operation="click",
                    objective="Click the Create control",
                    selectors=(Selector(kind=SelectorKind.ROLE, value="button", name="Create"),),
                    checkpoint=PageCheckpoint(url_pattern="https://vendor.example.com/keys"),
                    evidence_checks=frozenset({"page-confirmed"}),
                ),
            ),
        )


def test_browser_navigation_requires_a_selector_free_url_step() -> None:
    checkpoint = PageCheckpoint(url_pattern="https://vendor.example.com/keys")
    with pytest.raises(ValidationError, match="must not declare a selector"):
        PlaybookStep(
            id="open_keys",
            stage=Stage.CREATE,
            tool="browser.navigate",
            operation="navigate",
            objective="Open the API keys page",
            parameters={"url": "https://vendor.example.com/keys"},
            selectors=(Selector(kind=SelectorKind.CSS, value="body"),),
            checkpoint=checkpoint,
            evidence_checks=frozenset({"page-confirmed"}),
        )
    with pytest.raises(ValidationError, match="requires a URL parameter"):
        PlaybookStep(
            id="open_keys",
            stage=Stage.CREATE,
            tool="browser.navigate",
            operation="navigate",
            objective="Open the API keys page",
            checkpoint=checkpoint,
            evidence_checks=frozenset({"page-confirmed"}),
        )


def test_secure_capture_action_cannot_target_the_generated_credential() -> None:
    output = Selector(kind=SelectorKind.TEST_ID, value="new-api-key")
    with pytest.raises(ValidationError, match="credential creation control"):
        PlaybookStep(
            id="create_key",
            stage=Stage.CREATE,
            effect=PlaybookEffect.CREATE_CREDENTIAL,
            tool="browser.secure-capture",
            operation="capture",
            objective="Submit the credential creation form",
            selectors=(output,),
            checkpoint=PageCheckpoint(url_pattern="https://vendor.example.com/keys"),
            secure_field=SecureField(
                name="api_key",
                selector=output,
                provider_id_selector=Selector(kind=SelectorKind.TEST_ID, value="new-key-id"),
            ),
            evidence_checks=frozenset({"captured"}),
        )


def test_browser_playbook_cannot_embed_approval_policy() -> None:
    selector = Selector(kind=SelectorKind.TEST_ID, value="credential-control")
    checkpoint = PageCheckpoint(url_pattern="https://vendor.example.com/keys")
    with pytest.raises(ValidationError, match="credential controls protect operations"):
        PlaybookDraft(
            name="Vendor rotation",
            platform="vendor",
            allowed_domains=("vendor.example.com",),
            login_url_pattern="https://vendor.example.com/login",
            steps=(
                PlaybookStep(
                    id="create_key",
                    stage=Stage.CREATE,
                    effect=PlaybookEffect.CREATE_CREDENTIAL,
                    tool="browser.secure-capture",
                    operation="capture",
                    objective="Submit the credential creation form",
                    selectors=(Selector(kind=SelectorKind.TEST_ID, value="create-credential"),),
                    checkpoint=checkpoint,
                    protected=True,
                    secure_field=SecureField(
                        name="api_key",
                        selector=selector,
                        provider_id_selector=Selector(
                            kind=SelectorKind.TEST_ID, value="credential-id"
                        ),
                    ),
                    evidence_checks=frozenset({"captured"}),
                ),
                PlaybookStep(
                    id="revoke_key",
                    stage=Stage.REVOKE,
                    effect=PlaybookEffect.REVOKE_CREDENTIAL,
                    tool="browser.revokeCredential",
                    operation="revoke",
                    objective="Revoke the prior credential",
                    selectors=(selector,),
                    checkpoint=checkpoint,
                    evidence_checks=frozenset({"revoked"}),
                ),
            ),
        )


def test_browser_playbook_rejects_unprotected_credential_creation() -> None:
    selector = Selector(kind=SelectorKind.TEST_ID, value="credential")
    with pytest.raises(ValidationError, match="credential creation"):
        PlaybookStep(
            id="create_key",
            stage=Stage.CREATE,
            effect=PlaybookEffect.CREATE_CREDENTIAL,
            tool="browser.click",
            operation="create",
            objective="Create the replacement credential",
            selectors=(selector,),
            checkpoint=PageCheckpoint(url_pattern="https://vendor.example.com/keys"),
            evidence_checks=frozenset({"created"}),
        )


def test_browser_playbook_requires_a_typed_revocation_action() -> None:
    with pytest.raises(ValidationError, match="protected browser revoke tool"):
        PlaybookStep(
            id="revoke_key",
            stage=Stage.REVOKE,
            effect=PlaybookEffect.REVOKE_CREDENTIAL,
            tool="browser.click",
            operation="revoke",
            objective="Revoke the previous credential",
            selectors=(Selector(kind=SelectorKind.TEST_ID, value="revoke"),),
            checkpoint=PageCheckpoint(url_pattern="https://vendor.example.com/keys"),
            evidence_checks=frozenset({"revoked"}),
        )


def test_browser_playbook_rejects_secret_output_extraction() -> None:
    selector = Selector(kind=SelectorKind.TEST_ID, value="credential")
    checkpoint = PageCheckpoint(url_pattern="https://vendor.example.com/keys")
    with pytest.raises(ValidationError, match="cannot extract step outputs"):
        PlaybookDraft(
            name="Vendor rotation",
            platform="vendor",
            allowed_domains=("vendor.example.com",),
            login_url_pattern="https://vendor.example.com/login",
            steps=(
                PlaybookStep(
                    id="create_key",
                    stage=Stage.CREATE,
                    effect=PlaybookEffect.CREATE_CREDENTIAL,
                    tool="browser.secure-capture",
                    operation="capture",
                    objective="Submit the credential creation form",
                    selectors=(Selector(kind=SelectorKind.TEST_ID, value="create-credential"),),
                    checkpoint=checkpoint,
                    secure_field=SecureField(
                        name="api_key",
                        selector=selector,
                        provider_id_selector=Selector(
                            kind=SelectorKind.TEST_ID, value="credential-id"
                        ),
                    ),
                    outputs=(StepOutput(name="secret", selector=selector),),
                    evidence_checks=frozenset({"captured"}),
                ),
                PlaybookStep(
                    id="revoke_key",
                    stage=Stage.REVOKE,
                    effect=PlaybookEffect.REVOKE_CREDENTIAL,
                    tool="browser.revokeCredential",
                    operation="revoke",
                    objective="Revoke the previous credential",
                    selectors=(Selector(kind=SelectorKind.TEST_ID, value="revoke"),),
                    checkpoint=checkpoint,
                    evidence_checks=frozenset({"revoked"}),
                ),
            ),
        )


def test_secure_capture_session_requires_both_barriers() -> None:
    with pytest.raises(ValidationError, match="barriers"):
        BrowserSession(
            id="session_one",
            organisation_id="org_one",
            run_id="run_one",
            playbook_id="playbook_one",
            playbook_version="playbook_one",
            provider_connection_id="provider_one",
            secret_store_connection_id="secret_store_one",
            secret_resource="secret://project-one/key",
            status=BrowserStatus.CAPTURING,
            policy=BrowserPolicy(
                allowed_domains=("vendor.example.com",),
                allowed_actions=frozenset({BrowserActionKind.CLICK}),
            ),
            fencing_token=1,
            model_paused=False,
            recording_paused=True,
            created_at=NOW,
            updated_at=NOW,
            expires_at=NOW + timedelta(minutes=30),
        )


def test_controls_require_all_stage_checks() -> None:
    definition = ControlDefinition(
        required_checks=REQUIRED_CHECKS,
        allowed_tools=frozenset({"provider.create", "verification.run"}),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=1800,
        require_revoke_approval=True,
    )
    version = ControlVersion(
        id="control_version_one",
        organisation_id="org_one",
        credential_id="credential_one",
        number=1,
        definition=definition,
        digest=digest(definition),
        created_by="admin_one",
        created_at=NOW,
    )
    assert version.credential_id == "credential_one"
    with pytest.raises(ValidationError, match="at least 12 items"):
        ControlDefinition(
            required_checks={Stage.TRIGGER: REQUIRED_CHECKS[Stage.TRIGGER]},
            allowed_tools=frozenset({"provider.create", "verification.run"}),
            allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
            maximum_observation_seconds=1800,
        )


def test_controls_can_advance_without_human_revocation_approval() -> None:
    checks = dict(REQUIRED_CHECKS)
    checks[Stage.PREFLIGHT] = checks[Stage.PREFLIGHT].difference({"approvers-known"})
    checks[Stage.APPROVAL] = frozenset({"approval-not-required", "evidence-current"})
    definition = ControlDefinition(
        required_checks=checks,
        allowed_tools=frozenset({"provider.revoke", "verification.run"}),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=1800,
        require_revoke_approval=False,
    )
    assert definition.protected_tools == frozenset()
    assert definition.required_checks[Stage.APPROVAL] == frozenset(
        {"approval-not-required", "evidence-current"}
    )
    required_checks = dict(checks)
    required_checks[Stage.PREFLIGHT] = REQUIRED_CHECKS[Stage.PREFLIGHT]
    with pytest.raises(ValidationError, match="approval-valid"):
        ControlDefinition(
            required_checks=required_checks,
            allowed_tools=frozenset({"provider.revoke", "verification.run"}),
            protected_tools=frozenset({"provider.revoke"}),
            allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
            maximum_observation_seconds=1800,
            require_revoke_approval=True,
        )
