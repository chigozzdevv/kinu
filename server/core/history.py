from collections.abc import Iterable
from typing import Any, Protocol

from contracts import (
    AgentDecisionSummary,
    AgentKind,
    Approval,
    ApprovalDecision,
    ApprovalEvidenceSnapshot,
    BrowserActionSummary,
    RotationHistory,
    RotationRun,
    RunStageActivity,
    Stage,
    StageDetail,
    StageExecutionResult,
)


class BrowserEvidenceReader(Protocol):
    async def read_image(
        self, organisation_id: str, run_id: str, resource: str, expected_digest: str
    ) -> tuple[bytes, str]: ...


class RunHistoryService(Protocol):
    async def get(self, organisation_id: str, run_id: str) -> RotationHistory: ...

    async def approval_evidence(
        self, organisation_id: str, approval_id: str
    ) -> ApprovalEvidenceSnapshot: ...

    async def image(
        self, organisation_id: str, run_id: str, activity_id: str
    ) -> tuple[bytes, str]: ...


def _activity(
    result: StageExecutionResult,
    run: RotationRun | None = None,
    approvals: tuple[Approval, ...] = (),
) -> RunStageActivity:
    summary, details = _stage_presentation(result, run, approvals)
    return RunStageActivity(
        id=result.id,
        stage=result.stage,
        status=result.status,
        checks=tuple(sorted(result.checks)),
        evidence_count=len(result.evidence_ids),
        summary=summary,
        details=details,
        agent_decisions=_agent_decisions(result.stage, result.output),
        agent_input=_plan_input(result.stage, result.output),
        plan_steps=_plan_steps(result.stage, result.output),
        browser_actions=_browser_actions(result.output),
        reason=result.reason,
        retryable=result.retryable,
        started_at=result.started_at,
        completed_at=result.completed_at,
    )


def _plan_input(stage: Stage, output: dict[str, Any]) -> tuple[StageDetail, ...]:
    value = output.get("agent_input")
    if stage is not Stage.PLAN or not isinstance(value, dict):
        return ()
    labels = {
        "exposure": "Exposure",
        "expiry": "Expiry",
        "drift": "Drift",
        "manual": "Manual",
        "schedule": "Scheduled",
        "other": "Other",
    }
    trigger = value.get("trigger")
    method = value.get("method")
    count = value.get("app_count")
    approval = value.get("approval_required")
    observation = value.get("observation_seconds")
    if (
        not isinstance(trigger, str)
        or trigger not in labels
        or not isinstance(method, str)
        or method not in {"api", "browser"}
        or type(count) is not int
        or type(approval) is not bool
        or type(observation) is not int
    ):
        return ()
    details = [
        StageDetail(label="Trigger", value=labels[trigger]),
        StageDetail(label="Method", value="Browser" if method == "browser" else "API"),
        StageDetail(label="Connected apps", value=str(count)),
        StageDetail(label="Revocation approval", value="Required" if approval else "Not required"),
        StageDetail(label="Observation", value=_duration(observation)),
    ]
    if value.get("provider") in ("resend", "other"):
        details.insert(
            1,
            StageDetail(
                label="Provider", value="Resend" if value["provider"] == "resend" else "Other"
            ),
        )
    checks = value.get("preflight_checks")
    known = {
        "controls-checked": "Key controls",
        "approver-checked": "Approver",
        "provider-checked": "Provider",
        "credential-checked": "Current key",
        "apps-checked": "App mappings and verification",
    }
    if isinstance(checks, list):
        names = [known[item] for item in checks if isinstance(item, str) and item in known]
        if names:
            details.append(StageDetail(label="Passed preflight", value=", ".join(names)))
    return tuple(details)


def _plan_steps(stage: Stage, output: dict[str, Any]) -> tuple[Stage, ...]:
    agent = output.get("agent")
    if (
        stage is not Stage.PLAN
        or not isinstance(agent, dict)
        or not isinstance(agent.get("stages"), list)
    ):
        return ()
    try:
        return tuple(Stage(item) for item in agent["stages"])
    except (ValueError, TypeError):
        return ()


