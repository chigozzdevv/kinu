import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime
from typing import Any

from connectors.aws import AwsApi
from connectors.aws.client import AwsError
from psycopg import errors
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from core.errors import StorageUnavailableError


class DataApi:
    def __init__(
        self,
        aws: AwsApi,
        region: str,
        cluster: str,
        secret: str,
        database: str,
    ) -> None:
        self.aws = aws
        self.region = region
        self.parameters = {"resourceArn": cluster, "secretArn": secret, "database": database}

    def connection(self) -> "DataConnection":
        return DataConnection(self)


class DataConnection:
    def __init__(self, api: DataApi) -> None:
        self._api = api
        self._transaction: str | None = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        if self._transaction is not None:
            raise RuntimeError("nested Data API transactions are not supported")
        started = await self._call("begin_transaction")
        self._transaction = str(started["transactionId"])
        try:
            yield
            await self._call("commit_transaction", transactionId=self._transaction)
        except BaseException:
            with suppress(Exception):
                await self._call("rollback_transaction", transactionId=self._transaction)
            raise
        finally:
            self._transaction = None

    async def execute(
        self,
        query: str,
        parameters: Sequence[object] | None = None,
        *,
        prepare: bool | None = None,
    ) -> "DataCursor":
        arguments: list[dict[str, Any]] = []
        pieces = query.split("%s")
        if len(pieces) != len(parameters or ()) + 1:
            raise ValueError("SQL parameter count does not match its placeholders")
        sql = pieces[0]
        for index, value in enumerate(parameters or ()):
            item, cast = _parameter(f"p{index}", value)
            arguments.append(item)
            sql += f"CAST(:p{index} AS {cast})" if cast else f":p{index}"
            sql += pieces[index + 1]
        result = await self._call(
            "execute_statement",
            sql=sql,
            parameters=arguments,
            includeResultMetadata=True,
            resultSetOptions={"decimalReturnType": "STRING", "longReturnType": "LONG"},
            **({"transactionId": self._transaction} if self._transaction else {}),
        )
        columns = result.get("columnMetadata", [])
        return DataCursor(
            [
                {
                    column["name"]: _decode(field, column["typeName"])
                    for column, field in zip(columns, record, strict=True)
                }
                for record in result.get("records", [])
            ]
        )

    async def _call(self, operation: str, **parameters: Any) -> dict[str, Any]:
        request: dict[str, Any] = dict(self._api.parameters)
        if operation in {"commit_transaction", "rollback_transaction"}:
            request.pop("database")
        try:
            return await self._api.aws.call(
                "rds-data",
                operation,
                self._api.region,
                **request,
                **parameters,
            )
        except AwsError as error:
            if error.sqlstate:
                raise errors.lookup(error.sqlstate)("application storage query failed") from None
            raise StorageUnavailableError(
                "application storage is temporarily unavailable"
            ) from None


class DataCursor:
    def __init__(self, rows: list[DictRow]) -> None:
        self._rows = rows
        self._position = 0

    async def fetchone(self) -> DictRow | None:
        if self._position == len(self._rows):
            return None
        result = self._rows[self._position]
        self._position += 1
        return result

    async def fetchall(self) -> list[DictRow]:
        result = self._rows[self._position :]
        self._position = len(self._rows)
        return result


def _parameter(name: str, value: object) -> tuple[dict[str, Any], str | None]:
    cast: str | None = None
    encoded: dict[str, Any]
    if isinstance(value, Jsonb):
        encoded = {"stringValue": json.dumps(value.obj, separators=(",", ":"))}
        cast = "jsonb"
    elif isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("SQL timestamps require a timezone")
        encoded = {"stringValue": value.astimezone(UTC).isoformat()}
        cast = "timestamptz"
    elif isinstance(value, date):
        encoded = {"stringValue": value.isoformat()}
        cast = "date"
    elif value is None:
        encoded = {"isNull": True}
    elif isinstance(value, bool):
        encoded = {"booleanValue": value}
    elif isinstance(value, int):
        encoded = {"longValue": value}
    elif isinstance(value, str):
        encoded = {"stringValue": value}
    else:
        raise TypeError(f"unsupported SQL parameter type: {type(value).__name__}")
    return {"name": name, "value": encoded}, cast


def _decode(field: dict[str, Any], kind: str) -> Any:
    if field.get("isNull"):
        return None
    value = next(iter(field.values()))
    if kind in {"json", "jsonb"}:
        return json.loads(value)
    if kind in {"timestamp", "timestamptz"}:
        stamp = datetime.fromisoformat(value)
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
    if kind == "date":
        return date.fromisoformat(value)
    return value
