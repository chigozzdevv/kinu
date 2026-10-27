from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from psycopg import AsyncConnection, Error
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from core.errors import StorageUnavailableError


class Database:
    def __init__(self, url: str) -> None:
        self._pool = AsyncConnectionPool[AsyncConnection[DictRow]](
            url,
            # Keep statement names safe when a proxy multiplexes connections.
            kwargs={"row_factory": dict_row, "autocommit": True, "prepare_threshold": None},
            min_size=1,
            max_size=5,
            timeout=5,
            open=False,
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=5)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection[DictRow]]:
        try:
            async with self._pool.connection() as connection:
                yield connection
        except Error as error:
            raise StorageUnavailableError("application storage is unavailable") from error

    async def migrate(self) -> None:
        async with self.connection() as connection:
            await connection.execute(
                Path(__file__).with_name("schema.sql").read_text(), prepare=False
            )