def _stage_presentation(
    result: StageExecutionResult, run: RotationRun | None, approvals: tuple[Approval, ...]
) -> tuple[str | None, tuple[StageDetail, ...]]:
    if result.status.value not in {"succeeded", "recovered"}:
        if result.stage is Stage.APPROVAL:
            if approvals:
                return _approval_presentation(result.output, approvals)
            return ("Waiting for revocation approval", ())
        browser_error = _browser_error(result.output)
        if browser_error is not None:
            code, message = browser_error
            if code == "authentication-required":
                return (
                    "Provider session requires reauthentication",
                    (StageDetail(label="Provider response", value=message),),
                )
            return (
                "Browser step needs attention",
                (StageDetail(label="Provider response", value=message),),
            )
        if _browser_activity(result.output):
            return ("Computer Use paused", (StageDetail(label="Method", value="Computer Use"),))
        return (None, ())
    stage = result.stage
    output = result.output
    checks = result.checks
    if stage is Stage.TRIGGER:
        if run is None:
            return ("Rotation started", ())
        summary, configured_trigger = _trigger_story(run.trigger.source, run.trigger.kind)
        manual = run.trigger.kind == "manual" or run.trigger.source.strip().lower() in {
            "manual",
            "console",
            "dashboard",
            "agent-native",
        }
        details = []
        if not manual:
            details.append(StageDetail(label="Source", value=configured_trigger))
        if run.trigger.reason.strip():
            details.append(StageDetail(label="Reason", value=run.trigger.reason))
        return (
            "Manual rotation" if manual else summary,
            tuple(details),
        )
    if stage is Stage.PREFLIGHT:
        connections = _string_items(output.get("connections"))
        details = []
        if connections:
            details.append(StageDetail(label="Connections", value=f"{len(connections)} ready"))
        if "approvers-known" in checks:
            details.append(StageDetail(label="Approver", value="Available"))
        if _text(output.get("browser_playbook_version")):
            details.append(StageDetail(label="Playbook", value="Pinned"))
        return ("Ready to rotate", tuple(details))
    if stage is Stage.PLAN:
        plan = output.get("plan")
        values = plan if isinstance(plan, dict) else {}
        details = []
        context = output.get("plan_context")
        if isinstance(context, dict):
            for key, label in (("credential", "Credential"), ("provider", "Provider")):
                text = _text(context.get(key))
                if text:
                    details.append(StageDetail(label=label, value=text[:512]))
            apps = _joined(_string_items(context.get("apps")))
            if apps:
                details.append(StageDetail(label="Apps", value=apps[:512]))
            if context.get("method") in ("api", "browser"):
                details.append(
                    StageDetail(
                        label="Method", value="Browser" if context["method"] == "browser" else "API"
                    )
                )
            if type(context.get("approval_required")) is bool:
                details.append(
                    StageDetail(
                        label="Revocation approval",
                        value="Required" if context["approval_required"] else "Not required",
                    )
                )
        scopes = _joined(_string_items(values.get("target_scopes")))
        if scopes:
            details.append(StageDetail(label="Permissions", value=scopes))
        rollout = values.get("rollout")
        rollout_summary = None
        if isinstance(rollout, list) and rollout and all(isinstance(item, int) for item in rollout):
            rollout_summary = " → ".join(f"{item}%" for item in rollout)
        observation = values.get("observation_seconds")
        if isinstance(observation, int) and observation > 0:
            details.append(StageDetail(label="Observation", value=_duration(observation)))
        if values.get("recovery_ids"):
            details.append(StageDetail(label="Recovery", value="Rollback prepared"))
        summary = f"{rollout_summary} rollout" if rollout_summary else "Rotation plan ready"
        return (summary, tuple(details))
    if stage is Stage.CREATE:
        generation = output.get("generation")
        values = generation if isinstance(generation, dict) else {}
        details = [
            StageDetail(label="Operation", value="Create replacement"),
            StageDetail(
                label="Method",
                value="Browser"
                if output.get("method") == "browser"
                or (run and run.browser_playbook_version)
                or _browser_activity(output)
                else "Provider API",
            ),
        ]
        provider_id = _text(values.get("provider_id")) or _text(output.get("provider_id"))
        if provider_id:
            details.append(StageDetail(label="Provider credential", value=provider_id))
        return ("Replacement created", tuple(details))
    if stage is Stage.STORE:
        details = []
        if output.get("stored") is True:
            details.append(StageDetail(label="Storage", value="Replacement stored securely"))
        secret_store = _text(output.get("secret_store"))
        if secret_store:
            details.append(StageDetail(label="Secret store", value=_base_name(secret_store)))
        secret_reference = _text(output.get("secret_reference"))
        version = _resource_name(secret_reference) if secret_reference else None
        if version:
            details.append(StageDetail(label="Version", value=f"{version} enabled"))
        consumer_access = output.get("consumer_access")
        access_count = len(consumer_access) if isinstance(consumer_access, list) else 0
        if access_count:
            details.append(
                StageDetail(label="Consumer access", value=f"Confirmed for {access_count}")
            )
        return ("Secret verified", tuple(details))
    if stage is Stage.DEPLOY:
        deployments = run.deployments if run is not None else ()
        service_names = _unique(_resource_name(item.service) for item in deployments)
        services = _joined(service_names)
        candidates = _joined(_resource_name(item.candidate_revision) for item in deployments)
        rollbacks = _joined(_resource_name(item.rollback_revision) for item in deployments)
        details = []
        if len(service_names) > 1 and services:
            details.append(StageDetail(label="Services", value=services))
        if candidates:
            details.append(StageDetail(label="Candidate", value=candidates))
        if rollbacks:
            details.append(StageDetail(label="Rollback", value=rollbacks))
        summary = (
            f"Candidate running on {service_names[0]}"
            if len(service_names) == 1
            else f"Candidates running on {len(service_names)} services"
            if service_names
            else "Candidate running"
        )
        return (summary, tuple(details))
    if stage is Stage.VERIFY:
        details = []
        for check, label, value in (
            ("provider-valid", "Provider", "Replacement valid"),
            ("store-valid", "Secret store", "Version enabled"),
            ("deployment-valid", "Runtime", "Candidate running"),
        ):
            if check in checks:
                details.append(StageDetail(label=label, value=value))
        if "telemetry-healthy" in checks:
            details.append(StageDetail(label="Telemetry", value="Healthy"))
        return ("Deployment verified", tuple(details))
    if stage is Stage.ROLLOUT:
        services = _rollout_services(output)
        details = []
        if services:
            details.append(StageDetail(label="Services", value=services))
        milestones = output.get("milestones")
        if (
            isinstance(milestones, list)
            and milestones
            and all(isinstance(item, int) for item in milestones)
        ):
            details.append(
                StageDetail(label="Milestones", value=" → ".join(f"{item}%" for item in milestones))
            )
        return ("100% on replacement", tuple(details))
    if stage is Stage.OBSERVE:
        details = []
        samples = output.get("samples")
        if isinstance(samples, int) and samples > 0:
            details.append(StageDetail(label="Health checks", value=f"{samples} passed"))
        if "telemetry-healthy" in checks:
            details.append(StageDetail(label="Telemetry", value="Healthy"))
        if "old-use-clear" in checks:
            details.append(StageDetail(label="Previous credential use", value="Not detected"))
        observation = output.get("observation_seconds")
        summary = (
            f"{_duration(observation)} observation passed"
            if isinstance(observation, int) and observation > 0
            else "Observation passed"
        )
        return (summary, tuple(details))
    if stage is Stage.APPROVAL:
        if "approval-not-required" in checks:
            return ("Approval not required", ())
        return _approval_presentation(output, approvals)
    if stage is Stage.REVOKE:
        details = [
            StageDetail(label="Operation", value="Revoke previous key"),
            StageDetail(
                label="Method",
                value="Browser"
                if output.get("method") == "browser" or (run and run.browser_playbook_version)
                else "Provider API",
            ),
        ]
        if "old-rejected" in checks:
            details.append(StageDetail(label="Old credential", value="Rejected"))
        if "replacement-valid" in checks:
            details.append(StageDetail(label="Replacement", value="Valid"))
        if "old-secret-retired" in checks:
            details.append(StageDetail(label="Previous secret version", value="Retired"))
        return ("Previous credential revoked", tuple(details))
    if stage is Stage.COMPLETE:
        details = []
        if "replacement-valid" in checks:
            details.append(StageDetail(label="Replacement", value="Verified"))
        if "old-rejected" in checks:
            details.append(StageDetail(label="Previous key", value="Rejected"))
        if "consumers-current" in checks:
            details.append(StageDetail(label="Apps", value="Using the replacement"))
        return ("Rotation complete", tuple(details))


