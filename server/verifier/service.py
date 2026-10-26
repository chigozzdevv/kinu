from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from connectors import ConnectorContext
from contracts import (
    Connection,
    ProbeDefinition,
    ProbeResult,
    VerificationReport,
    VerificationStatus,
)
from core.errors import ResourceConflictError

from verifier.probes import ProbeExecutor


class VerificationRepository(Protocol):
    async def connection(self, organisation_id: str, connection_id: str) -> Connection: ...

    async def save_report(self, report: VerificationReport) -> VerificationReport: ...


class VerificationService:
    def __init__(
        self,
        repository: VerificationRepository,
        executor: ProbeExecutor,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._clock = clock

    async def verify(
        self,
        report_id: str,
        organisation_id: str,
        run_id: str,
        generation_id: str,
        definitions: tuple[ProbeDefinition, ...],
        context: ConnectorContext,
    ) -> VerificationReport:
        if not definitions:
            raise ResourceConflictError("verification requires at least one deterministic probe")
        if context.run.id != run_id or context.run.organisation_id != organisation_id:
            raise ResourceConflictError("verification context belongs to another run")
        if any(definition.organisation_id != organisation_id for definition in definitions):
            raise ResourceConflictError("verification probes cross organisation boundaries")
        started_at = self._clock()
        results: list[ProbeResult] = []
        for definition in definitions:
            connection = await self._repository.connection(
                organisation_id, definition.connection_id
            )
            secret_connection = (
                await self._repository.connection(organisation_id, definition.secret_connection_id)
                if definition.secret_connection_id is not None
                else None
            )
            results.append(
                await self._executor.execute(
                    definition, connection, context, self._clock, secret_connection
                )
            )
        statuses = {result.status for result in results}
        status = (
            VerificationStatus.PASSED
            if statuses == {VerificationStatus.PASSED}
            else VerificationStatus.FAILED
        )
        evidence_ids = tuple(
            evidence_id for result in results for evidence_id in result.evidence_ids
        )
        report = VerificationReport(
            id=report_id,
            organisation_id=organisation_id,
            run_id=run_id,
            generation_id=generation_id,
            status=status,
            results=tuple(results),
            checks=frozenset(check for result in results for check in result.checks),
            evidence_ids=evidence_ids,
            started_at=started_at,
            completed_at=self._clock(),
        )
        return await self._repository.save_report(report)
