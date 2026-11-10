import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from agents.decision import ApiInput, Decision
from contracts import (
    RotationPlan,
    RotationRun,
    RotationStrategy,
    RuntimeDeployment,
    Stage,
    StageBindings,
)
from core.errors import ResourceConflictError
from core.storage.database import Database
from policy import digest

from rotation import generations
from rotation.journal import Journal
from rotation.ports import Effects, Progress
from rotation.state import Snapshot

Planner = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Assessor = Callable[[str, ApiInput], Awaitable[Decision]]

PLAN_STAGES = (
    "create",
    "store",
    "deploy",
    "verify",
    "rollout",
    "observe",
    "approval",
    "revoke",
    "complete",
)


def stable(prefix: str, run_id: str) -> str:
    return prefix + "_" + hashlib.sha256(run_id.encode()).hexdigest()[:32]


class Stages:
    def __init__(
        self,
        database: Database,
        effects: Effects,
        state: Snapshot,
        run: RotationRun,
        planner: Planner | None = None,
        progress: Progress | None = None,
        assessor: Assessor | None = None,
    ) -> None:
        self.database, self.effects, self.state, self.run = database, effects, state, run
        self.planner = planner
        self.progress = progress
        self.assessor = assessor
        self.journal = Journal(database)

    async def output(self, stage: Stage) -> dict[str, Any]:
        result = await self.journal.get(self.run.organisation_id, self.run.id, stage)
        if result is None:
            raise ResourceConflictError(f"rotation is missing its {stage.value} checkpoint")
        if stage is Stage.CREATE:
            return result.output | {"run_id": self.run.id, "fencing_token": self.run.fencing_token}
        return result.output

    async def _api_proposal(self, phase: str, operation: Any) -> dict[str, str] | None:
        if self.assessor is None or self.state.playbook is not None:
            return None
        http = self.state.provider.http
        if http is None:
            return None
        decision = await self.assessor(
            self.run.organisation_id,
            ApiInput(
                stage="api",
                phase=phase,  # type: ignore[arg-type]
                method=operation.method,
                path=operation.path,
                identity_check=http.test_credential is not None,
                overlap=http.supports_overlap,
                scopes_count=len(self.state.credential.scopes),
            ),
        )
        if decision.decision != "execute":
            raise ResourceConflictError("Builder AI stopped the provider operation")
        return {"decision": decision.decision, "reason": decision.reason}

    async def execute(self, stage: Stage) -> tuple[frozenset[str], dict[str, Any], StageBindings]:
        state, run = self.state, self.run
        target = stable("generation", run.id)
        if stage is Stage.TRIGGER:
            return (
                frozenset({"request-authenticated", "source-deduplicated", "lease-held"}),
                {"source": run.trigger.source, "method": "api"},
                StageBindings(),
            )
        if stage is Stage.PREFLIGHT:
            if not state.controls.definition.preserve_old_generation:
                raise ResourceConflictError(
                    "controls must preserve the old generation during rollout"
                )
            if self.progress:
                await self.progress("controls-checked")
            initial_progress = getattr(self.effects, "initial_with_progress", None)
            result = (
                await initial_progress(state, self.progress)
                if initial_progress and self.progress
                else await self.effects.initial(state)
            )
            checks = {
                "provider-ready",
                "credential-known",
                "scopes-known",
                "playbook-eligible",
                "management-authenticated",
                "store-ready",
                "consumers-known",
                "runtime-ready",
                "verifier-ready",
                "overlap-supported",
                "mutation-declared",
                "no-conflict",
            }
            if state.controls.definition.require_revoke_approval:
                checks.add("approvers-known")
            return (
                frozenset(checks),
                result,
                StageBindings(
                    current_generation_id=state.current.id,
                    browser_playbook_version=state.playbook.id if state.playbook else None,
                ),
            )
        if stage is Stage.PLAN:
            initial = await self.output(Stage.PREFLIGHT)
            progress = await self.database.collection("run_progress").find_one(
                {"_id": f"{run.organisation_id}:{run.id}:{run.fencing_token}"}
            )
            allowed_checks = {
                "controls-checked",
                "approver-checked",
                "provider-checked",
                "credential-checked",
                "apps-checked",
            }
            completed_checks = progress.get("checks", []) if progress else []
            agent_input = {
                "trigger": {
                    "credential-exposure-detected": "exposure",
                    "credential-expiring": "expiry",
                    "credential-rotation-due": "schedule",
                    "credential-provider-drift": "drift",
                    "credential-inventory-drift": "drift",
                    "credential-runtime-drift": "drift",
                }.get(
                    run.trigger.kind,
                    "schedule"
                    if run.trigger.source == "schedule"
                    else "manual"
                    if run.trigger.source == "manual"
                    else "other",
                ),
                "method": "browser" if state.playbook else "api",
                "app_count": len(state.targets),
                "approval_required": state.controls.definition.require_revoke_approval,
                "observation_seconds": state.controls.definition.maximum_observation_seconds,
                "provider": "resend" if state.provider.platform == "resend" else "other",
                "preflight_checks": [
                    item
                    for item in completed_checks
                    if isinstance(item, str) and item in allowed_checks
                ],
            }
            proposal = (
                await self.planner(run.organisation_id, agent_input) if self.planner else None
            )
            if proposal is not None and (
                set(proposal) != {"strategy", "stages", "rationale"}
                or proposal.get("strategy") != "parallel"
                or proposal.get("stages") != list(PLAN_STAGES)
                or not isinstance(proposal.get("rationale"), str)
                or not 1 <= len(proposal["rationale"].strip()) <= 2048
            ):
                raise ResourceConflictError("Builder AI proposed an invalid rotation plan")
            plan = RotationPlan(
                id=stable("plan", run.id),
                organisation_id=run.organisation_id,
                run_id=run.id,
                credential_id=run.credential_id,
                control_version=run.control_version,
                strategy=RotationStrategy.PARALLEL,
                target_scopes=state.credential.scopes,
                consumer_ids=state.credential.consumer_ids,
                rollout=(100,),
                observation_seconds=state.controls.definition.maximum_observation_seconds,
                recovery_ids={Stage.DEPLOY: stable("rollback", run.id)},
            )
            result = {
                "plan": plan.model_dump(mode="json"),
                "plan_context": {
                    "credential": state.credential.display_name,
                    "provider": state.provider.display_name,
                    "apps": [target.service.display_name for target in state.targets],
                    "method": "browser" if state.playbook else "api",
                    "approval_required": state.controls.definition.require_revoke_approval,
                },
                "rollback_revisions": {
                    key: value["version"] for key, value in initial["deployments"].items()
                },
            }
            if proposal is not None:
                result["agent"] = proposal
                result["agent_input"] = agent_input
            return (
                frozenset({"plan-bound", "controls-pinned", "plan-hashed", "recovery-ready"}),
                result,
                StageBindings(plan_id=plan.id, plan_hash=digest(plan)),
            )
        if stage is Stage.CREATE:
            proposal = None
            if state.playbook is None and state.provider.http is not None:
                proposal = await self._api_proposal(
                    "create", state.provider.http.create_credential
                )
            result = await self.effects.issue(state, run.id)
            if proposal is not None:
                result["agent_api"] = proposal
            result["method"] = "browser" if state.playbook else "api"
            await generations.record(self.database, state, target, run.id, result)
            return (
                frozenset({"replacement-created", "mutation-resolved", "generation-recorded"}),
                result,
                StageBindings(target_generation_id=target),
            )
        created = await self.output(Stage.CREATE)
        if stage is Stage.STORE:
            result = await self.effects.access(state, created)
            return (
                frozenset({"secret-stored", "consumer-accessible", "plaintext-isolated"}),
                result,
                StageBindings(),
            )
        if stage is Stage.DEPLOY:
            result = await self.effects.deploy(state, created, run.id)
            initial = await self.output(Stage.PREFLIGHT)
            outputs = result["deployments"]
            if set(outputs) != {target.binding.id for target in state.targets}:
                raise ResourceConflictError("Deployment coverage is incomplete")
            deployments = []
            for consumer in state.targets:
                output = outputs[consumer.binding.id]
                if (
                    output["rollback_revision"]
                    != initial["deployments"][consumer.binding.id]["version"]
                ):
                    raise ResourceConflictError("Consumer changed before candidate deployment")
                deployments.append(
                    RuntimeDeployment(
                        binding_id=consumer.binding.id,
                        connection_id=consumer.runtime.id,
                        service=consumer.binding.runtime_resource,
                        candidate_revision=output["candidate_revision"],
                        rollback_revision=output["rollback_revision"],
                    )
                )
            return (
                frozenset(
                    {"candidate-deployed", "version-bound", "generation-tagged", "rollback-ready"}
                ),
                result,
                StageBindings(deployments=tuple(deployments)),
            )
        deployment_output = await self.output(Stage.DEPLOY)
        if stage is Stage.VERIFY:
            result = await self.effects.verify(state, created, deployment_output)
            checks = {
                "provider-valid",
                "store-valid",
                "deployment-valid",
                "coverage-complete",
                "rollback-ready",
            }
            if state.controls.definition.require_generation_telemetry:
                checks.add("telemetry-healthy")
            return (
                frozenset(checks),
                result,
                StageBindings(),
            )
        if stage is Stage.ROLLOUT:
            result = await self.effects.promote(state, created, deployment_output)
            return frozenset({"production-promoted", "rollout-healthy"}), result, StageBindings()
        if stage is Stage.OBSERVE:
            result = await self.effects.observe(state, created)
            checks = {"consumers-current"}
            if state.controls.definition.require_generation_telemetry:
                checks.update({"telemetry-healthy", "old-use-clear"})
            return (
                frozenset(checks),
                result,
                StageBindings(),
            )
        if stage is Stage.APPROVAL:
            observed = await self.journal.get(run.organisation_id, run.id, Stage.OBSERVE)
            if (
                not observed
                or (datetime.now(UTC) - observed.completed_at).total_seconds() > 60
                or state.controls.definition.require_revoke_approval
            ):
                raise ResourceConflictError(
                    "revocation requires current observation evidence and automatic controls"
                )
            return (
                frozenset({"approval-not-required", "evidence-current"}),
                {"require_revoke_approval": False},
                StageBindings(),
            )
        if stage is Stage.REVOKE:
            observation = await self.output(Stage.OBSERVE)
            proposal = None
            if state.playbook is None and state.provider.http is not None:
                proposal = await self._api_proposal(
                    "revoke", state.provider.http.revoke_credential
                )
            result = await self.effects.revoke(state, created, deployment_output, observation)
            if proposal is not None:
                result["agent_api"] = proposal
            result["method"] = "browser" if state.playbook else "api"
            return (
                frozenset(
                    {"old-revoked", "replacement-valid", "old-rejected", "old-secret-retired"}
                ),
                result,
                StageBindings(),
            )
        if stage is Stage.COMPLETE:
            verified = await self.journal.get(run.organisation_id, run.id, Stage.VERIFY)
            retired = await self.output(Stage.REVOKE)
            if not verified or not retired.get("old_rejected"):
                raise ResourceConflictError(
                    "completion requires verification and retirement evidence"
                )
            await self.effects.verify(state, created, deployment_output)
            await generations.promote(self.database, state, target, verified.id)
            return (
                frozenset(
                    {"consumers-current", "replacement-valid", "old-rejected", "audit-complete"}
                ),
                {"generation_id": target, "method": "api"},
                StageBindings(),
            )
        raise ResourceConflictError("unsupported API rotation stage")
