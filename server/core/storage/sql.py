from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from psycopg.rows import DictRow


class SqlCursor(Protocol):
    async def fetchone(self) -> DictRow | None: ...

    async def fetchall(self) -> list[DictRow]: ...


class SqlConnection(Protocol):
    async def execute(
        self,
        query: str,
        parameters: Sequence[object] | None = None,
        *,
        prepare: bool | None = None,
    ) -> SqlCursor: ...

    def transaction(self) -> AbstractAsyncContextManager[object]: ...