def _browser_error(output: dict[str, Any]) -> tuple[str, str] | None:
    value = output.get("browser_error")
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    message = value.get("message")
    if not isinstance(code, str) or not isinstance(message, str) or (not message.strip()):
        return None
    return (code, message)


def _approval_presentation(
    output: dict[str, Any], approvals: tuple[Approval, ...]
) -> tuple[str, tuple[StageDetail, ...]]:
    if output.get("approval_required") is False:
        return ("Approval not required", ())
    references = output.get("approvals")
    referenced_ids = (
        {
            approval_id
            for item in references
            if isinstance(item, dict)
            for approval_id in [_text(item.get("approval_id"))]
            if approval_id is not None
        }
        if isinstance(references, list)
        else set()
    )
    current = tuple(item for item in approvals if not referenced_ids or item.id in referenced_ids)
    details = (
        (StageDetail(label="Protected actions", value=str(len(current))),)
        if len(current) > 1
        else ()
    )
    decisions = {item.decision for item in current}
    if ApprovalDecision.REJECTED in decisions:
        return ("Revocation rejected", details)
    if ApprovalDecision.CANCELLED in decisions:
        return ("Revocation cancelled", details)
    if ApprovalDecision.MORE_EVIDENCE in decisions:
        return ("More evidence requested", details)
    if ApprovalDecision.EXTEND in decisions:
        return ("Observation extended", details)
    if ApprovalDecision.PENDING in decisions:
        return ("Waiting for revocation approval", details)
    if current and decisions == {ApprovalDecision.APPROVED}:
        return ("Revocation approved", details)
    return ("Revocation approval requested", details)


