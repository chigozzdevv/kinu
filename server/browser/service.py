from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import (
    BrowserAction,
    BrowserActionKind,
    BrowserActionRecord,
    BrowserActionStatus,
    BrowserPolicy,
    BrowserSession,
    BrowserStatus,
    ComputerUseActivity,
    SecureCaptureResult,
)
from core.errors import ResourceConflictError

from browser.url import metadata_url


class BrowserRepository(Protocol):
    async def create(self, session: BrowserSession) -> BrowserSession: ...

    async def get(self, organisation_id: str, session_id: str) -> BrowserSession: ...

    async def update(
        self,
        organisation_id: str,
        session_id: str,
        expected_revision: int,
        changed: BrowserSession,
    ) -> BrowserSession: ...

    async def save_capture(self, result: SecureCaptureResult) -> SecureCaptureResult: ...

    async def complete_capture(
        self,
        current: BrowserSession,
        changed: BrowserSession,
        result: SecureCaptureResult,
    ) -> BrowserSession: ...

    async def save_activity(self, activity: ComputerUseActivity) -> ComputerUseActivity: ...

    async def begin_action(
        self,
        current: BrowserSession,
        changed: BrowserSession,
        action: BrowserAction,
        authorised_at: datetime,
    ) -> BrowserSession: ...

    async def finish_action(
        self,
        organisation_id: str,
        session_id: str,
        action_id: str,
        status: BrowserActionStatus,
        error: str | None,
        completed_at: datetime,
    ) -> BrowserActionRecord: ...


