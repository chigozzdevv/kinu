import re
from datetime import UTC, datetime

from contracts import (
    BrowserActionKind,
    BrowserSession,
    ComputerUseActivity,
    ComputerUseActivityPhase,
    ComputerUseActivityStatus,
    PlaybookStep,
    PlaybookVersion,
)
from core.errors import ResourceConflictError
from rotation.stages import stable

from browser.driver import BrowserDriver
from browser.images import Images
from browser.planner import ControlPlanner
from browser.progress import Progress
from browser.service import BrowserService


class Proposals:
    def __init__(self, sessions: BrowserService, images: Images, planner: ControlPlanner) -> None:
        self.sessions, self.images, self.planner = sessions, images, planner

    async def record(
        self,
        session: BrowserSession,
        step: PlaybookStep,
        version: PlaybookVersion,
        driver: BrowserDriver,
        *,
        target_display_name: str | None = None,
    ) -> str:
        if (
            version.organisation_id != session.organisation_id
            or version.id != session.playbook_version
            or version.playbook_id != session.playbook_id
        ):
            raise ResourceConflictError("Browser task does not match its pinned playbook")
        await driver.validate_step(step)
        label = step.objective
        trusted = (
            (target_display_name,)
            if target_display_name and not _unsafe(target_display_name)
            else ()
        )
        image, controls, approved = await driver.model_screenshot(
            session,
            step.selectors[0] if step.selectors else None,
            trusted_text=trusted,
        )
        prompt = (
            "Create a replacement key following this playbook."
            if step.stage.value == "create"
            else "Retire the verified old key following this playbook."
        )
        instruction_lines = [
            "Playbook: " + version.playbook_id,
            "Version: " + version.id,
            "Playbook flow: " + _flow(version),
            "Step: " + step.id,
            "Objective: " + step.objective,
        ]
        if target_display_name:
            instruction_lines.append("Selected provider key name: " + target_display_name)
        instruction = "\n".join(instruction_lines)
        if len(instruction) > 2048 or _unsafe(instruction + label):
            await driver.clear_model_targets()
            raise ResourceConflictError("Browser task contains unsafe or oversized context")
        identity = stable("activity", session.id + ":" + step.id + ":requested")
        try:
            reference, image_digest = await self.images.save(
                session.organisation_id,
                session.run_id,
                identity,
                image,
            )
            await self.sessions.record_activity(
                session,
                ComputerUseActivity(
                    id=identity,
                    organisation_id=session.organisation_id,
                    session_id=session.id,
                    run_id=session.run_id,
                    step_id=step.id,
                    stage=step.stage,
                    turn=session.step_count + 1,
                    phase=ComputerUseActivityPhase.INPUT,
                    status=ComputerUseActivityStatus.SENT,
                    effect=step.effect,
                    prompt=prompt,
                    instruction=instruction,
                    intent=version.definition.name,
                    target=version.id,
                    image_reference=reference,
                    image_digest=image_digest,
                    recorded_at=datetime.now(UTC),
                ),
            )
        except Exception:
            await driver.clear_model_targets()
            raise
        progress = Progress(self.sessions, session, step)
        try:
            selected = await self.planner.choose(
                session.organisation_id,
                prompt + "\n" + instruction,
                controls,
                image,
                progress.publish,
            )
            if _unsafe(selected.summary):
                raise ResourceConflictError("Browser explanation contains credential-like content")
            if step.selectors and selected.control != approved:
                raise ResourceConflictError("Browser control differs from the pinned playbook")
            target_id = await driver.claim_model_target(selected.control)
            await driver.validate_step(step)
            await driver.clear_model_targets(keep=(target_id,))
        except Exception:
            await driver.clear_model_targets()
            await progress.fail()
            raise
        try:
            await self.sessions.record_activity(
                session,
                ComputerUseActivity(
                    id=stable("activity", session.id + ":" + step.id + ":proposed"),
                    organisation_id=session.organisation_id,
                    session_id=session.id,
                    run_id=session.run_id,
                    step_id=step.id,
                    stage=step.stage,
                    turn=session.step_count + 1,
                    phase=ComputerUseActivityPhase.PROPOSAL,
                    status=ComputerUseActivityStatus.PROPOSED,
                    action={
                        "browser.type": BrowserActionKind.TYPE,
                        "browser.select": BrowserActionKind.SELECT,
                    }.get(step.tool, BrowserActionKind.CLICK),
                    intent=controls[selected.control],
                    target=step.id,
                    arguments={"control_id": selected.control},
                    content=selected.summary,
                    recorded_at=datetime.now(UTC),
                ),
            )
        except Exception:
            await driver.clear_model_targets()
            raise
        return target_id

    async def capture_targets(
        self,
        session: BrowserSession,
        step: PlaybookStep,
        version: PlaybookVersion,
        driver: BrowserDriver,
        *,
        replacement_name: str | None = None,
    ) -> tuple[str, str]:
        """Let Builder locate redacted secret and ID fields after key creation."""
        if (
            version.organisation_id != session.organisation_id
            or version.id != session.playbook_version
            or version.playbook_id != session.playbook_id
        ):
            raise ResourceConflictError("Browser task does not match its pinned playbook")
        trusted = (replacement_name,) if replacement_name and not _unsafe(replacement_name) else ()
        image, controls, _ = await driver.model_screenshot(
            session,
            include_text=True,
            trusted_text=trusted,
        )
        if len(image) > 524_288:
            await driver.clear_model_targets()
            raise ResourceConflictError("Browser visual input exceeds its boundary")
        identity = stable("activity", session.id + ":" + step.id + ":capture-input")
        instruction = "\n".join(
            [
                "Playbook: " + version.playbook_id,
                "Version: " + version.id,
                "Playbook flow: " + _flow(version),
                "Step: " + step.id,
                "Objective: Capture the newly created key inside Kinu.",
            ]
        )
        try:
            reference, image_digest = await self.images.save(
                session.organisation_id, session.run_id, identity, image
            )
            await self.sessions.record_activity(
                session,
                ComputerUseActivity(
                    id=identity,
                    organisation_id=session.organisation_id,
                    session_id=session.id,
                    run_id=session.run_id,
                    step_id=step.id,
                    stage=step.stage,
                    turn=session.step_count + 1,
                    phase=ComputerUseActivityPhase.INPUT,
                    status=ComputerUseActivityStatus.SENT,
                    effect=step.effect,
                    prompt="Locate the replacement key on this redacted provider page.",
                    instruction=instruction,
                    intent=version.definition.name,
                    target=version.id,
                    image_reference=reference,
                    image_digest=image_digest,
                    recorded_at=datetime.now(UTC),
                ),
            )
        except Exception:
            await driver.clear_model_targets()
            raise
        progress = Progress(self.sessions, session, step)
        try:
            secret = await self.planner.choose(
                session.organisation_id,
                "Select the visible element containing the newly created secret value. "
                "The screenshot redacts values. Do not repeat or infer the secret; "
                "stop if unclear.\n" + instruction,
                controls,
                image,
                progress.publish,
            )
            secret_target = await driver.claim_model_target(secret.control)
            provider = await self.planner.choose(
                session.organisation_id,
                "Select the visible element displaying the provider key ID for the new key, "
                "not its secret value. Stop if the ID is not visible.\n" + instruction,
                controls,
                image,
                progress.publish,
            )
            if provider.control == secret.control:
                raise ResourceConflictError(
                    "Builder could not distinguish the key ID from its secret"
                )
            provider_target = await driver.claim_model_target(provider.control)
            await driver.clear_model_targets(keep=(secret_target, provider_target))
        except Exception:
            await driver.clear_model_targets()
            await progress.fail()
            raise

        try:
            for suffix, selected, purpose in (
                ("capture-secret", secret, "replacement secret field"),
                ("capture-provider-id", provider, "replacement provider ID"),
            ):
                await self.sessions.record_activity(
                    session,
                    ComputerUseActivity(
                        id=stable("activity", session.id + ":" + step.id + ":" + suffix),
                        organisation_id=session.organisation_id,
                        session_id=session.id,
                        run_id=session.run_id,
                        step_id=step.id,
                        stage=step.stage,
                        turn=session.step_count + 1,
                        phase=ComputerUseActivityPhase.PROPOSAL,
                        status=ComputerUseActivityStatus.PROPOSED,
                        action=BrowserActionKind.CLICK,
                        intent=purpose,
                        target=version.id,
                        arguments={"control_id": selected.control},
                        content="Selected from the redacted provider screenshot.",
                        recorded_at=datetime.now(UTC),
                    ),
                )
        except Exception:
            await driver.clear_model_targets()
            raise
        return secret_target, provider_target


def _unsafe(value: str) -> bool:
    return bool(
        re.search(
            r"\b(?:re_|sk_|rk_)[A-Za-z0-9_-]{12,}|\bBearer\s+[A-Za-z0-9._-]{16,}",
            value,
        )
    )


def _flow(version: PlaybookVersion) -> str:
    parts: list[str] = []
    size = 0
    for step in version.definition.steps:
        part = f"{step.stage.value}: {step.objective[:96]}"
        if len(step.objective) > 96:
            part += "…"
        added = len(part) + (3 if parts else 0)
        if size + added > 512:
            parts.append("…")
            break
        parts.append(part)
        size += added
    return " → ".join(parts)
