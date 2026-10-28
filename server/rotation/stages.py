import hashlib
from datetime import UTC, datetime
from typing import Any

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
from rotation.ports import Effects
from rotation.state import Snapshot


def stable(prefix: str, run_id: str) -> str:
    return prefix + "_" + hashlib.sha256(run_id.encode()).hexdigest()[:32]


class Stages:
    def __init__(
        self, database: Database, effects: Effects, state: Snapshot, run: RotationRun
    ) -> None:
        self.database, self.effects, self.state, self.run = database, effects, state, run
        self.journal = Journal(database)

    async def output(self, stage: Stage) -> dict[str, Any]:
        result = await self.journal.get(self.run.organisation_id, self.run.id, stage)
        if result is None:
            raise ResourceConflictError(f"rotation is missing its {stage.value} checkpoint")
        return result.output

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
            result = await self.effects.initial(state)
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
            return frozenset(checks), result, StageBindings(current_generation_id=state.current.id)
        if stage is Stage.PLAN:
            initial = await self.output(Stage.PREFLIGHT)
            plan = RotationPlan(
                id=stable("plan", run.id),
                organisation_id=run.organisation_id,
                run_id=run.id,
                credential_id=run.credential_id,
                control_version=run.control_version,
                strategy=RotationStrategy.PARALLEL,
                target_scopes=state.credential.scopes,
                consumer_ids=(state.binding.service_id,),
                rollout=(100,),
                observation_seconds=state.controls.definition.maximum_observation_seconds,
                recovery_ids={Stage.DEPLOY: stable("rollback", run.id)},
            )
            result = {
                "plan": plan.model_dump(mode="json"),
                "rollback_revision": initial["alias"]["version"],
            }
            return (
                frozenset({"plan-bound", "controls-pinned", "plan-hashed", "recovery-ready"}),
                result,
                StageBindings(plan_id=plan.id, plan_hash=digest(plan)),
            )
        if stage is Stage.CREATE:
            result = await self.effects.issue(state, run.id)
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
            if result["rollback_revision"] != initial["alias"]["version"]:
                raise ResourceConflictError("consumer changed before candidate deployment")
            deployment = RuntimeDeployment(
                binding_id=state.binding.id,
                connection_id=state.runtime.id,
                service=state.binding.runtime_resource,
                candidate_revision=result["candidate_revision"],
                rollback_revision=result["rollback_revision"],
            )
            return (
                frozenset(
                    {"candidate-deployed", "version-bound", "generation-tagged", "rollback-ready"}
                ),
                result,
                StageBindings(deployments=(deployment,)),
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
            result = await self.effects.revoke(state, created, deployment_output, observation)
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
