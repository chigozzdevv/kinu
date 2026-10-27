from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from psycopg import AsyncConnection, Error
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from core.errors import StorageUnavailableError
from core.storage.dataapi import DataApi
from core.storage.sql import SqlConnection


class Database:
    def __init__(self, url: str | None = None, *, data_api: DataApi | None = None) -> None:
        if (url is None) == (data_api is None):
            raise ValueError("configure one application database transport")
        self._data_api = data_api
        self._pool = (
            AsyncConnectionPool[AsyncConnection[DictRow]](
                url,
                # Keep statement names safe when a proxy multiplexes connections.
                kwargs={"row_factory": dict_row, "autocommit": True, "prepare_threshold": None},
                min_size=1,
                max_size=5,
                timeout=5,
                open=False,
            )
            if url is not None
            else None
        )

    async def open(self) -> None:
        if self._pool is not None:
            await self._pool.open(wait=True, timeout=5)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[SqlConnection]:
        try:
            if self._data_api:
                yield self._data_api.connection()
            else:
                assert self._pool is not None
                async with self._pool.connection() as connection:
                    yield connection
        except Error as error:
            raise StorageUnavailableError("application storage is unavailable") from error

    async def migrate(self) -> None:
        source = Path(__file__).with_name("schema.sql").read_text()
        async with self.connection() as connection:
            if self._data_api:
                import sqlparse

                async with connection.transaction():
                    for statement in sqlparse.split(source):
                        await connection.execute(statement)
            else:
                await connection.execute(source, prepare=False)
