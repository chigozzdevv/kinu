from typing import Any, cast

from rotation.ports import Effects, Progress, ProgressEffects
from rotation.state import Snapshot


class Router:
    def __init__(self, hosting: Effects, aws: Effects) -> None:
        self.hosting, self.aws = hosting, aws

    def executor(self, state: Snapshot) -> Effects:
        return self.hosting if state.store.platform == "kinu" else self.aws

    async def initial(self, state: Snapshot) -> dict[str, Any]:
        return await self.executor(state).initial(state)

    async def initial_with_progress(self, state: Snapshot, progress: Progress) -> dict[str, Any]:
        return await cast(ProgressEffects, self.executor(state)).initial_with_progress(
            state, progress
        )

    async def issue(self, state: Snapshot, run_id: str) -> dict[str, Any]:
        return await self.executor(state).issue(state, run_id)

    async def access(self, state: Snapshot, created: dict[str, Any]) -> dict[str, Any]:
        return await self.executor(state).access(state, created)

    async def deploy(self, state: Snapshot, created: dict[str, Any], run_id: str) -> dict[str, Any]:
        return await self.executor(state).deploy(state, created, run_id)

    async def verify(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.executor(state).verify(state, created, deployment)

    async def promote(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.executor(state).promote(state, created, deployment)

    async def verify_current(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any]
    ) -> None:
        await self.executor(state).verify_current(state, created, deployment)

    async def observe(self, state: Snapshot, created: dict[str, Any]) -> dict[str, Any]:
        return await self.executor(state).observe(state, created)

    async def revoke(
        self,
        state: Snapshot,
        created: dict[str, Any],
        deployment: dict[str, Any],
        observed: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.executor(state).revoke(state, created, deployment, observed)

    async def rollback(
        self, state: Snapshot, created: dict[str, Any], deployment: dict[str, Any] | None
    ) -> bool:
        return await self.executor(state).rollback(state, created, deployment)

    async def cleanup_unrecorded(self, state: Snapshot, run_id: str) -> bool:
        return await self.executor(state).cleanup_unrecorded(state, run_id)