class BrowserService:
    def __init__(
        self,
        repository: BrowserRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def create(self, session: BrowserSession) -> BrowserSession:
        if session.status is not BrowserStatus.PROVISIONING:
            raise ResourceConflictError("browser sessions must begin in provisioning")
        if not session.model_paused or not session.recording_paused:
            raise ResourceConflictError("browser barriers must remain armed during provisioning")
        return await self._repository.create(session)

    async def attach(
        self,
        organisation_id: str,
        session_id: str,
        revision: int,
        instance: str,
        internal_address: str,
    ) -> BrowserSession:
        current = await self._current(organisation_id, session_id, revision)
        if current.status is not BrowserStatus.PROVISIONING:
            raise ResourceConflictError("only a provisioning browser can attach a VM")
        changed = self._change(
            current,
            status=BrowserStatus.READY,
            worker_instance=instance,
            internal_address=internal_address,
        )
        return await self._repository.update(organisation_id, session_id, revision, changed)

    async def start(self, organisation_id: str, session_id: str, revision: int) -> BrowserSession:
        current = await self._current(organisation_id, session_id, revision)
        if current.status not in {BrowserStatus.READY, BrowserStatus.PAUSED}:
            raise ResourceConflictError("browser is not ready to start")
        changed = self._change(
            current,
            status=BrowserStatus.RUNNING,
            model_paused=False,
            recording_paused=False,
        )
        return await self._repository.update(organisation_id, session_id, revision, changed)

    async def rebind_fence(
        self,
        organisation_id: str,
        session_id: str,
        revision: int,
        fencing_token: int,
    ) -> BrowserSession:
        current = await self._current(organisation_id, session_id, revision)
        if current.status not in {BrowserStatus.READY, BrowserStatus.PAUSED}:
            raise ResourceConflictError("only an inactive browser can accept a renewed run fence")
        if fencing_token <= current.fencing_token:
            raise ResourceConflictError("browser fence must advance monotonically")
        changed = self._change(current, fencing_token=fencing_token)
        return await self._repository.update(organisation_id, session_id, revision, changed)

    async def arm_capture(
        self, organisation_id: str, session_id: str, revision: int
    ) -> BrowserSession:
        current = await self._current(organisation_id, session_id, revision)
        if current.status is not BrowserStatus.RUNNING:
            raise ResourceConflictError("secure capture can only arm a running browser")
        changed = self._change(
            current,
            status=BrowserStatus.CAPTURING,
            model_paused=True,
            recording_paused=True,
        )
        return await self._repository.update(organisation_id, session_id, revision, changed)

    async def arm_human_capture(
        self, organisation_id: str, session_id: str, revision: int
    ) -> BrowserSession:
        current = await self._current(organisation_id, session_id, revision)
        if current.status not in {BrowserStatus.TAKEOVER, BrowserStatus.PAUSED}:
            raise ResourceConflictError("human secure input requires takeover or a paused session")
        changed = self._change(
            current,
            status=BrowserStatus.CAPTURING,
            model_paused=True,
            recording_paused=True,
        )
        return await self._repository.update(organisation_id, session_id, revision, changed)

    async def complete_capture(
        self,
        result: SecureCaptureResult,
        revision: int,
    ) -> BrowserSession:
        current = await self._current(result.organisation_id, result.session_id, revision)
        if current.status is not BrowserStatus.CAPTURING:
            raise ResourceConflictError("secure capture barrier is not armed")
        takeover = current.takeover_subject is not None
        changed = self._change(
            current,
            status=BrowserStatus.TAKEOVER if takeover else BrowserStatus.RUNNING,
            model_paused=takeover,
            recording_paused=takeover,
        )
        return await self._repository.complete_capture(current, changed, result)

    async def freeze(self, organisation_id: str, session_id: str, revision: int) -> BrowserSession:
        current = await self._current(organisation_id, session_id, revision)
        changed = self._change(
            current,
            status=BrowserStatus.PAUSED,
            model_paused=True,
            recording_paused=True,
        )
        return await self._repository.update(organisation_id, session_id, revision, changed)

    async def takeover(
        self,
        organisation_id: str,
        session_id: str,
        revision: int,
        subject: str,
    ) -> BrowserSession:
        current = await self._current(organisation_id, session_id, revision)
        if current.status not in {BrowserStatus.RUNNING, BrowserStatus.PAUSED}:
            raise ResourceConflictError("browser cannot enter takeover from its current state")
        changed = self._change(
            current,
            status=BrowserStatus.TAKEOVER,
            model_paused=True,
            recording_paused=True,
            takeover_subject=subject,
        )
        return await self._repository.update(organisation_id, session_id, revision, changed)

    async def release_takeover(
        self,
        organisation_id: str,
        session_id: str,
        revision: int,
        subject: str,
    ) -> BrowserSession:
        current = await self._current(organisation_id, session_id, revision)
        if current.status is not BrowserStatus.TAKEOVER or current.takeover_subject != subject:
            raise ResourceConflictError("only the takeover owner can release the browser")
        changed = self._change(
            current,
            status=BrowserStatus.PAUSED,
            model_paused=True,
            recording_paused=True,
            takeover_subject=None,
        )
        return await self._repository.update(organisation_id, session_id, revision, changed)

    async def authorize_action(
        self,
        organisation_id: str,
        session_id: str,
        revision: int,
        action: BrowserAction,
    ) -> BrowserSession:
        current = await self._current(organisation_id, session_id, revision)
        allowed_status = current.status in {BrowserStatus.RUNNING, BrowserStatus.TAKEOVER}
        protected_capture = (
            current.status is BrowserStatus.CAPTURING
            and action.protected
            and current.model_paused
            and current.recording_paused
        )
        if not allowed_status and not protected_capture:
            raise ResourceConflictError("browser session is not accepting actions")
        if action.session_id != session_id or action.fencing_token != current.fencing_token:
            raise ResourceConflictError("browser action holds a stale session fence")
        if action.kind not in current.policy.allowed_actions:
            raise ResourceConflictError("browser action is not allowed by session policy")
        if current.status is BrowserStatus.TAKEOVER and action.protected:
            raise ResourceConflictError("takeover cannot self-authorise a protected action")
        changed = self._change(current, step_count=current.step_count + 1)
        recorded = action.model_copy(
            update={
                **({"value": "<redacted>"} if action.kind is BrowserActionKind.TYPE else {}),
                **({"url": metadata_url(action.url)} if action.url is not None else {}),
            }
        )
        return await self._repository.begin_action(current, changed, recorded, self._clock())

    async def finish_action(
        self,
        organisation_id: str,
        session_id: str,
        action_id: str,
        succeeded: bool,
        error: str | None = None,
    ) -> BrowserActionRecord:
        status = BrowserActionStatus.SUCCEEDED if succeeded else BrowserActionStatus.FAILED
        return await self._repository.finish_action(
            organisation_id,
            session_id,
            action_id,
            status,
            error,
            self._clock(),
        )

    async def record_activity(
        self, session: BrowserSession, activity: ComputerUseActivity
    ) -> ComputerUseActivity:
        if (
            activity.organisation_id != session.organisation_id
            or activity.session_id != session.id
            or activity.run_id != session.run_id
        ):
            raise ResourceConflictError("Computer Use activity crosses its browser session")
        if activity.turn not in {session.step_count, session.step_count + 1}:
            raise ResourceConflictError("Computer Use activity turn is inconsistent")
        return await self._repository.save_activity(activity)

    async def reprovision(
        self,
        organisation_id: str,
        session_id: str,
        revision: int,
        provider_connection_id: str,
        playbook_id: str,
        playbook_version: str,
        secret_store_connection_id: str,
        secret_resource: str,
        policy: BrowserPolicy,
        fencing_token: int,
        expires_at: datetime,
    ) -> BrowserSession:
        current = await self._repository.get(organisation_id, session_id)
        if current.revision != revision:
            raise ResourceConflictError(
                f"browser expected revision {revision}, found {current.revision}"
            )
        if current.status is not BrowserStatus.TERMINATED:
            raise ResourceConflictError("only a terminated browser can be reprovisioned")
        changed = self._change(
            current,
            status=BrowserStatus.PROVISIONING,
            provider_connection_id=provider_connection_id,
            playbook_id=playbook_id,
            playbook_version=playbook_version,
            secret_store_connection_id=secret_store_connection_id,
            secret_resource=secret_resource,
            policy=policy,
            fencing_token=fencing_token,
            worker_instance=None,
            internal_address=None,
            terminated_at=None,
            step_count=0,
            model_paused=True,
            recording_paused=True,
            takeover_subject=None,
            expires_at=expires_at,
        )
        return await self._repository.update(organisation_id, session_id, revision, changed)

    async def terminate(
        self,
        organisation_id: str,
        session_id: str,
        revision: int,
    ) -> BrowserSession:
        current = await self._current(organisation_id, session_id, revision)
        changed = self._change(
            current,
            status=BrowserStatus.TERMINATED,
            model_paused=True,
            recording_paused=True,
            terminated_at=self._clock(),
        )
        return await self._repository.update(organisation_id, session_id, revision, changed)

    async def _current(
        self, organisation_id: str, session_id: str, revision: int
    ) -> BrowserSession:
        current = await self._repository.get(organisation_id, session_id)
        if current.revision != revision:
            raise ResourceConflictError(
                f"browser expected revision {revision}, found {current.revision}"
            )
        if self._clock() >= current.expires_at:
            raise ResourceConflictError("browser session has expired")
        return current

    def _change(self, current: BrowserSession, **values: object) -> BrowserSession:
        return current.model_copy(
            update={
                **values,
                "updated_at": self._clock(),
                "revision": current.revision + 1,
            }
        )
