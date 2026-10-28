import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from core.errors import ResourceNotFoundError, StorageUnavailableError


class Database:
    def __init__(self, url: str, name: str = "kinu") -> None:
        self.client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            url, tz_aware=True, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000
        )
        self.documents = self.client[name]

    def collection(self, name: str) -> AsyncCollection[dict[str, Any]]:
        return self.documents[name]

    async def open(self) -> None:
        try:
            hello = await self.client.admin.command("hello")
            if not hello.get("setName") and hello.get("msg") != "isdbgrid":
                raise StorageUnavailableError("MongoDB requires a replica set for transactions")
        except PyMongoError as error:
            raise StorageUnavailableError("application storage is unavailable") from error

    async def close(self) -> None:
        await self.client.close()

    @asynccontextmanager
    async def transaction(
        self, organisation_id: str | None = None
    ) -> AsyncIterator[AsyncClientSession]:
        async with self.client.start_session() as session:
            deadline = monotonic() + 10
            while True:
                await session.start_transaction(
                    read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority")
                )
                try:
                    if organisation_id is not None:
                        result = await self.collection("organisations").update_one(
                            {"id": organisation_id}, {"$inc": {"write_version": 1}}, session=session
                        )
                        if not result.matched_count:
                            raise ResourceNotFoundError("workspace is unavailable")
                    break
                except PyMongoError as error:
                    await session.abort_transaction()
                    if (
                        not error.has_error_label("TransientTransactionError")
                        or monotonic() >= deadline
                    ):
                        raise StorageUnavailableError(
                            "application transaction is unavailable"
                        ) from error
                    await asyncio.sleep(0.01)
            try:
                yield session
                while True:
                    try:
                        await session.commit_transaction()
                        break
                    except PyMongoError as error:
                        if (
                            not error.has_error_label("UnknownTransactionCommitResult")
                            or monotonic() >= deadline
                        ):
                            raise
            except BaseException:
                if session.in_transaction:
                    await session.abort_transaction()
                raise

    async def migrate(self) -> None:
        from core.storage.indexes import ensure_indexes

        await ensure_indexes(self)
