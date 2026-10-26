from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from connectors import ConnectorContext
from contracts import (
    Connection,
    ProbeDefinition,
    ProbeResult,
)


class ProbeExecutor(Protocol):
    async def execute(
        self,
        definition: ProbeDefinition,
        connection: Connection,
        context: ConnectorContext,
        clock: Callable[[], datetime],
        secret_connection: Connection | None = None,
    ) -> ProbeResult: ...
