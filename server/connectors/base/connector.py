from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from contracts import Connection, RotationRun

from connectors.base.result import ConnectorResponse


@dataclass(frozen=True, slots=True)
class ConnectorContext:
    request_id: str
    agent_id: str
    connection: Connection
    run: RotationRun
    now: datetime
    idempotency_key: str


class Connector(Protocol):
    @property
    def tools(self) -> frozenset[str]: ...

    async def execute(
        self,
        tool: str,
        payload: dict[str, Any],
        context: ConnectorContext,
    ) -> ConnectorResponse: ...


@runtime_checkable
class ReconcilesMutations(Protocol):
    async def prepare(
        self,
        tool: str,
        payload: dict[str, Any],
        context: ConnectorContext,
    ) -> dict[str, str | int | bool | tuple[str, ...]]: ...

    async def reconcile(
        self,
        tool: str,
        payload: dict[str, Any],
        state: dict[str, str | int | bool | tuple[str, ...]],
        context: ConnectorContext,
    ) -> ConnectorResponse | None: ...
