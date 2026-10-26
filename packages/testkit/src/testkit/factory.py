from datetime import UTC, datetime

from contracts import (
    ControlDefinition,
    ControlVersion,
    HttpAuth,
    HttpAuthScheme,
    HttpOperation,
    HttpProviderApi,
    RecoveryMode,
    RotationRun,
    Stage,
    StageProof,
    Trigger,
)
from policy import REQUIRED_CHECKS, GatePolicy, digest


def make_http_provider_api(
    base_url: str = "https://provider.example/v3",
    scheme: HttpAuthScheme = HttpAuthScheme.BEARER,
) -> HttpProviderApi:
    return HttpProviderApi(
        base_url=base_url,
        auth=HttpAuth(scheme=scheme),
        list_credentials=HttpOperation(
            method="GET",
            path="/api_keys",
            success_statuses=(200,),
            query={"limit": "500"},
            list_items="result",
            provider_id_field="api_key_id",
            name_field="name",
            metadata_fields={"scopes": "scopes"},
        ),
        create_credential=HttpOperation(
            method="POST",
            path="/api_keys",
            success_statuses=(201,),
            body={"name": "${name}", "scopes": "${scopes}"},
            provider_id_field="api_key_id",
            secret_field="api_key",
            name_field="name",
        ),
        revoke_credential=HttpOperation(
            method="DELETE",
            path="/api_keys/{provider_id}",
            success_statuses=(204,),
        ),
        test_credential=HttpOperation(
            method="GET",
            path="/scopes",
            success_statuses=(200,),
            provider_id_field="id",
        ),
        credential_auth=HttpAuth(scheme=HttpAuthScheme.BEARER),
    )


def make_control_version(
    organisation_id: str = "org_one",
    version_id: str = "control_one",
    credential_id: str = "cred_one",
    now: datetime | None = None,
) -> ControlVersion:
    current = now or datetime.now(UTC)
    definition = ControlDefinition(
        required_checks=REQUIRED_CHECKS,
        allowed_tools=frozenset({"provider.create", "verification.run"}),
        allowed_recovery_modes=frozenset({RecoveryMode.ROLLBACK}),
        maximum_observation_seconds=1800,
        require_revoke_approval=True,
    )
    return ControlVersion(
        id=version_id,
        organisation_id=organisation_id,
        credential_id=credential_id,
        number=1,
        definition=definition,
        digest=digest(definition),
        created_by="admin_one",
        created_at=current,
    )


def make_run(now: datetime | None = None) -> RotationRun:
    current = now or datetime.now(UTC)
    return RotationRun(
        id="run_one",
        organisation_id="org_one",
        credential_id="cred_one",
        trigger=Trigger(
            source="schedule",
            event_id="event-one",
            actor_id="service_one",
            reason="routine rotation",
            urgency="routine",
            received_at=current,
        ),
        control_version="control_one",
        created_at=current,
        updated_at=current,
    )


def make_proof(stage: Stage, now: datetime | None = None) -> StageProof:
    current = now or datetime.now(UTC)
    return StageProof(
        run_id="run_one",
        organisation_id="org_one",
        stage=stage,
        checks=GatePolicy().checks(stage),
        evidence_ids=(f"evidence_{stage.value}",),
        actor_id="service_one",
        recorded_at=current,
    )
