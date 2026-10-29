import re
from datetime import UTC, datetime

from contracts import (
    BrowserActionKind,
    BrowserSession,
    ComputerUseActivity,
    ComputerUseActivityPhase,
    ComputerUseActivityStatus,
    PlaybookEffect,
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
    ) -> None:
        if (
            version.organisation_id != session.organisation_id
            or version.id != session.playbook_version
            or version.playbook_id != session.playbook_id
        ):
            raise ResourceConflictError("Browser task does not match its pinned playbook")
        await driver.validate_step(step)
        label = await driver.control_label(step.selectors[0])
        prompt = (
            "Create a replacement key following this playbook."
            if step.effect is PlaybookEffect.CREATE_CREDENTIAL
            else "Retire the verified old key following this playbook."
        )
        instruction = "\n".join(
            [
                "Playbook: " + version.playbook_id,
                "Version: " + version.id,
                "Step: " + step.id,
                "Objective: " + step.objective,
                *(
                    (condition.selector.name or condition.selector.value)
                    + ": "
                    + condition.expected
                    for condition in step.conditions
                ),
            ]
        )
        if len(instruction) > 2048 or re.search(
            r"\b(?:re_|sk_|rk_)[A-Za-z0-9_-]{24,}|\bBearer\s+[A-Za-z0-9._-]{16,}",
            instruction + label,
        ):
            raise ResourceConflictError("Browser task contains unsafe or oversized context")
        identity = stable("activity", session.id + ":" + step.id + ":requested")
        reference, image_digest = await self.images.save(
            session.organisation_id,
            session.run_id,
            identity,
            await driver.evidence_screenshot(session),
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
        progress = Progress(self.sessions, session, step)
        try:
            selected = await self.planner.choose(
                session.organisation_id,
                prompt + "\n" + instruction,
                {step.id: label},
                progress.publish,
            )
            if selected.control != step.id:
                raise ResourceConflictError("Browser control is not approved")
            await driver.validate_step(step)
        except Exception:
            await progress.fail()
            raise
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
                action=BrowserActionKind.CLICK,
                intent=label,
                target=step.id,
                arguments={"control_id": selected.control, "button": label},
                content=selected.summary,
                recorded_at=datetime.now(UTC),
            ),
        )