def _agent_decisions(stage: Stage, output: dict[str, Any]) -> tuple[AgentDecisionSummary, ...]:
    decisions: list[AgentDecisionSummary] = []
    agent = output.get("agent")
    if isinstance(agent, dict):
        if stage is Stage.PREFLIGHT:
            missing = _string_items(agent.get("missing_inventory"))
            stale = _string_items(agent.get("stale_inventory"))
            declared = _string_items(agent.get("declared_consumers"))
            ready = not missing and (not stale)
            if ready and declared:
                noun = "consumer" if len(declared) == 1 else "consumers"
                explanation = (
                    "No missing or stale mappings were found across "
                    f"{len(declared)} declared {noun}."
                )
            elif ready:
                explanation = (
                    _text(agent.get("conclusion"))
                    or "No missing or stale consumer mappings were found."
                )
            else:
                explanation = (
                    _text(agent.get("conclusion"))
                    or "One or more consumer mappings must be resolved before rotation."
                )
            decisions.append(
                AgentDecisionSummary(
                    agent=AgentKind.INVENTORY,
                    decision="Consumer inventory confirmed"
                    if ready
                    else "Resolve consumer mappings",
                    explanation=explanation,
                )
            )
        elif stage is Stage.PLAN:
            rationale = _text(agent.get("rationale"))
            if rationale:
                strategy = _text(agent.get("strategy")) or "rotation"
                decisions.append(
                    AgentDecisionSummary(
                        agent=AgentKind.PLANNER,
                        decision=f"Use {_human(strategy)} strategy",
                        explanation=rationale,
                    )
                )
    api = output.get("agent_api")
    if isinstance(api, dict) and stage in {Stage.CREATE, Stage.REVOKE}:
        executed = api.get("decision") == "execute"
        decisions.append(
            AgentDecisionSummary(
                agent=AgentKind.OPERATOR,
                decision=("Execute provider operation" if executed else "Stop provider operation"),
                explanation=(
                    "Builder AI approved the declared provider operation; "
                    "Kinu executed it and verified the result."
                    if executed
                    else "Builder AI stopped the declared provider operation."
                ),
            )
        )
    for item in _browser_activity(output):
        operator = item.get("operator")
        if not isinstance(operator, dict):
            continue
        ready = operator.get("ready") is True and operator.get("drift_detected") is not True
        explanation = (
            "The approved browser step matched its expected checkpoint."
            if ready
            else "The browser step paused because its safety checkpoint did not pass."
        )
        decisions.append(
            AgentDecisionSummary(
                agent=AgentKind.OPERATOR,
                decision="Continue browser step" if ready else "Pause browser step",
                explanation=explanation,
            )
        )
    return tuple(decisions)


