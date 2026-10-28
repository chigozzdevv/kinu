from typing import Protocol


class RunDispatcher(Protocol):
    async def require_scope(self, organisation_id: str, credential_id: str) -> None: ...

    async def submit(self, organisation_id: str, run_id: str) -> None: ...
