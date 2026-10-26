import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Protocol, TypeVar

from broker.capability import CapabilityClaims, CapabilitySigner, request_digest
from contracts import (
    BrowserAccessGrant,
    BrowserAccessMode,
    BrowserSession,
    BrowserStatus,
    Contract,
    RotationRun,
    RunStatus,
)
from core.errors import ResourceConflictError
from core.ids import new_id
from core.storage.paths import StoragePaths

from browser.secret import SecretAccessInstaller
from browser.service import BrowserService

SignerLoader = Callable[[], Awaitable[CapabilitySigner]]
T = TypeVar("T", bound=Contract)


class BrowserCatalog(Protocol):
    async def get(self, path: str, model: type[T]) -> T: ...


class BrowserAccessService:
    def __init__(
        self,
        catalog: BrowserCatalog,
        sessions: BrowserService,
        signer_loader: SignerLoader,
        gateway_url: str,
        clock: Callable[[], datetime],
        secret_access: SecretAccessInstaller | None = None,
    ) -> None:
        self._catalog = catalog
        self._sessions = sessions
        self._signer_loader = signer_loader
        self._gateway_url = gateway_url.rstrip("/")
        self._clock = clock
        self._signer: CapabilitySigner | None = None
        self._lock = asyncio.Lock()
        self._secret_access = secret_access

    async def issue(
        self,
        organisation_id: str,
        session_id: str,
        mode: BrowserAccessMode,
        subject: str,
    ) -> BrowserAccessGrant:
        session = await self._catalog.get(
            StoragePaths.browser(organisation_id, session_id), BrowserSession
        )
        run = await self._catalog.get(
            StoragePaths.run(organisation_id, session.run_id), RotationRun
        )
        now = self._clock()
        if session.expires_at <= now or session.status is BrowserStatus.TERMINATED:
            raise ResourceConflictError("browser session is no longer available")
        if run.status not in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.RECOVERING}:
            raise ResourceConflictError("browser access requires an active or paused run")
        if mode is BrowserAccessMode.TAKEOVER:
            if session.takeover_subject not in {None, subject}:
                raise ResourceConflictError("browser takeover belongs to another operator")
            if session.status is not BrowserStatus.TAKEOVER:
                session = await self._sessions.takeover(
                    organisation_id, session.id, session.revision, subject
                )
            if self._secret_access is None:
                raise ResourceConflictError("browser secure capture authorization is unavailable")
            await self._secret_access.install(run, session)
        expires_at = min(session.expires_at, now + timedelta(minutes=5))
        tool = f"browser.{mode.value}"
        payload = {"session_id": session.id, "subject": subject}
        signer = await self._get_signer()
        capability = signer.mint(
            CapabilityClaims(
                organisation_id=organisation_id,
                run_id=run.id,
                agent_id=_actor_id(subject),
                tool=tool,
                connection_id=session.id,
                stage=run.stage,
                fencing_token=session.fencing_token,
                request_digest=request_digest(tool, payload),
                action_digest=request_digest(tool, payload),
                expires_at=int(expires_at.timestamp()),
                nonce=new_id("browsercap"),
            )
        )
        return BrowserAccessGrant(
            organisation_id=organisation_id,
            session_id=session.id,
            mode=mode,
            gateway_url=self._gateway_url,
            capability=capability,
            expires_at=expires_at,
            session=session,
        )

    async def release(
        self,
        organisation_id: str,
        session_id: str,
        subject: str,
    ) -> BrowserSession:
        session = await self._catalog.get(
            StoragePaths.browser(organisation_id, session_id), BrowserSession
        )
        return await self._sessions.release_takeover(
            organisation_id, session.id, session.revision, subject
        )

    async def _get_signer(self) -> CapabilitySigner:
        if self._signer is not None:
            return self._signer
        async with self._lock:
            if self._signer is None:
                self._signer = await self._signer_loader()
            return self._signer


def _actor_id(subject: str) -> str:
    from core.auth import AuthenticatedIdentity

    return AuthenticatedIdentity(subject=subject, issuer="browser-access").actor_id