def _browser_actions(output: dict[str, Any]) -> tuple[BrowserActionSummary, ...]:
    actions: list[BrowserActionSummary] = []
    for item in _browser_activity(output):
        step_id = _text(item.get("step_id"))
        objective = _text(item.get("objective"))
        operation = _text(item.get("operation"))
        outcome = _text(item.get("outcome"))
        if step_id and objective and operation and outcome:
            actions.append(
                BrowserActionSummary(
                    step_id=step_id, objective=objective, operation=operation, outcome=outcome
                )
            )
    return tuple(actions)


def _browser_activity(output: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    value = output.get("browser_activity")
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _trigger_story(source: str, kind: str) -> tuple[str, str]:
    normalised = source.strip().lower()
    if kind in {"credential-rotation-due", "credential-expiring"}:
        return ("Rotation started on schedule", "Scheduled rotation")
    if kind == "credential-exposure-detected":
        return ("Exposure alert started rotation", _human(source))
    if kind in {
        "credential-inventory-drift",
        "credential-provider-drift",
        "credential-runtime-drift",
    }:
        return ("Configuration drift started rotation", "Configuration drift")
    if kind == "manual" or normalised in {"manual", "console", "dashboard"}:
        return ("Rotation started manually", "Manual rotation")
    if normalised in {"schedule", "scheduler"}:
        return ("Rotation started on schedule", "Scheduled rotation")
    if normalised == "github-secret-scanning":
        return ("Exposure alert started rotation", "GitHub Secret Scanning")
    if normalised == "secret-manager":
        return ("Secret-store event started rotation", "Secret-store schedule")
    source_name = _human(source)
    return (f"{source_name} started rotation", source_name)


def _human(value: str) -> str:
    terms = {"api": "API", "github": "GitHub", "id": "ID", "oauth": "OAuth"}
    words = value.replace("_", " ").replace("-", " ").split()
    return " ".join(terms.get(word.lower(), word.capitalize()) for word in words)


def _duration(seconds: int) -> str:
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds % size == 0:
            amount = seconds // size
            return f"{amount} {unit}{('' if amount == 1 else 's')}"
    return f"{seconds} seconds"


def _resource_name(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1]


def _base_name(value: str) -> str:
    return value.split("·", 1)[0].strip()


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in values if item))


def _joined(values: Iterable[str]) -> str | None:
    unique = tuple(dict.fromkeys(item for item in values if isinstance(item, str) and item))
    if not unique:
        return None
    return ", ".join(unique)[:512]


def _rollout_services(output: dict[str, Any]) -> str | None:
    steps = output.get("steps")
    if not isinstance(steps, list):
        return None
    return _joined(
        _resource_name(service)
        for item in steps
        if isinstance(item, dict)
        for service in [_text(item.get("service"))]
        if service is not None
    )
