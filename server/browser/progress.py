from datetime import UTC, datetime

from contracts import (
    BrowserSession,
    ComputerUseActivity,
    ComputerUseActivityPhase,
    ComputerUseActivityStatus,
    PlaybookStep,
)
from core.errors import ResourceConflictError
from rotation.stages import stable

from browser.service import BrowserService


class Progress:
    def __init__(
        self, service: BrowserService, session: BrowserSession, step: PlaybookStep
    ) -> None:
        self.service, self.session, self.step = service, session, step
        self.count, self.content = 0, ""

    async def publish(self, content: str) -> None:
        if not content or content == self.content:
            return
        self.count += 1
        if self.count > 192 or len(content) > 4096:
            raise ResourceConflictError("Browser explanation exceeded its budget")
        self.content = content
        await self._record(ComputerUseActivityStatus.STREAMING, str(self.count))

    async def fail(self) -> None:
        await self._record(ComputerUseActivityStatus.FAILED, "failed")

    async def _record(self, status: ComputerUseActivityStatus, suffix: str) -> None:
        session, step = self.session, self.step
        await self.service.record_activity(
            session,
            ComputerUseActivity(
                id=stable("activity", session.id + ":" + step.id + ":summary:" + suffix),
                organisation_id=session.organisation_id,
                session_id=session.id,
                run_id=session.run_id,
                step_id=step.id,
                stage=step.stage,
                turn=session.step_count + 1,
                phase=ComputerUseActivityPhase.RESPONSE,
                status=status,
                content=self.content or None,
                recorded_at=datetime.now(UTC),
            ),
        )
